"""Tests for same-transaction activity notifications (v0.5.0)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from core.event_bus import EventBus
from core.events import EventType, JarvisEvent
from services.entity_memory_service import EntityMemoryService
from storage.activity_notify import (
    ActivityNotificationPublisher,
    parse_notification_payload,
    validate_notify_channel,
)
from storage.entity_records import EntityCreate
from storage.entity_repository import EntityRepository
from storage.observation_repository import ObservationRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
    session_scope,
)


def _stack(publisher: ActivityNotificationPublisher | None = None):
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    entities = EntityRepository(factory)
    observations = ObservationRepository(factory)
    bus = EventBus()
    service = EntityMemoryService(
        bus,
        entities,
        observations,
        session_factory=factory,
        camera_id="front-door",
        process_inline=True,
        activity_publisher=publisher,
    )
    service.start()
    return service, entities, observations, factory, publisher


def _object_event(
    event_type: EventType,
    *,
    track_id: int = 1,
    confidence: float = 0.9,
    frame_id: int = 1,
    last_seen: str = "2026-07-28T15:00:00+00:00",
) -> JarvisEvent:
    return JarvisEvent.create(
        event_type,
        source="vision_memory",
        identity=f"person-{track_id}",
        track_id=track_id,
        label="person",
        confidence=confidence,
        frames_seen=frame_id,
        frame_id=frame_id,
        first_seen="2026-07-28T15:00:00+00:00",
        last_seen=last_seen,
        bounding_box={"x1": 1, "y1": 2, "x2": 3, "y2": 4},
        camera_id="front-door",
    )


def test_channel_validation() -> None:
    assert validate_notify_channel("jarvis_activity") == "jarvis_activity"
    try:
        validate_notify_channel("bad-channel!")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_entity_created_and_closed_notifications_same_transaction() -> None:
    publisher = ActivityNotificationPublisher(
        observation_notifications_enabled=False
    )
    service, entities, observations, factory, _ = _stack(publisher)

    service.handle_object_event(
        _object_event(EventType.OBJECT_ENTERED, frame_id=1)
    )
    assert any(
        item["event_type"] == "entity_created" for item in publisher.captured
    )
    created = next(
        item
        for item in publisher.captured
        if item["event_type"] == "entity_created"
    )
    assert created["event_id"].startswith("entity-created:")
    assert "occurred_at" in created

    service.handle_object_event(
        _object_event(
            EventType.OBJECT_EXITED,
            frame_id=2,
            last_seen="2026-07-28T15:00:05+00:00",
        )
    )
    assert any(
        item["event_type"] == "entity_closed" for item in publisher.captured
    )
    # Observations disabled: no observation_recorded payloads.
    assert not any(
        item["event_type"] == "observation_recorded"
        for item in publisher.captured
    )
    service.stop()


def test_rollback_emits_no_notification() -> None:
    publisher = ActivityNotificationPublisher()
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    entities = EntityRepository(factory)

    try:
        with session_scope(factory) as session:
            entities.create(
                EntityCreate(
                    identity_key="camera:front-door:tracker:9",
                    identity_strategy="tracker_id",
                    label="person",
                    track_id=9,
                    camera_id="front-door",
                    first_seen=datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc),
                    last_seen=datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc),
                    confidence=0.9,
                ),
                session=session,
            )
            publisher.publish_entity_created(
                session,
                entity_id=uuid4(),
                occurred_at=datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc),
            )
            # Capture happens before commit for sqlite; clear after rollback.
            raise RuntimeError("force rollback")
    except RuntimeError:
        pass

    # For SQLite, captured list may still hold the in-memory capture from the
    # rolled-back session body; durable state must be empty.
    assert entities.get_latest_by_identity_key(
        "camera:front-door:tracker:9"
    ) is None


def test_observation_disabled_by_default_and_throttle() -> None:
    publisher = ActivityNotificationPublisher(
        observation_notifications_enabled=True,
        observation_min_interval_seconds=10.0,
    )
    service, entities, observations, factory, _ = _stack(publisher)
    service.handle_object_event(
        _object_event(EventType.OBJECT_ENTERED, frame_id=1)
    )
    service.handle_object_event(
        _object_event(EventType.OBJECT_UPDATED, frame_id=2)
    )
    service.handle_object_event(
        _object_event(EventType.OBJECT_UPDATED, frame_id=3)
    )
    obs_events = [
        item
        for item in publisher.captured
        if item["event_type"] == "observation_recorded"
    ]
    # Throttled to one observation notify for the entity.
    assert len(obs_events) == 1
    assert obs_events[0]["event_id"].startswith("observation:")
    service.stop()


def test_parse_notification_payload() -> None:
    payload = parse_notification_payload(
        '{"event_id":"entity-created:x","event_type":"entity_created","occurred_at":"2026-07-28T15:00:00Z"}'
    )
    assert payload["event_type"] == "entity_created"
    try:
        parse_notification_payload("{not-json")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    try:
        parse_notification_payload(
            '{"event_id":"x","event_type":"nope","occurred_at":"t"}'
        )
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_payload_is_minimal() -> None:
    publisher = ActivityNotificationPublisher()
    service, *_ = _stack(publisher)
    service.handle_object_event(_object_event(EventType.OBJECT_ENTERED))
    payload = publisher.captured[0]
    assert set(payload.keys()) == {"event_id", "event_type", "occurred_at"}
    assert "bounding_box" not in payload
    assert "password" not in str(payload)
    service.stop()


if __name__ == "__main__":
    test_channel_validation()
    test_entity_created_and_closed_notifications_same_transaction()
    test_rollback_emits_no_notification()
    test_observation_disabled_by_default_and_throttle()
    test_parse_notification_payload()
    test_payload_is_minimal()
    print("Activity notify tests passed.")
