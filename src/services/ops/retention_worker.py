"""Bounded retention execution engine (v0.10.0 phase 4).

Optional background worker. Starts only when ``RetentionConfig.enabled``.
Failures are isolated — never raised into API / alert / notification paths.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID

from config.models import RetentionConfig
from storage.retention_repository import RetentionRepository
from storage.sqlalchemy_db import session_scope
from sqlalchemy.orm import Session, sessionmaker


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DomainRunResult:
    domain: str
    dry_run: bool
    cutoff: str
    eligible_total: int
    batches: int
    rows_examined: int
    rows_deleted: int
    rows_skipped: int
    duration_ms: float
    status: str  # ok | skipped | error
    error: str | None = None


@dataclass
class RetentionRunSummary:
    dry_run: bool
    started_at: str
    completed_at: str
    duration_ms: float
    domains: list[DomainRunResult] = field(default_factory=list)
    rows_examined: int = 0
    rows_deleted: int = 0
    rows_skipped: int = 0
    status: str = "ok"
    error: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "rows_examined": self.rows_examined,
            "rows_deleted": self.rows_deleted,
            "rows_skipped": self.rows_skipped,
            "status": self.status,
            "error": self.error,
            "domains": [asdict(d) for d in self.domains],
        }


class RetentionWorker:
    """Interval-driven retention worker with dry-run and batch caps."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        config: RetentionConfig,
        *,
        repository: RetentionRepository | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._config = config
        self._repo = repository or RetentionRepository(session_factory)
        self._logger = logger or logging.getLogger(__name__)
        self.enabled = bool(config.enabled)
        self.dry_run = bool(config.dry_run)
        self.allow_manual_destructive_run = bool(
            getattr(config, "allow_manual_destructive_run", False)
        )
        self.interval_seconds = max(60, int(config.interval_seconds))
        self.batch_size = max(1, int(config.batch_size))
        self.max_batches_per_run = max(1, int(config.max_batches_per_run))
        self.manual_cooldown_seconds = 30.0

        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._cycle_lock = asyncio.Lock()

        # In-memory execution state (exposed via ops status).
        self.state: str = "idle"  # idle | running | disabled
        self.last_started: datetime | None = None
        self.last_completed: datetime | None = None
        self.last_duration_ms: float | None = None
        self.rows_examined: int = 0
        self.rows_deleted: int = 0
        self.rows_skipped: int = 0
        self.last_error: str | None = None
        self.last_run: RetentionRunSummary | None = None
        self.cycles_completed: int = 0
        self.last_manual_trigger_at: datetime | None = None
        self.manual_dry_runs_total: int = 0
        self.manual_runs_total: int = 0
        self.manual_rejected_total: int = 0
        self.last_manual_latency_ms: float | None = None

        if not self.enabled:
            self.state = "disabled"

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "allow_manual_destructive_run": self.allow_manual_destructive_run,
            "state": self.state,
            "interval_seconds": self.interval_seconds,
            "batch_size": self.batch_size,
            "max_batches_per_run": self.max_batches_per_run,
            "manual_cooldown_seconds": self.manual_cooldown_seconds,
            "last_started": (
                self.last_started.isoformat() if self.last_started else None
            ),
            "last_completed": (
                self.last_completed.isoformat()
                if self.last_completed
                else None
            ),
            "last_duration_ms": self.last_duration_ms,
            "rows_examined": self.rows_examined,
            "rows_deleted": self.rows_deleted,
            "rows_skipped": self.rows_skipped,
            "last_error": self.last_error,
            "cycles_completed": self.cycles_completed,
            "last_manual_trigger_at": (
                self.last_manual_trigger_at.isoformat()
                if self.last_manual_trigger_at
                else None
            ),
            "manual_dry_runs_total": self.manual_dry_runs_total,
            "manual_runs_total": self.manual_runs_total,
            "manual_rejected_total": self.manual_rejected_total,
            "last_manual_latency_ms": self.last_manual_latency_ms,
            "last_run": (
                self.last_run.to_public_dict() if self.last_run else None
            ),
            "cycle_active": self._cycle_lock.locked(),
            "destructive_permitted": self.destructive_permitted(),
        }

    def destructive_permitted(self) -> bool:
        """True when server config allows a destructive manual run."""

        return (
            self.enabled
            and not self.dry_run
            and self.allow_manual_destructive_run
            and self._config.any_domain_enabled()
        )

    def cooldown_remaining_seconds(self) -> float:
        if self.last_manual_trigger_at is None:
            return 0.0
        elapsed = (_utc_now() - self.last_manual_trigger_at).total_seconds()
        remaining = self.manual_cooldown_seconds - elapsed
        return max(0.0, remaining)

    async def start(self) -> None:
        if not self.enabled:
            self.state = "disabled"
            return
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self.state = "idle"
        self._task = asyncio.create_task(
            self._run_loop(), name="retention-worker"
        )
        self._logger.info(
            "Retention worker started (dry_run=%s interval=%ss)",
            self.dry_run,
            self.interval_seconds,
        )

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self.enabled:
            self.state = "idle"
        else:
            self.state = "disabled"
        self._logger.info("Retention worker stopped")

    async def _run_loop(self) -> None:
        # Sleep first so enabling retention does not immediately delete data;
        # manual dry-run/run still invoke a cycle on demand.
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=float(self.interval_seconds)
                )
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self.run_cycle()
            except Exception:
                # Isolation: never crash the loop into the event loop caller.
                self._logger.exception("Retention worker cycle failed")
                self.last_error = "cycle_failed"

    async def run_cycle(self) -> RetentionRunSummary:
        """Run one retention cycle (async lock prevents overlap)."""

        async with self._cycle_lock:
            return await asyncio.to_thread(self.run_cycle_sync)

    async def try_run_cycle(
        self, *, force_dry_run: bool | None = None
    ) -> RetentionRunSummary:
        """Run one cycle if idle; raise RetentionBusyError if a cycle is active."""

        if self._cycle_lock.locked():
            raise RetentionBusyError("Retention cycle already active.")
        async with self._cycle_lock:
            return await asyncio.to_thread(
                self.run_cycle_sync, force_dry_run=force_dry_run
            )

    def run_cycle_sync(
        self, *, force_dry_run: bool | None = None
    ) -> RetentionRunSummary:
        """Synchronous single cycle — used by tests and background thread.

        Parameters
        ----------
        force_dry_run:
            When True/False, temporarily overrides configured dry_run for this
            cycle only (manual dry-run endpoint forces True).
        """

        previous_dry = self.dry_run
        if force_dry_run is not None:
            self.dry_run = bool(force_dry_run)
        try:
            if not self.enabled and force_dry_run is None:
                summary = RetentionRunSummary(
                    dry_run=self.dry_run,
                    started_at=_utc_now().isoformat(),
                    completed_at=_utc_now().isoformat(),
                    duration_ms=0.0,
                    status="disabled",
                )
                return summary

            started = _utc_now()
            mono0 = time.perf_counter()
            self.state = "running"
            self.last_started = started
            self.last_error = None
            domain_results: list[DomainRunResult] = []
            examined = 0
            deleted = 0
            skipped = 0
            status = "ok"
            error: str | None = None
            effective_dry = self.dry_run

            try:
                for domain_name, runner in self._domain_runners():
                    try:
                        result = runner()
                        domain_results.append(result)
                        examined += result.rows_examined
                        deleted += result.rows_deleted
                        skipped += result.rows_skipped
                        if result.status == "error":
                            status = "degraded"
                    except Exception as exc:  # noqa: BLE001 - isolate domain
                        self._logger.exception(
                            "Retention domain %s failed", domain_name
                        )
                        domain_results.append(
                            DomainRunResult(
                                domain=domain_name,
                                dry_run=effective_dry,
                                cutoff="",
                                eligible_total=0,
                                batches=0,
                                rows_examined=0,
                                rows_deleted=0,
                                rows_skipped=0,
                                duration_ms=0.0,
                                status="error",
                                error="domain_failed",
                            )
                        )
                        status = "degraded"
                        error = "domain_failed"
                        _ = exc
            except Exception:  # noqa: BLE001
                self._logger.exception("Retention cycle aborted")
                status = "error"
                error = "cycle_failed"
                self.last_error = error

            completed = _utc_now()
            duration_ms = (time.perf_counter() - mono0) * 1000.0
            summary = RetentionRunSummary(
                dry_run=effective_dry,
                started_at=started.isoformat(),
                completed_at=completed.isoformat(),
                duration_ms=duration_ms,
                domains=domain_results,
                rows_examined=examined,
                rows_deleted=deleted,
                rows_skipped=skipped,
                status=status,
                error=error,
            )
            self.last_completed = completed
            self.last_duration_ms = duration_ms
            self.rows_examined = examined
            self.rows_deleted = deleted
            self.rows_skipped = skipped
            self.last_run = summary
            self.cycles_completed += 1
            self.state = "idle" if self.enabled else "disabled"
            return summary
        finally:
            self.dry_run = previous_dry


    def _domain_runners(
        self,
    ) -> list[tuple[str, Callable[[], DomainRunResult]]]:
        cfg = self._config
        runners: list[tuple[str, Callable[[], DomainRunResult]]] = []
        if cfg.observations.enabled:
            runners.append(
                (
                    "observations",
                    lambda: self._run_domain(
                        "observations",
                        keep_days=cfg.observations.keep_days,
                        count_fn=self._repo.count_eligible_observations,
                        fetch_fn=self._repo.fetch_eligible_observation_ids,
                        delete_fn=self._repo.delete_observations_by_ids,
                    ),
                )
            )
        if cfg.zone_sessions.enabled:
            runners.append(
                (
                    "zone_sessions",
                    lambda: self._run_domain(
                        "zone_sessions",
                        keep_days=cfg.zone_sessions.keep_closed_days,
                        count_fn=self._repo.count_eligible_zone_sessions,
                        fetch_fn=self._repo.fetch_eligible_zone_session_ids,
                        delete_fn=self._repo.delete_zone_sessions_by_ids,
                    ),
                )
            )
        if cfg.alerts.enabled:
            runners.append(
                (
                    "alerts",
                    lambda: self._run_domain(
                        "alerts",
                        keep_days=cfg.alerts.keep_resolved_days,
                        count_fn=self._repo.count_eligible_alerts,
                        fetch_fn=self._repo.fetch_eligible_alert_ids,
                        delete_fn=self._repo.delete_alerts_by_ids,
                    ),
                )
            )
        if cfg.evaluator_state.enabled:
            runners.append(
                (
                    "evaluator_state",
                    lambda: self._run_domain(
                        "evaluator_state",
                        keep_days=cfg.evaluator_state.keep_inactive_days,
                        count_fn=self._repo.count_eligible_evaluator_states,
                        fetch_fn=self._repo.fetch_eligible_evaluator_state_ids,
                        delete_fn=self._repo.delete_evaluator_states_by_ids,
                    ),
                )
            )
        if cfg.notification_deliveries.enabled:
            runners.append(
                (
                    "notification_deliveries",
                    lambda: self._run_domain(
                        "notification_deliveries",
                        keep_days=cfg.notification_deliveries.keep_terminal_days,
                        count_fn=self._repo.count_eligible_notification_deliveries,
                        fetch_fn=self._repo.fetch_eligible_notification_delivery_ids,
                        delete_fn=self._repo.delete_notification_deliveries_by_ids,
                    ),
                )
            )
        # Entities last (experimental): eligibility requires cascade-safe
        # dependents already pruned (no alerts / evaluator rows remaining).
        if cfg.entities.enabled:
            runners.append(
                (
                    "entities",
                    lambda: self._run_domain(
                        "entities",
                        keep_days=cfg.entities.keep_closed_days,
                        count_fn=self._repo.count_eligible_entities,
                        fetch_fn=self._repo.fetch_eligible_entity_ids,
                        delete_fn=self._repo.delete_entities_by_ids,
                    ),
                )
            )
        return runners

    def _run_domain(
        self,
        domain: str,
        *,
        keep_days: int,
        count_fn: Callable[..., int],
        fetch_fn: Callable[..., list[UUID]],
        delete_fn: Callable[..., int],
    ) -> DomainRunResult:
        mono0 = time.perf_counter()
        cutoff = _utc_now() - timedelta(days=int(keep_days))
        cutoff_iso = cutoff.isoformat()
        try:
            eligible_total = int(count_fn(cutoff=cutoff))
        except Exception:  # noqa: BLE001
            self._logger.exception("Retention count failed domain=%s", domain)
            return DomainRunResult(
                domain=domain,
                dry_run=self.dry_run,
                cutoff=cutoff_iso,
                eligible_total=0,
                batches=0,
                rows_examined=0,
                rows_deleted=0,
                rows_skipped=0,
                duration_ms=(time.perf_counter() - mono0) * 1000.0,
                status="error",
                error="count_failed",
            )

        batches = 0
        examined = 0
        deleted = 0
        skipped = 0

        # Cap work this cycle.
        remaining_batches = self.max_batches_per_run
        while remaining_batches > 0:
            remaining_batches -= 1
            try:
                ids = list(
                    fetch_fn(cutoff=cutoff, limit=self.batch_size)
                )
            except Exception:  # noqa: BLE001
                self._logger.exception(
                    "Retention fetch failed domain=%s", domain
                )
                return DomainRunResult(
                    domain=domain,
                    dry_run=self.dry_run,
                    cutoff=cutoff_iso,
                    eligible_total=eligible_total,
                    batches=batches,
                    rows_examined=examined,
                    rows_deleted=deleted,
                    rows_skipped=skipped,
                    duration_ms=(time.perf_counter() - mono0) * 1000.0,
                    status="error",
                    error="fetch_failed",
                )
            if not ids:
                break
            batches += 1
            examined += len(ids)
            if self.dry_run:
                # Exercise full path except final DELETE.
                skipped += len(ids)
                # Dry-run does not delete, so the same IDs would reappear.
                # Stop after one batch per domain to honor max_batches and
                # avoid infinite loops on the same eligible set.
                break

            try:
                # One short transaction per batch.
                with session_scope(self._session_factory) as session:
                    removed = int(delete_fn(ids, session=session))
                deleted += removed
                skipped += max(0, len(ids) - removed)
            except Exception:  # noqa: BLE001
                self._logger.exception(
                    "Retention delete failed domain=%s", domain
                )
                return DomainRunResult(
                    domain=domain,
                    dry_run=self.dry_run,
                    cutoff=cutoff_iso,
                    eligible_total=eligible_total,
                    batches=batches,
                    rows_examined=examined,
                    rows_deleted=deleted,
                    rows_skipped=skipped,
                    duration_ms=(time.perf_counter() - mono0) * 1000.0,
                    status="error",
                    error="delete_failed",
                )

            if len(ids) < self.batch_size:
                break

        return DomainRunResult(
            domain=domain,
            dry_run=self.dry_run,
            cutoff=cutoff_iso,
            eligible_total=eligible_total,
            batches=batches,
            rows_examined=examined,
            rows_deleted=deleted,
            rows_skipped=skipped,
            duration_ms=(time.perf_counter() - mono0) * 1000.0,
            status="ok",
        )


class RetentionBusyError(RuntimeError):
    """A retention cycle is already running."""


class RetentionGuardError(RuntimeError):
    """Manual retention trigger rejected by safety guards."""

    def __init__(self, code: str, message: str, *, http_status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
