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
from api.alert_routes import alerts_router, rules_router
from api.entity_routes import router as entity_router
from api.notification_routes import (
    alert_deliveries_router,
    deliveries_router,
    rule_targets_router,
    targets_router,
)
from api.ops_routes import router as ops_router
from api.schemas import HealthOut
from api.timeline_routes import entity_timeline_router, router as timeline_router
from api.zone_routes import entity_zones_router, router as zone_router
from services.ops.metrics import OpsMetricsRegistry
from services.ops.retention_control import RetentionControlService
from services.ops.retention_worker import RetentionWorker
from services.ops.status import OpsStatusCollector
from storage.retention_repository import RetentionRepository
from services.activity_listener import ActivityNotificationListener
from services.activity_stream import ActivityStreamBroker
from services.alerts.consumer import AlertCommittedEventConsumer
from services.alerts.due_reconciler import AlertDueReconciler
from services.alerts.evaluation_service import AlertEvaluationService
from services.alerts.rule_service import AlertQueryService, AlertRuleService
from services.entity_query_service import EntityQueryService, QueryLimits
from services.notifications.delivery_service import NotificationDeliveryQueryService
from services.notifications.enqueue import NotificationEnqueueService
from services.notifications.registry import NotificationProviderRegistry
from services.notifications.target_service import NotificationTargetService
from services.notifications.webhook_provider import WebhookNotificationProvider
from services.notifications.worker import NotificationDeliveryWorker
from services.timeline_service import TimelineLimits, TimelineService
from services.zone_query_service import ZoneQueryLimits, ZoneQueryService
from storage.activity_notify import ActivityNotificationPublisher
from storage.alert_repositories import (
    AlertCheckpointRepository,
    AlertEvaluatorStateRepository,
    AlertRepository,
    AlertRuleRepository,
)
from storage.entity_repository import EntityRepository
from storage.entity_zone_session_repository import EntityZoneSessionRepository
from storage.notification_repositories import (
    NotificationDeliveryRepository,
    NotificationTargetRepository,
    RuleNotificationTargetRepository,
)
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
    alerts_config: Any | None = None,
    notifications_config: Any | None = None,
    ops_config: Any | None = None,
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

        alert_consumer = getattr(app.state, "alert_consumer", None)
        alert_reconciler = getattr(app.state, "alert_reconciler", None)
        notification_worker = getattr(app.state, "notification_worker", None)
        retention_worker = getattr(app.state, "retention_worker", None)
        if alert_consumer is not None:
            try:
                await alert_consumer.start()
                await alert_consumer.wait_until_ready(timeout=30.0)
            except Exception:
                logger.exception("Failed to start alert consumer")
        if alert_reconciler is not None:
            try:
                await alert_reconciler.start()
            except Exception:
                logger.exception("Failed to start alert due reconciler")
        if notification_worker is not None:
            try:
                await notification_worker.start()
                await notification_worker.wait_until_ready(timeout=10.0)
            except Exception:
                logger.exception("Failed to start notification delivery worker")
        if retention_worker is not None:
            try:
                await retention_worker.start()
            except Exception:
                logger.exception("Failed to start retention worker")

        yield

        if retention_worker is not None:
            await retention_worker.stop()
        if notification_worker is not None:
            await notification_worker.stop()
        if alert_reconciler is not None:
            await alert_reconciler.stop()
        if alert_consumer is not None:
            await alert_consumer.stop()

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
        version="0.10.0",
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

    # Alert subsystem (separate from core vision transactions).
    alerts_cfg = alerts_config
    notif_cfg = notifications_config
    factory_for_alerts = getattr(app.state, "session_factory", None)
    alert_rule_service = None
    alert_query_service = None
    alert_consumer = None
    alert_reconciler = None
    notification_target_service = None
    notification_delivery_service = None
    notification_worker = None
    if factory_for_alerts is not None:
        rule_repo = AlertRuleRepository(factory_for_alerts)
        alert_repo = AlertRepository(factory_for_alerts)
        state_repo = AlertEvaluatorStateRepository(factory_for_alerts)
        checkpoint_repo = AlertCheckpointRepository(factory_for_alerts)
        session_repo = EntityZoneSessionRepository(factory_for_alerts)
        max_rules = int(getattr(alerts_cfg, "max_rules", 100)) if alerts_cfg else 100
        max_meta = (
            int(getattr(alerts_cfg, "max_metadata_bytes", 8192))
            if alerts_cfg
            else 8192
        )
        default_cd = (
            int(getattr(alerts_cfg, "default_cooldown_seconds", 60))
            if alerts_cfg
            else 60
        )
        alert_rule_service = AlertRuleService(
            rule_repo,
            max_rules=max_rules,
            max_metadata_bytes=max_meta,
            default_cooldown=default_cd,
            logger=logger,
        )
        alert_publisher = ActivityNotificationPublisher(
            channel=(
                stream_cfg.notify_channel
                if stream_cfg is not None
                else "jarvis_activity"
            ),
            logger=logger,
        )
        # Notification outbox + webhook worker (v0.9.0).
        # allow_private_targets must flow from NotificationsConfig into both
        # target CRUD SSRF validation and delivery-time WebhookNotificationProvider.
        target_repo = NotificationTargetRepository(factory_for_alerts)
        assoc_repo = RuleNotificationTargetRepository(factory_for_alerts)
        delivery_repo = NotificationDeliveryRepository(factory_for_alerts)
        allow_private = _notifications_allow_private(notif_cfg)
        notification_target_service = NotificationTargetService(
            target_repo,
            assoc_repo,
            allow_private_targets=allow_private,
            max_metadata_bytes=max_meta,
            logger=logger,
        )
        notification_delivery_service = NotificationDeliveryQueryService(
            delivery_repo, logger=logger
        )
        enqueue = NotificationEnqueueService(
            target_repo, delivery_repo, logger=logger
        )
        alert_query_service = AlertQueryService(
            alert_repo,
            session_factory=factory_for_alerts,
            activity_publisher=alert_publisher,
            notification_enqueue=enqueue,
            logger=logger,
        )
        provider_registry = NotificationProviderRegistry()
        provider_registry.register(
            WebhookNotificationProvider(
                request_timeout_seconds=float(
                    getattr(notif_cfg, "request_timeout_seconds", 5.0)
                    if notif_cfg is not None
                    else 5.0
                ),
                max_request_bytes=int(
                    getattr(notif_cfg, "max_request_bytes", 65536)
                    if notif_cfg is not None
                    else 65536
                ),
                max_response_bytes=int(
                    getattr(notif_cfg, "max_response_bytes", 8192)
                    if notif_cfg is not None
                    else 8192
                ),
                allow_private_targets=allow_private,
                logger=logger,
            )
        )
        notif_enabled = (
            bool(notif_cfg.enabled) if notif_cfg is not None else True
        )
        notification_worker = NotificationDeliveryWorker(
            delivery_repo,
            target_repo,
            provider_registry,
            enabled=notif_enabled,
            worker_id=str(
                getattr(notif_cfg, "worker_id", "jarvis-notification-worker")
                if notif_cfg is not None
                else "jarvis-notification-worker"
            ),
            poll_interval_seconds=float(
                getattr(notif_cfg, "worker_poll_interval_seconds", 1.0)
                if notif_cfg is not None
                else 1.0
            ),
            max_attempts=int(
                getattr(notif_cfg, "max_attempts", 5)
                if notif_cfg is not None
                else 5
            ),
            initial_backoff_seconds=float(
                getattr(notif_cfg, "initial_backoff_seconds", 30.0)
                if notif_cfg is not None
                else 30.0
            ),
            max_backoff_seconds=float(
                getattr(notif_cfg, "max_backoff_seconds", 1800.0)
                if notif_cfg is not None
                else 1800.0
            ),
            backoff_multiplier=float(
                getattr(notif_cfg, "backoff_multiplier", 2.0)
                if notif_cfg is not None
                else 2.0
            ),
            batch_size=int(
                getattr(notif_cfg, "batch_size", 50)
                if notif_cfg is not None
                else 50
            ),
            max_concurrent_deliveries=int(
                getattr(notif_cfg, "max_concurrent_deliveries", 3)
                if notif_cfg is not None
                else 3
            ),
            lock_timeout_seconds=float(
                getattr(notif_cfg, "lock_timeout_seconds", 60.0)
                if notif_cfg is not None
                else 60.0
            ),
            logger=logger,
        )

        evaluation = AlertEvaluationService(
            factory_for_alerts,
            rule_repo,
            alert_repo,
            state_repo,
            activity_publisher=alert_publisher,
            session_repository=session_repo,
            notification_enqueue=enqueue,
            logger=logger,
        )
        alerts_enabled = (
            bool(alerts_cfg.enabled) if alerts_cfg is not None else True
        )
        alert_consumer = AlertCommittedEventConsumer(
            evaluation_service=evaluation,
            timeline_service=resolved_timeline,
            checkpoint_repository=checkpoint_repo,
            consumer_name=(
                str(alerts_cfg.consumer_name)
                if alerts_cfg is not None
                else "jarvis-alert-evaluator"
            ),
            queue_size=(
                int(alerts_cfg.queue_size) if alerts_cfg is not None else 500
            ),
            replay_overlap_seconds=(
                float(alerts_cfg.replay_overlap_seconds)
                if alerts_cfg is not None
                else 5.0
            ),
            startup_catchup_limit=(
                int(alerts_cfg.startup_catchup_limit)
                if alerts_cfg is not None
                else 500
            ),
            enabled=alerts_enabled,
            logger=logger,
        )
        alert_reconciler = AlertDueReconciler(
            evaluation,
            interval_seconds=(
                float(alerts_cfg.reconcile_interval_seconds)
                if alerts_cfg is not None
                else 2.0
            ),
            batch_size=(
                int(alerts_cfg.reconcile_batch_size)
                if alerts_cfg is not None
                else 100
            ),
            enabled=alerts_enabled,
            logger=logger,
        )
        if listener is not None:
            listener.add_event_handler(alert_consumer.submit)

    app.state.alert_rule_service = alert_rule_service
    app.state.alert_query_service = alert_query_service
    app.state.alert_consumer = alert_consumer
    app.state.alert_reconciler = alert_reconciler
    app.state.alerts_config = alerts_cfg
    app.state.notification_target_service = notification_target_service
    app.state.notification_delivery_service = notification_delivery_service
    app.state.notification_worker = notification_worker
    app.state.notifications_config = notif_cfg
    app.state.notifications_allow_private_targets = (
        _notifications_allow_private(notif_cfg)
    )

    # Ops / retention (v0.10.0): policy always loaded; worker only if enabled.
    resolved_ops = ops_config
    if resolved_ops is None:
        from config.models import OpsConfig as _OpsConfig

        resolved_ops = _OpsConfig()
    app.state.ops_config = resolved_ops
    retention_cfg = getattr(resolved_ops, "retention", None)
    app.state.retention_config = retention_cfg
    retention_worker = None
    factory_for_ops = getattr(app.state, "session_factory", None)
    if factory_for_ops is not None and retention_cfg is not None:
        retention_worker = RetentionWorker(
            factory_for_ops,
            retention_cfg,
            repository=RetentionRepository(factory_for_ops),
            logger=logger,
        )
    app.state.retention_worker = retention_worker

    app.include_router(entity_router)
    app.include_router(entity_timeline_router)
    app.include_router(entity_zones_router)
    app.include_router(timeline_router)
    app.include_router(zone_router)
    app.include_router(rules_router)
    app.include_router(alerts_router)
    app.include_router(targets_router)
    app.include_router(deliveries_router)
    app.include_router(rule_targets_router)
    app.include_router(alert_deliveries_router)
    app.include_router(activity_ws_router)
    app.include_router(ops_router)

    # Operational observability (v0.10.0).
    ops_metrics = OpsMetricsRegistry()
    app.state.ops_metrics = ops_metrics
    app.state.ops_status_collector = OpsStatusCollector(
        session_factory=getattr(app.state, "session_factory", None),
        metrics=ops_metrics,
        logger=logger,
    )
    app.state.retention_control = RetentionControlService(
        retention_worker,
        metrics=ops_metrics,
        logger=logger,
    )

    _mount_live_activity_console(app)

    @app.get("/health", response_model=HealthOut, tags=["system"])
    def health() -> HealthOut:
        """Liveness probe — preserved contract (always 200 when process is up)."""

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
    # Pass NotificationsConfig explicitly (same object load_app_config built).
    # Do not default-construct a second NotificationsConfig here — that would
    # drop env overrides such as JARVIS_NOTIFICATIONS_ALLOW_PRIVATE_TARGETS.
    notifications = getattr(app_config, "notifications", None)
    return create_app(
        database_url=app_config.database.url,
        limits=limits,
        timeline_limits=timeline_limits,
        zone_limits=zone_limits,
        activity_stream_config=app_config.activity_stream,
        alerts_config=app_config.alerts,
        notifications_config=notifications,
        ops_config=getattr(app_config, "ops", None),
        create_schema=create_schema,
    )


def create_app_from_config() -> FastAPI:
    """Factory used by uvicorn: ``uvicorn api.app:create_app_from_config``."""

    from config import load_app_config

    return build_app_from_loaded_config(load_app_config())


def _notifications_allow_private(notif_cfg: Any | None) -> bool:
    """Read allow_private_targets from NotificationsConfig (default False)."""

    if notif_cfg is None:
        return False
    return bool(getattr(notif_cfg, "allow_private_targets", False))
