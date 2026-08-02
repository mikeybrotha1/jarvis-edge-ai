"""Lightweight due_at reconciler for sustained alert conditions."""

from __future__ import annotations

import asyncio
import logging

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
                await asyncio.to_thread(
                    self._eval.process_due_states, batch_size=self.batch_size
                )
            except Exception:
                self._logger.exception("Alert due reconciler iteration failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.interval_seconds
                )
                return
            except asyncio.TimeoutError:
                continue
