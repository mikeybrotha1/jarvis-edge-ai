"""Listener unit tests for the activity stream (v0.5.0)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from services.activity_listener import ActivityNotificationListener
from services.activity_stream import ActivityStreamBroker
from services.timeline_service import TimelineNotFoundError
from storage.timeline_models import TimelineEvent, TimelineEventType


class _FakeTimeline:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.events: dict[str, TimelineEvent] = {}

    def get_event(self, event_id: str) -> TimelineEvent:
        self.calls.append(event_id)
        if event_id not in self.events:
            raise TimelineNotFoundError(event_id)
        return self.events[event_id]


async def _run() -> None:
    entity_id = uuid4()
    event_id = f"entity-created:{entity_id}"
    event = TimelineEvent(
        id=event_id,
        event_type=TimelineEventType.ENTITY_CREATED,
        occurred_at=datetime(2026, 7, 28, 18, 0, tzinfo=timezone.utc),
        source="entity",
        entity_id=entity_id,
        camera_id="front-door",
        entity_type="person",
        summary="Person appeared on front-door",
        payload={},
    )
    timeline = _FakeTimeline()
    timeline.events[event_id] = event
    broker = ActivityStreamBroker(client_queue_size=10, max_connections=5)
    client = await broker.register()

    listener = ActivityNotificationListener(
        database_url="sqlite+pysqlite:///:memory:",
        channel="jarvis_activity",
        timeline_service=timeline,  # type: ignore[arg-type]
        broker=broker,
    )

    # Malformed payload rejected without resolution.
    await listener._handle_notify("{bad")
    assert timeline.calls == []

    # One resolution per notification, then fan-out.
    payload = (
        '{"event_id":"%s","event_type":"entity_created",'
        '"occurred_at":"2026-07-28T18:00:00Z"}' % event_id
    )
    await listener._handle_notify(payload)
    assert timeline.calls == [event_id]
    msg = await asyncio.wait_for(client.queue.get(), timeout=1.0)
    assert msg["type"] == "timeline.event"
    assert msg["event"]["id"] == event_id

    # Second client still sees the same single resolution path.
    client2 = await broker.register()
    await listener._handle_notify(payload)
    assert timeline.calls == [event_id, event_id]
    msg2 = await asyncio.wait_for(client2.queue.get(), timeout=1.0)
    assert msg2["event"]["id"] == event_id

    # SQLite start marks ready and clean stop leaves no task.
    await listener.start()
    assert await listener.wait_until_ready(timeout=2.0) is True
    await listener.stop()
    assert listener._task is None


def test_listener_unit_behaviour() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    test_listener_unit_behaviour()
    print("Activity listener tests passed.")
