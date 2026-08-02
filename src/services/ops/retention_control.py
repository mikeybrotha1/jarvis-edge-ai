"""Guarded manual retention triggers (v0.10.0 phase 5)."""

from __future__ import annotations

import logging
from typing import Any

from services.ops.metrics import OpsMetricsRegistry
from services.ops.retention_worker import (
    RetentionBusyError,
    RetentionGuardError,
    RetentionRunSummary,
    RetentionWorker,
    _utc_now,
)


class RetentionControlService:
    """Policy checks + cooldown for manual dry-run and destructive run."""

    def __init__(
        self,
        worker: RetentionWorker | None,
        *,
        metrics: OpsMetricsRegistry | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._worker = worker
        self._metrics = metrics
        self._logger = logger or logging.getLogger(__name__)

    def status_document(self) -> dict[str, Any]:
        worker = self._worker
        if worker is None:
            return {
                "enabled": False,
                "dry_run": True,
                "allow_manual_destructive_run": False,
                "destructive_permitted": False,
                "manual_cooldown_seconds": 30.0,
                "cooldown_remaining_seconds": 0.0,
                "worker": {"state": "not_configured"},
                "domains": {},
                "note": "Retention worker is not configured.",
            }
        cfg = worker._config
        stats = worker.stats()
        return {
            "enabled": bool(cfg.enabled),
            "dry_run": bool(cfg.dry_run),
            "allow_manual_destructive_run": bool(
                cfg.allow_manual_destructive_run
            ),
            "interval_seconds": int(cfg.interval_seconds),
            "batch_size": int(cfg.batch_size),
            "max_batches_per_run": int(cfg.max_batches_per_run),
            "destructive_permitted": worker.destructive_permitted(),
            "manual_cooldown_seconds": worker.manual_cooldown_seconds,
            "cooldown_remaining_seconds": round(
                worker.cooldown_remaining_seconds(), 3
            ),
            "any_domain_enabled": cfg.any_domain_enabled(),
            "worker": {
                "state": stats.get("state"),
                "cycle_active": stats.get("cycle_active"),
                "last_started": stats.get("last_started"),
                "last_completed": stats.get("last_completed"),
                "last_duration_ms": stats.get("last_duration_ms"),
                "rows_examined": stats.get("rows_examined"),
                "rows_deleted": stats.get("rows_deleted"),
                "rows_skipped": stats.get("rows_skipped"),
                "last_error": stats.get("last_error"),
                "cycles_completed": stats.get("cycles_completed"),
                "last_manual_trigger_at": stats.get("last_manual_trigger_at"),
                "manual_dry_runs_total": stats.get("manual_dry_runs_total"),
                "manual_runs_total": stats.get("manual_runs_total"),
                "manual_rejected_total": stats.get("manual_rejected_total"),
                "last_manual_latency_ms": stats.get("last_manual_latency_ms"),
                "last_run": stats.get("last_run"),
            },
            "domains": {
                "observations": {
                    "enabled": bool(cfg.observations.enabled),
                    "keep_days": int(cfg.observations.keep_days),
                },
                "entities": {
                    "enabled": bool(cfg.entities.enabled),
                    "keep_closed_days": int(cfg.entities.keep_closed_days),
                },
                "zone_sessions": {
                    "enabled": bool(cfg.zone_sessions.enabled),
                    "keep_closed_days": int(
                        cfg.zone_sessions.keep_closed_days
                    ),
                },
                "alerts": {
                    "enabled": bool(cfg.alerts.enabled),
                    "keep_resolved_days": int(cfg.alerts.keep_resolved_days),
                },
                "evaluator_state": {
                    "enabled": bool(cfg.evaluator_state.enabled),
                    "keep_inactive_days": int(
                        cfg.evaluator_state.keep_inactive_days
                    ),
                },
                "notification_deliveries": {
                    "enabled": bool(cfg.notification_deliveries.enabled),
                    "keep_terminal_days": int(
                        cfg.notification_deliveries.keep_terminal_days
                    ),
                },
            },
            "note": (
                "Manual triggers use validated server configuration only; "
                "request bodies cannot override policy."
            ),
        }

    async def manual_dry_run(self) -> RetentionRunSummary:
        worker = self._require_worker()
        if not worker.enabled:
            self._reject(worker, "retention_disabled",
                         "Retention is disabled.", http_status=409)
        self._check_cooldown(worker)
        try:
            summary = await worker.try_run_cycle(force_dry_run=True)
        except RetentionBusyError:
            self._reject(
                worker,
                "cycle_active",
                "A retention cycle is already active.",
                http_status=409,
            )
        except RetentionGuardError:
            raise
        except Exception:
            self._logger.exception("Manual retention dry-run failed")
            worker.last_error = "manual_dry_run_failed"
            self._reject(
                worker,
                "execution_failed",
                "Retention dry-run failed.",
                http_status=503,
            )

        # Always non-destructive
        if summary.rows_deleted != 0:
            worker.last_error = "dry_run_deleted_rows"
            self._reject(
                worker,
                "dry_run_integrity",
                "Dry-run integrity check failed.",
                http_status=500,
            )
        worker.manual_dry_runs_total += 1
        worker.last_manual_trigger_at = _utc_now()
        worker.last_manual_latency_ms = summary.duration_ms
        if self._metrics is not None:
            self._metrics.inc("manual_retention_dry_runs_total")
            self._metrics.observe_latency_ms(
                "manual_retention", summary.duration_ms
            )
            self._metrics.mark_success("manual_retention_dry_run")
        return summary

    async def manual_run(self) -> RetentionRunSummary:
        worker = self._require_worker()
        if not worker.enabled:
            self._reject(
                worker,
                "retention_disabled",
                "Retention is disabled.",
                http_status=409,
            )
        if worker.dry_run:
            self._reject(
                worker,
                "dry_run_enabled",
                "Destructive execution is not enabled (dry_run=true).",
                http_status=409,
            )
        if not worker.allow_manual_destructive_run:
            self._reject(
                worker,
                "manual_guard_disabled",
                "Manual destructive retention is not permitted.",
                http_status=403,
            )
        if not worker._config.any_domain_enabled():
            self._reject(
                worker,
                "no_domain_enabled",
                "At least one retention domain must be enabled.",
                http_status=422,
            )
        self._check_cooldown(worker)
        try:
            summary = await worker.try_run_cycle(force_dry_run=False)
        except RetentionBusyError:
            self._reject(
                worker,
                "cycle_active",
                "A retention cycle is already active.",
                http_status=409,
            )
        except RetentionGuardError:
            raise
        except Exception:
            self._logger.exception("Manual retention run failed")
            worker.last_error = "manual_run_failed"
            self._reject(
                worker,
                "execution_failed",
                "Retention run failed.",
                http_status=503,
            )

        worker.manual_runs_total += 1
        worker.last_manual_trigger_at = _utc_now()
        worker.last_manual_latency_ms = summary.duration_ms
        if self._metrics is not None:
            self._metrics.inc("manual_retention_runs_total")
            self._metrics.observe_latency_ms(
                "manual_retention", summary.duration_ms
            )
            self._metrics.mark_success("manual_retention_run")
        return summary

    def _require_worker(self) -> RetentionWorker:
        if self._worker is None:
            raise RetentionGuardError(
                "not_configured",
                "Retention worker is not configured.",
                http_status=503,
            )
        return self._worker

    def _check_cooldown(self, worker: RetentionWorker) -> None:
        remaining = worker.cooldown_remaining_seconds()
        if remaining > 0:
            self._reject(
                worker,
                "rate_limited",
                "Manual retention trigger is rate-limited; try again later.",
                http_status=429,
            )

    def _reject(
        self,
        worker: RetentionWorker | None,
        code: str,
        message: str,
        *,
        http_status: int,
    ) -> None:
        if worker is not None:
            worker.manual_rejected_total += 1
        if self._metrics is not None:
            self._metrics.inc("manual_retention_rejected_total")
            self._metrics.mark_error("manual_retention")
        raise RetentionGuardError(code, message, http_status=http_status)
