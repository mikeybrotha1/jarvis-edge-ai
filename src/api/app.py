"""FastAPI application factory for the entity query and activity APIs.

The API is intentionally independent of camera / Hailo hardware. It only
requires a database connection and the entity-memory repositories.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.activity_ws import router as activity_ws_router
from api.entity_routes import router as entity_router
from api.schemas import HealthOut
from api.timeline_routes import entity_timeline_router, router as timeline_router
from api.zone_routes import entity_zones_router, router as zone_router
from services.activity_listener import ActivityNotificationListener
from services.activity_stream import ActivityStreamBroker
from services.entity_query_service import EntityQueryService, QueryLimits
from services.timeline_service import TimelineLimits, TimelineService
from services.zone_query_service import ZoneQueryLimits, ZoneQueryService
from storage.entity_repository import EntityRepository
from storage.entity_zone_session_repository import EntityZoneSessionRepository
from storage.observation_repository import ObservationRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)
from storage.timeline_repository import TimelineRepository
from storage.zone_repository import ZoneRepository

logger = logging.getLogger(__name__)


def create_app(
    *,
    query_service: EntityQueryService | None = None,
    timeline_service: TimelineService | None = None,
    zone_query_service: ZoneQueryService | None = None,
    session_factory: sessionmaker[Session] | None = None,
    database_url: str | None = None,
    limits: QueryLimits | None = None,
    timeline_limits: TimelineLimits | None = None,
    zone_limits: ZoneQueryLimits | None = None,
    activity_stream_config: Any | None = None,
    activity_broker: ActivityStreamBroker | None = None,
    activity_listener: ActivityNotificationListener | None = None,
    enable_activity_stream: bool | None = None,
    create_schema: bool = False,
    title: str = "Jarvis Edge AI Entity Query API",
) -> FastAPI:
    """Build a FastAPI app with injectable query and activity-stream deps."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        service = getattr(app.state, "query_service", None)
        if service is None:
            raise RuntimeError("EntityQueryService was not initialised.")
        if getattr(app.state, "timeline_service", None) is None:
            raise RuntimeError("TimelineService was not initialised.")

        listener: ActivityNotificationListener | None = getattr(
            app.state,
            "activity_listener",
            None,
        )
        broker: ActivityStreamBroker | None = getattr(
            app.state,
            "activity_broker",
            None,
        )
        stream_enabled = bool(
            getattr(app.state, "activity_stream_enabled", False)
        )
        app.state.activity_stream_ready = False

        if stream_enabled and listener is not None:
            try:
                await listener.start()
                ready = await listener.wait_until_ready(timeout=10.0)
                app.state.activity_stream_ready = ready
                if ready:
                    logger.info("Activity stream LISTEN ready")
                else:
                    logger.warning(
                        "Activity stream listener did not become ready in time"
                    )
            except Exception:
                logger.exception("Failed to start activity stream listener")
                app.state.activity_stream_ready = False
        elif stream_enabled and broker is not None:
            # Test/SQLite mode: broker-only fan-out without LISTEN.
            app.state.activity_stream_ready = True
            logger.info("Activity stream broker ready (no LISTEN connection)")
        else:
            logger.info(
                "Entity query API ready (activity_stream_enabled=%s)",
                stream_enabled,
            )

        yield

        if broker is not None:
            await broker.close_all(code=1001, reason="server shutdown")

        if listener is not None:
            await listener.stop()

        engine = getattr(app.state, "engine", None)
        if engine is not None:
            engine.dispose()
            logger.info("Entity query API engine disposed")

    app = FastAPI(
        title=title,
        version="0.7.0",
        lifespan=lifespan,
    )

    resolved_service = query_service
    resolved_timeline = timeline_service
    resolved_zones = zone_query_service
    engine = None
    factory = session_factory
    resolved_db_url = database_url

    need_repositories = (
        resolved_service is None
        or resolved_timeline is None
        or resolved_zones is None
    )

    if need_repositories:
        if factory is None:
            if database_url and str(database_url).strip():
                engine = create_entity_engine(database_url)
                if create_schema:
                    create_entity_schema(engine)
                factory = create_session_factory(engine)
            elif (
                resolved_service is not None
                and resolved_timeline is not None
                and resolved_zones is None
            ):
                # Tests inject query/timeline without a factory: provide an
                # isolated in-memory zone stack so zone routes remain usable.
                engine = create_entity_engine("sqlite+pysqlite:///:memory:")
                create_entity_schema(engine)
                factory = create_session_factory(engine)
            else:
                raise ValueError(
                    "database_url is required when query_service / "
                    "timeline_service and session_factory are not provided."
                )

        entity_repository = EntityRepository(factory)
        observation_repository = ObservationRepository(factory)
        zone_repository = ZoneRepository(factory)
        session_repository = EntityZoneSessionRepository(factory)

        if resolved_service is None:
            resolved_service = EntityQueryService(
                entity_repository,
                observation_repository,
                limits=limits,
                logger=logger,
            )

        if resolved_timeline is None:
            # v0.7.0: TimelineRepository is a facade over provider composer.
            resolved_timeline = TimelineService(
                TimelineRepository(factory),
                entity_repository,
                limits=timeline_limits,
                logger=logger,
            )

        if resolved_zones is None:
            resolved_zones = ZoneQueryService(
                zone_repository,
                session_repository,
                entity_repository,
                limits=zone_limits,
                logger=logger,
            )

        app.state.session_factory = factory
        app.state.engine = engine
    else:
        app.state.session_factory = session_factory
        app.state.engine = None

    app.state.query_service = resolved_service
    app.state.timeline_service = resolved_timeline
    app.state.zone_query_service = resolved_zones
    app.state.limits = limits or QueryLimits()
    app.state.timeline_limits = timeline_limits or TimelineLimits()
    app.state.zone_limits = zone_limits or ZoneQueryLimits()

    # Activity stream wiring (optional; REST works without it).
    stream_cfg = activity_stream_config
    stream_enabled = (
        bool(stream_cfg.enabled)
        if stream_cfg is not None
        else bool(enable_activity_stream)
    )
    app.state.activity_stream_config = stream_cfg
    app.state.activity_stream_enabled = stream_enabled

    broker = activity_broker
    if stream_enabled and broker is None:
        queue_size = (
            int(stream_cfg.client_queue_size) if stream_cfg else 100
        )
        max_conn = int(stream_cfg.max_connections) if stream_cfg else 25
        broker = ActivityStreamBroker(
            client_queue_size=queue_size,
            max_connections=max_conn,
            logger=logger,
        )
    app.state.activity_broker = broker

    listener = activity_listener
    if (
        stream_enabled
        and listener is None
        and broker is not None
        and resolved_db_url
        and not str(resolved_db_url).startswith("sqlite")
    ):
        channel = (
            stream_cfg.notify_channel if stream_cfg else "jarvis_activity"
        )
        listener = ActivityNotificationListener(
            database_url=resolved_db_url,
            channel=channel,
            timeline_service=resolved_timeline,
            broker=broker,
            reconnect_initial_seconds=(
                float(stream_cfg.reconnect_initial_seconds)
                if stream_cfg
                else 1.0
            ),
            reconnect_max_seconds=(
                float(stream_cfg.reconnect_max_seconds)
                if stream_cfg
                else 30.0
            ),
            logger=logger,
        )
    app.state.activity_listener = listener
    app.state.activity_stream_ready = False

    app.include_router(entity_router)
    app.include_router(entity_timeline_router)
    app.include_router(entity_zones_router)
    app.include_router(timeline_router)
    app.include_router(zone_router)
    app.include_router(activity_ws_router)

    _mount_live_activity_console(app)

    @app.get("/health", response_model=HealthOut, tags=["system"])
    def health() -> HealthOut:
        return HealthOut()

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": _safe_validation_detail(exc.errors()),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.exception("Unhandled API error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error."},
        )

    return app


def _safe_validation_detail(errors: list[Any]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in errors:
        cleaned.append(
            {
                "loc": item.get("loc"),
                "msg": item.get("msg"),
                "type": item.get("type"),
            }
        )
    return cleaned


def _console_directory() -> Path:
    """Repository-root ``console/`` directory (sibling of ``src/``)."""

    return Path(__file__).resolve().parents[2] / "console"


def _mount_live_activity_console(app: FastAPI) -> None:
    """Serve the static Live Activity Console at ``/console``.

    Registered after API routers so ``/api/v1/*``, ``/ws/v1/*``, ``/health``,
    and OpenAPI routes remain authoritative.
    """

    console_dir = _console_directory()
    index = console_dir / "index.html"
    if not console_dir.is_dir() or not index.is_file():
        logger.warning(
            "Live Activity Console assets not found at %s",
            console_dir,
        )
        return

    @app.get("/console", include_in_schema=False)
    @app.get("/console/", include_in_schema=False)
    async def live_activity_console_index() -> FileResponse:
        return FileResponse(index, media_type="text/html; charset=utf-8")

    # Assets: /console/css/*, /console/js/*
    app.mount(
        "/console",
        StaticFiles(directory=str(console_dir), html=False),
        name="live_activity_console",
    )
    logger.info("Live Activity Console mounted at /console (%s)", console_dir)


def build_app_from_loaded_config(
    app_config: Any,
    *,
    create_schema: bool = False,
) -> FastAPI:
    """Build the API app from an already-loaded ``AppConfig``.

    Used by both ``python -m api`` and ``create_app_from_config`` so CLI and
    uvicorn factory paths wire activity_stream identically.
    """

    limits = QueryLimits(
        entity_default_limit=app_config.api.default_limit,
        entity_maximum_limit=app_config.api.maximum_limit,
    )
    timeline_limits = TimelineLimits(
        default_limit=app_config.timeline.default_limit,
        maximum_limit=app_config.timeline.maximum_limit,
    )
    zone_limits = ZoneQueryLimits(
        default_limit=app_config.api.default_limit,
        maximum_limit=app_config.api.maximum_limit,
        maximum_zones_per_camera=app_config.spatial.maximum_zones_per_camera,
    )
    return create_app(
        database_url=app_config.database.url,
        limits=limits,
        timeline_limits=timeline_limits,
        zone_limits=zone_limits,
        activity_stream_config=app_config.activity_stream,
        create_schema=create_schema,
    )


def create_app_from_config() -> FastAPI:
    """Factory used by uvicorn: ``uvicorn api.app:create_app_from_config``."""

    from config import load_app_config

    return build_app_from_loaded_config(load_app_config())
