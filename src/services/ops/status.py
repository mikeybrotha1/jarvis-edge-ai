"""Operational component status collection (v0.10.0 phase 1)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from services.ops.metrics import OpsMetricsRegistry

# Historical reconciler errors must not permanently degrade readiness.
# Degrade only when the last error is more recent than the last success,
# or when no success has occurred yet and the error is still within this window.
_DUE_RECONCILER_RECENT_ERROR_WINDOW = timedelta(minutes=5)


class ComponentStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class OverallStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class OpsStatusCollector:
    """Build safe operational status and metrics from app runtime state."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] | None,
        metrics: OpsMetricsRegistry | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.metrics = metrics or OpsMetricsRegistry()
        self._logger = logger or logging.getLogger(__name__)

    def check_database(self) -> dict[str, Any]:
        if self._session_factory is None:
            return {
                "status": ComponentStatus.UNAVAILABLE.value,
                "detail": "Database session factory is not configured.",
            }
        try:
            with self._session_factory() as session:
                session.execute(text("SELECT 1"))
            self.metrics.mark_success("database")
            self.metrics.set_gauge("database_up", 1.0)
            return {
                "status": ComponentStatus.HEALTHY.value,
                "detail": "Database connectivity OK.",
            }
        except Exception:  # noqa: BLE001 - sanitized public surface
            self._logger.exception("Database health check failed")
            self.metrics.mark_error("database")
            self.metrics.set_gauge("database_up", 0.0)
            return {
                "status": ComponentStatus.UNAVAILABLE.value,
                "detail": "Database connectivity failed.",
            }

    def check_timeline(self, timeline_service: Any | None) -> dict[str, Any]:
        if timeline_service is None:
            return {
                "status": ComponentStatus.UNAVAILABLE.value,
                "detail": "Timeline service is not configured.",
            }
        try:
            # Bounded probe — no full scan.
            timeline_service.list_timeline(limit=1, sort="desc")
            self.metrics.mark_success("timeline")
            return {
                "status": ComponentStatus.HEALTHY.value,
                "detail": "Timeline composition OK.",
            }
        except Exception:  # noqa: BLE001
            self._logger.exception("Timeline health check failed")
            self.metrics.mark_error("timeline")
            return {
                "status": ComponentStatus.DEGRADED.value,
                "detail": "Timeline composition check failed.",
            }

    def check_activity_listener(
        self,
        *,
        stream_enabled: bool,
        listener: Any | None,
        broker: Any | None,
        stream_ready: bool,
    ) -> dict[str, Any]:
        if not stream_enabled:
            return {
                "status": ComponentStatus.DISABLED.value,
                "detail": "Activity stream is disabled.",
            }
        # SQLite / test broker-only mode: no LISTEN connection.
        if listener is None and broker is not None:
            clients = 0
            try:
                clients = int(getattr(broker, "connection_count", 0) or 0)
            except Exception:  # noqa: BLE001
                clients = 0
            self.metrics.set_gauge("activity_ws_clients", float(clients))
            return {
                "status": ComponentStatus.HEALTHY.value,
                "detail": "Activity broker ready (no LISTEN connection).",
                "websocket_clients": clients,
            }
        if listener is None:
            return {
                "status": ComponentStatus.UNAVAILABLE.value,
                "detail": "Activity listener is not configured.",
            }
        ready = bool(getattr(listener, "is_ready", False) or stream_ready)
        if ready:
            self.metrics.mark_success("activity_listener")
            return {
                "status": ComponentStatus.HEALTHY.value,
                "detail": "Activity listener is ready.",
            }
        self.metrics.mark_error("activity_listener")
        return {
            "status": ComponentStatus.DEGRADED.value,
            "detail": "Activity listener is not ready.",
        }

    def check_alert_consumer(self, consumer: Any | None) -> dict[str, Any]:
        if consumer is None:
            return {
                "status": ComponentStatus.DISABLED.value,
                "detail": "Alert consumer is not configured.",
            }
        if not bool(getattr(consumer, "enabled", True)):
            return {
                "status": ComponentStatus.DISABLED.value,
                "detail": "Alert consumer is disabled.",
            }
        stats = {}
        if hasattr(consumer, "stats"):
            try:
                stats = dict(consumer.stats())
            except Exception:  # noqa: BLE001
                stats = {}
        queue_depth = stats.get("queue_depth")
        if queue_depth is None and hasattr(consumer, "queue_depth"):
            try:
                queue_depth = int(consumer.queue_depth)
            except Exception:  # noqa: BLE001
                queue_depth = None
        if queue_depth is not None:
            self.metrics.set_gauge("alert_consumer_queue_depth", float(queue_depth))
        dropped = stats.get("dropped", getattr(consumer, "dropped_count", 0))
        self.metrics.set_gauge("alert_consumer_dropped", float(dropped or 0))

        ready = bool(getattr(consumer, "is_ready", False))
        degraded = bool(getattr(consumer, "is_degraded", False))
        detail = {
            "queue_depth": queue_depth,
            "dropped": dropped,
            "consumer_name": getattr(consumer, "consumer_name", None),
            "last_success_at": _iso(stats.get("last_success_at")),
            "checkpoint_event_id": stats.get("checkpoint_event_id"),
            "checkpoint_occurred_at": _iso(stats.get("checkpoint_occurred_at")),
        }
        if not ready:
            return {
                "status": ComponentStatus.DEGRADED.value,
                "detail": "Alert consumer is starting or not ready.",
                **{k: v for k, v in detail.items() if v is not None},
            }
        if degraded:
            return {
                "status": ComponentStatus.DEGRADED.value,
                "detail": "Alert consumer is degraded.",
                **{k: v for k, v in detail.items() if v is not None},
            }
        return {
            "status": ComponentStatus.HEALTHY.value,
            "detail": "Alert consumer is ready.",
            **{k: v for k, v in detail.items() if v is not None},
        }

    def check_due_reconciler(self, reconciler: Any | None) -> dict[str, Any]:
        if reconciler is None:
            return {
                "status": ComponentStatus.DISABLED.value,
                "detail": "Alert due reconciler is not configured.",
            }
        if not bool(getattr(reconciler, "enabled", True)):
            return {
                "status": ComponentStatus.DISABLED.value,
                "detail": "Alert due reconciler is disabled.",
            }
        stats = {}
        if hasattr(reconciler, "stats"):
            try:
                stats = dict(reconciler.stats())
            except Exception:  # noqa: BLE001
                stats = {}
        running = bool(stats.get("running", getattr(reconciler, "is_running", False)))
        errors = int(stats.get("error_count", 0) or 0)
        self.metrics.set_gauge("alert_reconciler_errors", float(errors))
        last_success = stats.get("last_success_at")
        last_error = stats.get("last_error_at")
        last_success_iso = (
            _iso(last_success) if isinstance(last_success, datetime) else last_success
        )
        last_error_iso = (
            _iso(last_error) if isinstance(last_error, datetime) else last_error
        )
        if not running:
            return {
                "status": ComponentStatus.DEGRADED.value,
                "detail": "Alert due reconciler is not running.",
                "error_count": errors,
                "last_success_at": last_success_iso,
                "last_error_at": last_error_iso,
            }
        # Prefer current-cycle failure signal: last_error after last_success.
        # Fall back to a short recent-error window when success has never occurred.
        recent_error = False
        if isinstance(last_error, datetime):
            err = (
                last_error
                if last_error.tzinfo is not None
                else last_error.replace(tzinfo=timezone.utc)
            )
            if isinstance(last_success, datetime):
                ok = (
                    last_success
                    if last_success.tzinfo is not None
                    else last_success.replace(tzinfo=timezone.utc)
                )
                recent_error = err > ok
            else:
                recent_error = (_utc_now() - err) <= _DUE_RECONCILER_RECENT_ERROR_WINDOW
        if recent_error:
            return {
                "status": ComponentStatus.DEGRADED.value,
                "detail": "Alert due reconciler has recent errors.",
                "error_count": errors,
                "last_success_at": last_success_iso,
                "last_error_at": last_error_iso,
            }
        return {
            "status": ComponentStatus.HEALTHY.value,
            "detail": "Alert due reconciler is running.",
            "error_count": errors,
            "last_success_at": last_success_iso,
            "last_error_at": last_error_iso,
        }

    def check_notification_worker(self, worker: Any | None) -> dict[str, Any]:
        if worker is None:
            return {
                "status": ComponentStatus.DISABLED.value,
                "detail": "Notification worker is not configured.",
            }
        if not bool(getattr(worker, "enabled", True)):
            return {
                "status": ComponentStatus.DISABLED.value,
                "detail": "Notification worker is disabled.",
            }
        stats: dict[str, Any] = {}
        if hasattr(worker, "stats"):
            try:
                stats = dict(worker.stats())
            except Exception:  # noqa: BLE001
                stats = {}
        for key in (
            "pending",
            "processing",
            "delivered_total",
            "failed_total",
            "exhausted_total",
            "retry_total",
        ):
            if key in stats and stats[key] is not None:
                self.metrics.set_gauge(f"notification_{key}", float(stats[key]))
        if stats.get("last_delivery_latency_ms") is not None:
            self.metrics.observe_latency_ms(
                "notification_delivery",
                float(stats["last_delivery_latency_ms"]),
            )
        ready = bool(getattr(worker, "is_ready", False) or stats.get("ready"))
        degraded = bool(
            getattr(worker, "is_degraded", False) or stats.get("degraded")
        )
        payload = {
            "pending": stats.get("pending"),
            "processing": stats.get("processing"),
            "delivered_total": stats.get("delivered_total"),
            "failed_total": stats.get("failed_total"),
            "exhausted_total": stats.get("exhausted_total"),
            "retry_total": stats.get("retry_total"),
            "average_delivery_latency_ms": stats.get(
                "average_delivery_latency_ms"
            ),
            "last_delivery_latency_ms": stats.get("last_delivery_latency_ms"),
            "worker_id": stats.get("worker_id"),
        }
        clean = {k: v for k, v in payload.items() if v is not None}
        if not ready:
            return {
                "status": ComponentStatus.DEGRADED.value,
                "detail": "Notification worker is not ready.",
                **clean,
            }
        if degraded:
            return {
                "status": ComponentStatus.DEGRADED.value,
                "detail": "Notification worker is degraded.",
                **clean,
            }
        return {
            "status": ComponentStatus.HEALTHY.value,
            "detail": "Notification worker is ready.",
            **clean,
        }

    def collect(self, app_state: Any) -> dict[str, Any]:
        """Build full ops status document from FastAPI app.state."""

        components: dict[str, Any] = {}
        components["database"] = self.check_database()
        components["timeline_composition"] = self.check_timeline(
            getattr(app_state, "timeline_service", None)
        )
        components["activity_listener"] = self.check_activity_listener(
            stream_enabled=bool(
                getattr(app_state, "activity_stream_enabled", False)
            ),
            listener=getattr(app_state, "activity_listener", None),
            broker=getattr(app_state, "activity_broker", None),
            stream_ready=bool(
                getattr(app_state, "activity_stream_ready", False)
            ),
        )
        components["alert_consumer"] = self.check_alert_consumer(
            getattr(app_state, "alert_consumer", None)
        )
        components["due_reconciler"] = self.check_due_reconciler(
            getattr(app_state, "alert_reconciler", None)
        )
        components["notification_worker"] = self.check_notification_worker(
            getattr(app_state, "notification_worker", None)
        )

        overall = self._overall(components)
        document: dict[str, Any] = {
            "status": overall.value,
            "service": "jarvis-entity-query-api",
            "timestamp": _utc_now().isoformat(),
            "components": components,
            "metrics": self.metrics.snapshot(),
        }
        # Additive phase-3 field: policy snapshot only (no execution).
        retention = self._retention_summary(app_state)
        if retention is not None:
            document["retention"] = retention
        return document

    @staticmethod
    def _retention_summary(app_state: Any) -> dict[str, Any] | None:
        cfg = getattr(app_state, "retention_config", None)
        if cfg is None:
            ops = getattr(app_state, "ops_config", None)
            cfg = getattr(ops, "retention", None) if ops is not None else None
        if cfg is None:
            return None
        try:
            worker = getattr(app_state, "retention_worker", None)
            worker_stats: dict[str, Any] = {}
            if worker is not None and hasattr(worker, "stats"):
                try:
                    worker_stats = dict(worker.stats())
                except Exception:  # noqa: BLE001
                    worker_stats = {}
            if not bool(cfg.enabled):
                execution = "disabled"
                note = "Retention worker is disabled (ops.retention.enabled=false)."
            elif worker is None:
                execution = "not_configured"
                note = "Retention worker is not configured."
            else:
                execution = str(worker_stats.get("state") or "idle")
                note = (
                    "Retention worker active (dry_run)."
                    if bool(cfg.dry_run)
                    else "Retention worker active (destructive mode)."
                )
            return {
                "enabled": bool(cfg.enabled),
                "dry_run": bool(cfg.dry_run),
                "interval_seconds": int(cfg.interval_seconds),
                "batch_size": int(cfg.batch_size),
                "max_batches_per_run": int(cfg.max_batches_per_run),
                "execution": execution,
                "note": note,
                "worker": {
                    "state": worker_stats.get("state"),
                    "last_started": worker_stats.get("last_started"),
                    "last_completed": worker_stats.get("last_completed"),
                    "last_duration_ms": worker_stats.get("last_duration_ms"),
                    "rows_examined": worker_stats.get("rows_examined"),
                    "rows_deleted": worker_stats.get("rows_deleted"),
                    "rows_skipped": worker_stats.get("rows_skipped"),
                    "last_error": worker_stats.get("last_error"),
                    "cycles_completed": worker_stats.get("cycles_completed"),
                    "last_run": worker_stats.get("last_run"),
                },
                "domains": {
                    "observations": {
                        "enabled": bool(cfg.observations.enabled),
                        "keep_days": int(cfg.observations.keep_days),
                    },
                    "entities": {
                        "enabled": bool(cfg.entities.enabled),
                        "keep_closed_days": int(
                            cfg.entities.keep_closed_days
                        ),
                    },
                    "zone_sessions": {
                        "enabled": bool(cfg.zone_sessions.enabled),
                        "keep_closed_days": int(
                            cfg.zone_sessions.keep_closed_days
                        ),
                    },
                    "alerts": {
                        "enabled": bool(cfg.alerts.enabled),
                        "keep_resolved_days": int(
                            cfg.alerts.keep_resolved_days
                        ),
                    },
                    "evaluator_state": {
                        "enabled": bool(cfg.evaluator_state.enabled),
                        "keep_inactive_days": int(
                            cfg.evaluator_state.keep_inactive_days
                        ),
                    },
                    "notification_deliveries": {
                        "enabled": bool(
                            cfg.notification_deliveries.enabled
                        ),
                        "keep_terminal_days": int(
                            cfg.notification_deliveries.keep_terminal_days
                        ),
                    },
                },
            }
        except Exception:  # noqa: BLE001
            return {
                "enabled": False,
                "dry_run": True,
                "execution": "unavailable",
                "note": "Retention configuration unavailable.",
            }

    def readiness(self, app_state: Any) -> dict[str, Any]:
        """Readiness: process can serve traffic when database is healthy."""

        db = self.check_database()
        db_status = db["status"]
        ready = db_status == ComponentStatus.HEALTHY.value
        return {
            "ready": ready,
            "status": "ready" if ready else "not_ready",
            "timestamp": _utc_now().isoformat(),
            "checks": {
                "database": db_status,
            },
        }

    @staticmethod
    def _overall(components: dict[str, Any]) -> OverallStatus:
        statuses = [
            str(body.get("status", ComponentStatus.UNAVAILABLE.value))
            for body in components.values()
        ]
        # Required path components for API serving.
        required = (
            components.get("database", {}).get("status"),
            components.get("timeline_composition", {}).get("status"),
        )
        if ComponentStatus.UNAVAILABLE.value in required:
            return OverallStatus.UNAVAILABLE
        # Optional components reporting unavailable → degrade overall.
        if ComponentStatus.UNAVAILABLE.value in statuses:
            return OverallStatus.DEGRADED
        if ComponentStatus.DEGRADED.value in statuses:
            return OverallStatus.DEGRADED
        return OverallStatus.HEALTHY
