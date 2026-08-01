"""Tests for ActivityStreamBroker fan-out and back-pressure (v0.5.0)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from services.activity_stream import (
    DEFAULT_LIFECYCLE_TYPES,
    ActivityStreamBroker,
    ActivitySubscription,
)
from storage.timeline_models import TimelineEvent, TimelineEventType


def _event(
    event_type: TimelineEventType,
    *,
    entity_id=None,
    camera_id: str = "front-door",
    entity_type: str = "person",
    event_id: str | None = None,
) -> TimelineEvent:
    eid = entity_id or uuid4()
    if event_id is None:
        if event_type is TimelineEventType.ENTITY_CREATED:
            event_id = f"entity-created:{eid}"
        elif event_type is TimelineEventType.ENTITY_CLOSED:
            event_id = f"entity-closed:{eid}"
        else:
            event_id = f"observation:{uuid4()}"
    return TimelineEvent(
        id=event_id,
        event_type=event_type,
        occurred_at=datetime(2026, 7, 28, 16, 0, tzinfo=timezone.utc),
        source="entity",
        entity_id=eid,
        camera_id=camera_id,
        entity_type=entity_type,
        summary="test",
        payload={},
    )


async def _run_broker_tests() -> None:
    broker = ActivityStreamBroker(client_queue_size=3, max_connections=2)
    client = await broker.register()
    assert client.subscription.event_types == set(DEFAULT_LIFECYCLE_TYPES)

    # Default filters: observation ignored.
    await broker.publish(
        _event(TimelineEventType.OBSERVATION_RECORDED)
    )
    assert client.queue.empty()

    created = _event(TimelineEventType.ENTITY_CREATED)
    await broker.publish(created)
    msg = await client.queue.get()
    assert msg["type"] == "timeline.event"
    assert msg["event"]["event_type"] == "entity_created"

    # Subscription update with filter.
    entity = uuid4()
    updated = await broker.update_subscription(
        client.client_id,
        ActivitySubscription(
            event_types={"entity_created", "observation_recorded"},
            entity_ids={entity},
        ),
    )
    assert "observation_recorded" in updated.event_types

    await broker.publish(
        _event(TimelineEventType.ENTITY_CREATED, entity_id=uuid4())
    )
    assert client.queue.empty()
    await broker.publish(
        _event(TimelineEventType.ENTITY_CREATED, entity_id=entity)
    )
    assert not client.queue.empty()
    await client.queue.get()

    # Bounded queue: fill with observations then lifecycle; drop observation.
    await broker.update_subscription(
        client.client_id,
        ActivitySubscription(
            event_types={
                "entity_created",
                "entity_closed",
                "observation_recorded",
            }
        ),
    )
    for _ in range(3):
        await broker.publish(_event(TimelineEventType.OBSERVATION_RECORDED))
    # Queue full; publish lifecycle should drop an observation and accept.
    await broker.publish(_event(TimelineEventType.ENTITY_CLOSED))
    types = []
    while not client.queue.empty():
        item = client.queue.get_nowait()
        if item.get("type") == "timeline.event":
            types.append(item["event"]["event_type"])
        elif item.get("type") == "stream.warning":
            assert item["code"] == "events_dropped"
    assert "entity_closed" in types

    # Slow consumer: fill with lifecycle only then force close.
    while not client.queue.empty():
        client.queue.get_nowait()
    for _ in range(3):
        await broker.publish(_event(TimelineEventType.ENTITY_CREATED))
    await broker.publish(_event(TimelineEventType.ENTITY_CREATED))
    close_msg = None
    while not client.queue.empty():
        item = client.queue.get_nowait()
        if item.get("type") == "_close":
            close_msg = item
    assert close_msg is not None
    assert close_msg["code"] == 4002

    # One slow client does not block another.
    other = await broker.register()
    await broker.publish(_event(TimelineEventType.ENTITY_CREATED))
    other_msg = await asyncio.wait_for(other.queue.get(), timeout=1.0)
    assert other_msg["type"] == "timeline.event"

    await broker.unregister(client.client_id)
    await broker.unregister(other.client_id)
    await broker.close_all()


def test_broker_behaviour() -> None:
    asyncio.run(_run_broker_tests())


if __name__ == "__main__":
    test_broker_behaviour()
    print("Activity stream broker tests passed.")
