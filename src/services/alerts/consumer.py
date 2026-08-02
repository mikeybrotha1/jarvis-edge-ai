"""Committed-event consumer with checkpoint recovery (v0.8.0)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from storage.alert_repositories import AlertCheckpointRepository
from storage.timeline_cursor import encode_cursor, decode_cursor
from storage.timeline_models import (
    TimelineEvent,
    TimelineEventType,
    TimelineListFilter,
    TimelineCursor,
)
from services.alerts.evaluation_service import (
    ALERT_EVENT_TYPES,
    SOURCE_EVENT_TYPES_FOR_EVAL,
    AlertEvaluationService,
)
from services.timeline_service import TimelineService


class AlertCommittedEventConsumer:
    """Recover from checkpoint, then accept live resolved source events.

    Does not own LISTEN; receives TimelineEvent via ``submit`` after the
    activity listener resolves notifications (thin fan-out).
    """

    def __init__(
        self,
        *,
        evaluation_service: AlertEvaluationService,
        timeline_service: TimelineService,
        checkpoint_repository: AlertCheckpointRepository,
        consumer_name: str = "jarvis-alert-evaluator",
        queue_size: int = 500,
        replay_overlap_seconds: float = 5.0,
        startup_catchup_limit: int = 500,
        enabled: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        self._eval = evaluation_service
        self._timeline = timeline_service
        self._checkpoints = checkpoint_repository
        self.consumer_name = consumer_name
        self.queue_size = max(1, queue_size)
        self.replay_overlap_seconds = max(0.0, replay_overlap_seconds)
        self.startup_catchup_limit = max(1, startup_catchup_limit)
        self.enabled = enabled
        self._logger = logger or logging.getLogger(__name__)
        self._queue: asyncio.Queue[TimelineEvent | None] = asyncio.Queue(
            maxsize=self.queue_size
        )
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._ready = asyncio.Event()
        self._degraded = False
        self._dropped = 0

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    @property
    def is_degraded(self) -> bool:
        return self._degraded

    async def start(self) -> None:
        if not self.enabled:
            self._ready.set()
            return
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._ready.clear()
        self._task = asyncio.create_task(
            self._run(), name="alert-committed-event-consumer"
        )

    async def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._ready.clear()

    async def wait_until_ready(self, timeout: float = 30.0) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def submit(self, event: TimelineEvent) -> None:
        """Fan-out entry from activity listener (non-blocking if possible)."""

        if not self.enabled or self._stop.is_set():
            return
        if event.event_type.value in ALERT_EVENT_TYPES:
            return
        if event.event_type.value not in SOURCE_EVENT_TYPES_FOR_EVAL:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1
            self._degraded = True
            self._logger.warning(
                "Alert consumer queue full; dropped source event_id=%s",
                event.id,
            )

    async def _run(self) -> None:
        try:
            await asyncio.to_thread(self._catch_up)
            self._ready.set()
            self._logger.info(
                "Alert consumer catch-up complete consumer=%s",
                self.consumer_name,
            )
        except Exception:
            self._degraded = True
            self._logger.exception("Alert consumer catch-up failed")
            self._ready.set()

        while not self._stop.is_set():
            try:
                item = await self._queue.get()
            except asyncio.CancelledError:
                raise
            if item is None:
                return
            try:
                await asyncio.to_thread(self._process_and_checkpoint, item)
            except Exception:
                self._degraded = True
                self._logger.exception(
                    "Alert evaluation failed for event_id=%s", item.id
                )

    def _catch_up(self) -> None:
        checkpoint = self._checkpoints.get(self.consumer_name)
        occurred_after: datetime | None = None
        cursor: TimelineCursor | None = None
        if checkpoint and checkpoint.last_occurred_at and checkpoint.last_event_id:
            occurred_after = checkpoint.last_occurred_at - timedelta(
                seconds=self.replay_overlap_seconds
            )
            # Start after checkpoint via cursor for exact ordering.
            cursor = TimelineCursor(
                occurred_at=checkpoint.last_occurred_at,
                event_id=checkpoint.last_event_id,
            )

        remaining = self.startup_catchup_limit
        while remaining > 0:
            limit = min(100, remaining)
            page = self._timeline.list_timeline(
                occurred_after=occurred_after if cursor is None else None,
                event_type=list(SOURCE_EVENT_TYPES_FOR_EVAL),
                limit=limit,
                cursor=encode_cursor(cursor.occurred_at, cursor.event_id)
                if cursor is not None
                else None,
                sort="asc",
            )
            if not page.items:
                break
            for event in page.items:
                self._process_and_checkpoint(event)
            last = page.items[-1]
            cursor = TimelineCursor(
                occurred_at=last.occurred_at, event_id=last.id
            )
            remaining -= len(page.items)
            if page.next_cursor is None:
                break
            # Continue with opaque next cursor path
            cursor = decode_cursor(page.next_cursor)

    def _process_and_checkpoint(self, event: TimelineEvent) -> None:
        if event.event_type.value in ALERT_EVENT_TYPES:
            return
        self._eval.process_source_event(event)
        self._checkpoints.save(
            self.consumer_name,
            last_occurred_at=event.occurred_at,
            last_event_id=event.id,
        )
