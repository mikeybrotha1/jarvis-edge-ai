"""FastAPI application factory for the entity query API.

The API is intentionally independent of camera / Hailo hardware. It only
requires a database connection and the entity-memory repositories.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.entity_routes import router as entity_router
from api.schemas import HealthOut
from services.entity_query_service import EntityQueryService, QueryLimits
from storage.entity_repository import EntityRepository
from storage.observation_repository import ObservationRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)

logger = logging.getLogger(__name__)


def create_app(
    *,
    query_service: EntityQueryService | None = None,
    session_factory: sessionmaker[Session] | None = None,
    database_url: str | None = None,
    limits: QueryLimits | None = None,
    create_schema: bool = False,
    title: str = "Jarvis Edge AI Entity Query API",
) -> FastAPI:
    """Build a FastAPI app with injectable query dependencies.

    Parameters
    ----------
    query_service:
        Optional pre-built service (tests inject this).
    session_factory:
        Optional SQLAlchemy session factory. Used when ``query_service`` is
        omitted.
    database_url:
        Optional PostgreSQL / SQLite URL used to build engine + session
        factory when neither service nor factory is provided.
    limits:
        Pagination defaults and maxima for the query service.
    create_schema:
        When True, create entity-memory tables on startup (test convenience).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        service = getattr(app.state, "query_service", None)
        if service is None:
            raise RuntimeError("EntityQueryService was not initialised.")
        logger.info("Entity query API ready")
        yield
        engine = getattr(app.state, "engine", None)
        if engine is not None:
            engine.dispose()
            logger.info("Entity query API engine disposed")

    app = FastAPI(
        title=title,
        version="0.4.1",
        lifespan=lifespan,
    )

    resolved_service = query_service
    engine = None

    if resolved_service is None:
        factory = session_factory
        if factory is None:
            if not database_url or not database_url.strip():
                raise ValueError(
                    "database_url is required when query_service and "
                    "session_factory are not provided."
                )
            engine = create_entity_engine(database_url)
            if create_schema:
                create_entity_schema(engine)
            factory = create_session_factory(engine)

        resolved_service = EntityQueryService(
            EntityRepository(factory),
            ObservationRepository(factory),
            limits=limits,
            logger=logger,
        )
        app.state.session_factory = factory
        app.state.engine = engine
    else:
        app.state.session_factory = session_factory
        app.state.engine = None

    app.state.query_service = resolved_service
    app.state.limits = limits or QueryLimits()

    app.include_router(entity_router)

    @app.get("/health", response_model=HealthOut, tags=["system"])
    def health() -> HealthOut:
        return HealthOut()

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        # Avoid leaking raw request body internals beyond field errors.
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


def create_app_from_config() -> FastAPI:
    """Factory used by uvicorn: ``uvicorn api.app:create_app_from_config``."""

    from config import load_app_config

    app_config = load_app_config()
    limits = QueryLimits(
        entity_default_limit=app_config.api.default_limit,
        entity_maximum_limit=app_config.api.maximum_limit,
    )
    return create_app(
        database_url=app_config.database.url,
        limits=limits,
        create_schema=False,
    )
