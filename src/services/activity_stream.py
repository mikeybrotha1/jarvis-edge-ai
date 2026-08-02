"""In-process activity stream broker for WebSocket clients.

Resolves TimelineEvent objects once (via the listener) and fans them out to
per-client bounded queues with lifecycle-preserving drop policy.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from storage.timeline_models import TimelineEvent, TimelineEventType


DEFAULT_LIFECYCLE_TYPES: frozenset[str] = frozenset(
    {
        TimelineEventType.ENTITY_CREATED.value,
        TimelineEventType.ENTITY_CLOSED.value,
        TimelineEventType.ZONE_ENTERED.value,
        TimelineEventType.ZONE_EXITED.value,
        TimelineEventType.ZONE_OCCUPANCY_CHANGED.value,
        TimelineEventType.ALERT_TRIGGERED.value,
        TimelineEventType.ALERT_RESOLVED.value,
    }
)


@dataclass
class ActivitySubscription:
    """Per-client subscription filters.

    Filtering is AND across categories and OR within a category.
    """

    event_types: set[str] = field(
        default_factory=lambda: set(DEFAULT_LIFECYCLE_TYPES)
    )
    camera_ids: set[str] = field(default_factory=set)
    entity_ids: set[UUID] = field(default_factory=set)
    entity_types: set[str] = field(default_factory=set)
    zone_ids: set[UUID] = field(default_factory=set)
    rule_ids: set[UUID] = field(default_factory=set)
    severities: set[str] = field(default_factory=set)

    def matches(self, event: TimelineEvent) -> bool:
        if event.event_type.value not in self.event_types:
            return False
        if self.camera_ids:
            if event.camera_id is None or event.camera_id not in self.camera_ids:
                return False
        if self.entity_ids and event.entity_id not in self.entity_ids:
            return False
        if self.entity_types and event.entity_type not in self.entity_types:
            return False
        if self.zone_ids:
            zone_raw = event.payload.get("zone_id")
            if zone_raw is None:
                return False
            try:
                zone_id = UUID(str(zone_raw))
            except ValueError:
                return False
            if zone_id not in self.zone_ids:
                return False
        if self.rule_ids:
            rule_raw = event.payload.get("rule_id")
            if rule_raw is None:
                return False
            try:
                rule_id = UUID(str(rule_raw))
            except ValueError:
                return False
            if rule_id not in self.rule_ids:
                return False
        if self.severities:
            sev = event.payload.get("severity")
            if sev is None or str(sev) not in self.severities:
                return False
        return True

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "event_types": sorted(self.event_types),
            "camera_ids": sorted(self.camera_ids),
            "entity_ids": [str(item) for item in sorted(self.entity_ids, key=str)],
            "entity_types": sorted(self.entity_types),
            "zone_ids": [str(item) for item in sorted(self.zone_ids, key=str)],
            "rule_ids": [str(item) for item in sorted(self.rule_ids, key=str)],
            "severities": sorted(self.severities),
        }


@dataclass
class ActivityClient:
    """One WebSocket subscriber with a bounded outbound queue."""

    client_id: str
    queue: asyncio.Queue[dict[str, Any]]
    subscription: ActivitySubscription
    queue_size: int
    dropped_events: int = 0
    closed: bool = False


class ActivityStreamBroker:
    """Fan-out resolved timeline events to WebSocket client queues."""

    def __init__(
        self,
        *,
        client_queue_size: int = 100,
        max_connections: int = 25,
        logger: logging.Logger | None = None,
    ) -> None:
        if client_queue_size < 1:
            raise ValueError("client_queue_size must be >= 1")
        if max_connections < 1:
            raise ValueError("max_connections must be >= 1")

        self.client_queue_size = client_queue_size
        self.max_connections = max_connections
        self._logger = logger or logging.getLogger(__name__)
        self._clients: dict[str, ActivityClient] = {}
        self._lock = asyncio.Lock()

    @property
    def connection_count(self) -> int:
        return len(self._clients)

    async def register(self) -> ActivityClient:
        async with self._lock:
            if len(self._clients) >= self.max_connections:
                raise RuntimeError("maximum activity stream connections reached")
            client_id = str(uuid4())
            client = ActivityClient(
                client_id=client_id,
                queue=asyncio.Queue(maxsize=self.client_queue_size),
                subscription=ActivitySubscription(),
                queue_size=self.client_queue_size,
            )
            self._clients[client_id] = client
            return client

    async def unregister(self, client_id: str) -> None:
        async with self._lock:
            client = self._clients.pop(client_id, None)
            if client is not None:
                client.closed = True

    async def update_subscription(
        self,
        client_id: str,
        subscription: ActivitySubscription,
    ) -> ActivitySubscription:
        async with self._lock:
            client = self._clients.get(client_id)
            if client is None:
                raise KeyError(client_id)
            client.subscription = subscription
            return client.subscription

    async def publish(self, event: TimelineEvent) -> None:
        """Enqueue a resolved event for matching clients (non-blocking)."""

        async with self._lock:
            clients = list(self._clients.values())

        for client in clients:
            if client.closed:
                continue
            if not client.subscription.matches(event):
                continue
            message = {
                "type": "timeline.event",
                "event": _event_to_dict(event),
            }
            await self._enqueue(client, message)

    async def broadcast_status(self, message: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients.values())
        for client in clients:
            if not client.closed:
                await self._enqueue(client, message, prefer_drop_observation=True)

    async def close_all(self, *, code: int = 1001, reason: str = "shutdown") -> None:
        async with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.closed = True
            try:
                client.queue.put_nowait(
                    {
                        "type": "_close",
                        "code": code,
                        "reason": reason,
                    }
                )
            except asyncio.QueueFull:
                pass

    async def _enqueue(
        self,
        client: ActivityClient,
        message: dict[str, Any],
        *,
        prefer_drop_observation: bool = False,
    ) -> None:
        if client.closed:
            return

        try:
            client.queue.put_nowait(message)
            return
        except asyncio.QueueFull:
            pass

        # Drop oldest observation first so lifecycle events can still land.
        dropped = _drop_oldest_observation(client.queue)
        if dropped:
            client.dropped_events += 1
            try:
                client.queue.put_nowait(message)
            except asyncio.QueueFull:
                # Extremely full; fall through to slow-consumer handling.
                pass
            else:
                # Best-effort warning only after the real event is queued.
                warning = {
                    "type": "stream.warning",
                    "code": "events_dropped",
                    "message": (
                        "Dropped oldest observation_recorded event "
                        "due to back-pressure."
                    ),
                    "dropped_count": 1,
                    "sent_at": _now_iso(),
                }
                try:
                    client.queue.put_nowait(warning)
                except asyncio.QueueFull:
                    pass
                return

        # Still full and no observation to drop: mark slow consumer.
        client.closed = True
        client.dropped_events += 1
        try:
            client.queue.put_nowait(
                {
                    "type": "_close",
                    "code": 4002,
                    "reason": "slow consumer queue exhausted",
                }
            )
        except asyncio.QueueFull:
            # Force a drain of one item then close signal.
            try:
                client.queue.get_nowait()
                client.queue.put_nowait(
                    {
                        "type": "_close",
                        "code": 4002,
                        "reason": "slow consumer queue exhausted",
                    }
                )
            except Exception:
                pass
        self._logger.warning(
            "Closing slow activity stream client_id=%s",
            client.client_id,
        )


def _drop_oldest_observation(queue: asyncio.Queue[dict[str, Any]]) -> bool:
    """Remove the oldest observation_recorded timeline.event from the queue."""

    if queue.empty():
        return False

    items: deque[dict[str, Any]] = deque()
    removed = False
    while True:
        try:
            item = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if (
            not removed
            and item.get("type") == "timeline.event"
            and isinstance(item.get("event"), dict)
            and item["event"].get("event_type")
            == TimelineEventType.OBSERVATION_RECORDED.value
        ):
            removed = True
            continue
        items.append(item)

    for item in items:
        queue.put_nowait(item)
    return removed


def _event_to_dict(event: TimelineEvent) -> dict[str, Any]:
    occurred = event.occurred_at
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    else:
        occurred = occurred.astimezone(timezone.utc)
    return {
        "id": event.id,
        "event_type": event.event_type.value,
        "occurred_at": occurred.isoformat().replace("+00:00", "Z"),
        "source": event.source,
        "entity_id": str(event.entity_id),
        "camera_id": event.camera_id,
        "entity_type": event.entity_type,
        "summary": event.summary,
        "payload": dict(event.payload),
    }


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
