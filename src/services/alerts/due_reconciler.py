"""Lightweight due_at reconciler for sustained alert conditions."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from services.alerts.evaluation_service import AlertEvaluationService


class AlertDueReconciler:
    def __init__(
        self,
        evaluation_service: AlertEvaluationService,
        *,
        interval_seconds: float = 2.0,
        batch_size: int = 100,
        enabled: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        self._eval = evaluation_service
        self.interval_seconds = max(0.5, float(interval_seconds))
        self.batch_size = max(1, int(batch_size))
        self.enabled = enabled
        self._logger = logger or logging.getLogger(__name__)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._error_count = 0
        self._iterations = 0
        self._last_success_at: datetime | None = None
        self._last_error_at: datetime | None = None
        self._last_triggered_count = 0

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self.is_running,
            "error_count": self._error_count,
            "iterations": self._iterations,
            "last_success_at": self._last_success_at,
            "last_error_at": self._last_error_at,
            "last_triggered_count": self._last_triggered_count,
            "interval_seconds": self.interval_seconds,
            "batch_size": self.batch_size,
        }

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(), name="alert-due-reconciler"
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

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                triggered = await asyncio.to_thread(
                    self._eval.process_due_states, batch_size=self.batch_size
                )
                self._iterations += 1
                self._last_triggered_count = len(triggered or [])
                self._last_success_at = datetime.now(timezone.utc)
            except Exception:
                self._error_count += 1
                self._last_error_at = datetime.now(timezone.utc)
                self._logger.exception("Alert due reconciler iteration failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.interval_seconds
                )
                return
            except asyncio.TimeoutError:
                continue
