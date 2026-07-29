"""Tests for Jarvis persistent entity memory (v0.4.0)."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from uuid import UUID

from core.event_bus import EventBus
from core.events import EventType, JarvisEvent
from core.identity import ObservationContext, TrackerIdIdentityMatcher
from services.entity_memory_service import EntityMemoryService
from storage.entity_orm import EntityStatus
from storage.entity_repository import EntityRepository
from storage.observation_repository import ObservationRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)

CAMERA = "test_camera"


def identity_key(track_id: int, camera_id: str = CAMERA) -> str:
    return f"camera:{camera_id}:tracker:{track_id}"


def _build_stack(db_url: str = "sqlite+pysqlite:///:memory:"):
    engine = create_entity_engine(db_url)
    create_entity_schema(engine)
    session_factory = create_session_factory(engine)
    entities = EntityRepository(session_factory)
    observations = ObservationRepository(session_factory)
    bus = EventBus()
    service = EntityMemoryService(
        bus,
        entities,
        observations,
        session_factory=session_factory,
        identity_matcher=TrackerIdIdentityMatcher(),
        camera_id=CAMERA,
        process_inline=True,
    )
    return bus, service, entities, observations, engine, session_factory


def object_event(
    event_type: EventType,
    *,
    track_id: int = 1,
    label: str = "person",
    confidence: float = 0.90,
    frame_id: int = 1,
    event_id: str | None = None,
    last_seen: str = "2026-07-27T12:00:00+00:00",
    camera_id: str | None = None,
) -> JarvisEvent:
    event = JarvisEvent.create(
        event_type,
        source="vision_memory",
        identity=f"{label}-{track_id}",
        track_id=track_id,
        label=label,
        confidence=confidence,
        frames_seen=frame_id,
        frame_id=frame_id,
        first_seen="2026-07-27T12:00:00+00:00",
        last_seen=last_seen,
        bounding_box={"x1": 10, "y1": 20, "x2": 100, "y2": 200},
        frame_source=camera_id or CAMERA,
        camera_id=camera_id or CAMERA,
    )
    if event_id is not None:
        return JarvisEvent(
            event_type=event.event_type,
            source=event.source,
            data=event.data,
            timestamp=event.timestamp,
            event_id=event_id,
            version=event.version,
        )
    return event


def test_entity_creation_on_entered() -> None:
    bus, service, entities, observations, *_ = _build_stack()
    created: list[JarvisEvent] = []
    bus.subscribe(EventType.ENTITY_CREATED, created.append)
    service.start()

    bus.publish(object_event(EventType.OBJECT_ENTERED, confidence=0.87))

    assert len(created) == 1
    payload = created[0].data
    assert payload["identity_key"] == identity_key(1)
    assert payload["identity_strategy"] == "tracker_id"
    assert payload["times_seen"] == 1
    assert payload["average_confidence"] == 0.87
    assert payload["status"] == "active"
    assert payload["camera_id"] == CAMERA

    entity = entities.get_active_by_identity_key(identity_key(1))
    assert entity is not None
    assert observations.count_for_entity(entity.id) == 1
    snapshots = entities.list_snapshots(entity.id)
    assert len(snapshots) == 1
    assert snapshots[0].reason == "created"

    service.stop()


def test_repeated_observations_update_counters() -> None:
    bus, service, entities, observations, *_ = _build_stack()
    updated: list[JarvisEvent] = []
    bus.subscribe(EventType.ENTITY_UPDATED, updated.append)
    service.start()

    bus.publish(
        object_event(
            EventType.OBJECT_ENTERED,
            confidence=0.80,
            frame_id=1,
        )
    )
    bus.publish(
        object_event(
            EventType.OBJECT_UPDATED,
            confidence=1.00,
            frame_id=2,
        )
    )
    bus.publish(
        object_event(
            EventType.OBJECT_UPDATED,
            confidence=0.60,
            frame_id=3,
        )
    )

    entity = entities.get_active_by_identity_key(identity_key(1))
    assert entity is not None
    assert entity.times_seen == 3
    assert abs(entity.average_confidence - 0.80) < 1e-9
    assert observations.count_for_entity(entity.id) == 3
    assert len(updated) == 2
    assert len(entities.list_snapshots(entity.id)) == 3

    service.stop()


def test_entity_exit_closes_and_publishes() -> None:
    bus, service, entities, observations, *_ = _build_stack()
    closed: list[JarvisEvent] = []
    bus.subscribe(EventType.ENTITY_CLOSED, closed.append)
    service.start()

    bus.publish(object_event(EventType.OBJECT_ENTERED, frame_id=1))
    bus.publish(
        object_event(
            EventType.OBJECT_EXITED,
            confidence=0.70,
            frame_id=5,
        )
    )

    assert len(closed) == 1
    assert closed[0].data["status"] == "closed"
    assert closed[0].data["times_seen"] == 2

    entity = entities.get_latest_by_identity_key(identity_key(1))
    assert entity is not None
    assert entity.status is EntityStatus.CLOSED
    assert entities.get_active_by_identity_key(identity_key(1)) is None
    assert observations.count_for_entity(entity.id) == 2

    reasons = [snap.reason for snap in entities.list_snapshots(entity.id)]
    assert reasons == ["created", "closed"]

    service.stop()


def test_multiple_tracker_ids_are_independent() -> None:
    bus, service, entities, *_ = _build_stack()
    service.start()

    bus.publish(object_event(EventType.OBJECT_ENTERED, track_id=1))
    bus.publish(
        object_event(
            EventType.OBJECT_ENTERED,
            track_id=2,
            label="car",
            confidence=0.55,
        )
    )
    bus.publish(
        object_event(
            EventType.OBJECT_UPDATED,
            track_id=1,
            confidence=0.99,
            frame_id=2,
        )
    )

    person = entities.get_active_by_identity_key(identity_key(1))
    car = entities.get_active_by_identity_key(identity_key(2))
    assert person is not None
    assert car is not None
    assert person.times_seen == 2
    assert car.times_seen == 1
    assert car.label == "car"
    assert person.identity_key != car.identity_key

    service.stop()


def test_same_tracker_id_on_different_cameras_are_independent() -> None:
    bus, service, entities, *_ = _build_stack()
    service.start()

    bus.publish(
        object_event(
            EventType.OBJECT_ENTERED,
            track_id=1,
            camera_id="cam_a",
        )
    )
    bus.publish(
        object_event(
            EventType.OBJECT_ENTERED,
            track_id=1,
            camera_id="cam_b",
            confidence=0.70,
        )
    )

    a = entities.get_active_by_identity_key(identity_key(1, "cam_a"))
    b = entities.get_active_by_identity_key(identity_key(1, "cam_b"))
    assert a is not None
    assert b is not None
    assert a.id != b.id
    assert a.identity_key == "camera:cam_a:tracker:1"
    assert b.identity_key == "camera:cam_b:tracker:1"

    service.stop()


def test_duplicate_events_do_not_corrupt_counters() -> None:
    bus, service, entities, observations, *_ = _build_stack()
    created: list[JarvisEvent] = []
    bus.subscribe(EventType.ENTITY_CREATED, created.append)
    service.start()

    event = object_event(
        EventType.OBJECT_ENTERED,
        event_id="fixed-event-1",
        confidence=0.91,
    )
    bus.publish(event)
    bus.publish(event)
    bus.publish(event)

    entity = entities.get_active_by_identity_key(identity_key(1))
    assert entity is not None
    assert entity.times_seen == 1
    assert entity.average_confidence == 0.91
    assert observations.count_for_entity(entity.id) == 1
    assert len(created) == 1

    service.stop()


def test_out_of_order_update_after_close_does_not_reopen() -> None:
    bus, service, entities, observations, *_ = _build_stack()
    service.start()

    bus.publish(object_event(EventType.OBJECT_ENTERED, frame_id=1))
    bus.publish(object_event(EventType.OBJECT_EXITED, frame_id=2))

    closed = entities.get_latest_by_identity_key(identity_key(1))
    assert closed is not None
    assert closed.status is EntityStatus.CLOSED
    times_before = closed.times_seen

    bus.publish(
        object_event(
            EventType.OBJECT_UPDATED,
            confidence=0.11,
            frame_id=3,
        )
    )

    after = entities.get_latest_by_identity_key(identity_key(1))
    assert after is not None
    assert after.status is EntityStatus.CLOSED
    assert after.times_seen == times_before
    assert after.average_confidence == closed.average_confidence
    # Late update is still recorded as an observation for audit history.
    assert observations.count_for_entity(after.id) == times_before + 1

    service.stop()


def test_restart_persistence_survives_service_stop() -> None:
    db_path = Path("/tmp/jarvis_entity_memory_restart.sqlite3")
    if db_path.exists():
        db_path.unlink()

    url = f"sqlite+pysqlite:///{db_path}"
    bus1, service1, *_ = _build_stack(url)
    service1.start()
    bus1.publish(object_event(EventType.OBJECT_ENTERED, confidence=0.77))
    bus1.publish(
        object_event(
            EventType.OBJECT_UPDATED,
            confidence=0.88,
            frame_id=2,
        )
    )
    service1.stop()

    bus2, service2, entities2, observations2, *_ = _build_stack(url)
    service2.start()

    entity = entities2.get_active_by_identity_key(identity_key(1))
    assert entity is not None
    assert entity.times_seen == 2
    assert abs(entity.average_confidence - 0.825) < 1e-9
    assert observations2.count_for_entity(entity.id) == 2

    service2.stop()
    db_path.unlink(missing_ok=True)


def test_database_failure_rolls_back_transaction() -> None:
    bus, service, entities, *_ = _build_stack()
    service.start()

    original_snapshot = entities.create_snapshot

    def boom(*args, **kwargs):
        raise RuntimeError("simulated snapshot failure")

    entities.create_snapshot = boom  # type: ignore[method-assign]

    failed = False
    try:
        service.handle_object_event(object_event(EventType.OBJECT_ENTERED))
    except RuntimeError:
        failed = True

    assert failed is True
    assert entities.get_active_by_identity_key(identity_key(1)) is None
    assert entities.get_latest_by_identity_key(identity_key(1)) is None

    entities.create_snapshot = original_snapshot  # type: ignore[method-assign]
    service.stop()


def test_handler_does_not_block_event_bus() -> None:
    bus, _, entities, observations, _, session_factory = _build_stack()
    service = EntityMemoryService(
        bus,
        entities,
        observations,
        session_factory=session_factory,
        camera_id=CAMERA,
        process_inline=False,
    )

    original_create = entities.create

    def slow_create(*args, **kwargs):
        time.sleep(0.25)
        return original_create(*args, **kwargs)

    entities.create = slow_create  # type: ignore[method-assign]
    service.start()

    started = time.perf_counter()
    bus.publish(object_event(EventType.OBJECT_ENTERED))
    elapsed = time.perf_counter() - started

    assert elapsed < 0.10

    service.flush(timeout=2.0)
    entity = entities.get_active_by_identity_key(identity_key(1))
    assert entity is not None

    service.stop()


def test_start_stop_unsubscribes_cleanly() -> None:
    bus, service, *_ = _build_stack()
    created: list[JarvisEvent] = []
    bus.subscribe(EventType.ENTITY_CREATED, created.append)

    service.start()
    service.start()  # idempotent
    assert service.is_running is True

    service.stop()
    service.stop()  # idempotent
    assert service.is_running is False

    bus.publish(object_event(EventType.OBJECT_ENTERED))
    assert created == []


def test_new_enter_after_close_creates_new_entity() -> None:
    """CLOSED entities are never reopened; a new ENTER creates a new row."""

    bus, service, entities, *_ = _build_stack()
    created: list[JarvisEvent] = []
    bus.subscribe(EventType.ENTITY_CREATED, created.append)
    service.start()

    bus.publish(object_event(EventType.OBJECT_ENTERED, frame_id=1))
    bus.publish(object_event(EventType.OBJECT_EXITED, frame_id=2))

    first = entities.get_latest_by_identity_key(identity_key(1))
    assert first is not None
    assert first.status is EntityStatus.CLOSED
    first_id = first.id

    bus.publish(
        object_event(
            EventType.OBJECT_ENTERED,
            confidence=0.50,
            frame_id=3,
            last_seen="2026-07-27T12:05:00+00:00",
        )
    )

    active = entities.get_active_by_identity_key(identity_key(1))
    assert active is not None
    assert active.status is EntityStatus.ACTIVE
    assert active.id != first_id
    assert active.times_seen == 1
    # New appearance starts its own first_seen clock.
    assert active.first_seen.replace(tzinfo=None) == datetime(
        2026, 7, 27, 12, 5
    )
    assert len(created) == 2

    closed_again = entities.get_by_id(first_id)
    assert closed_again is not None
    assert closed_again.status is EntityStatus.CLOSED
    assert closed_again.times_seen == 2

    service.stop()


def test_tracker_identity_matcher_key_format() -> None:
    matcher = TrackerIdIdentityMatcher()

    match = matcher.match(
        ObservationContext(
            track_id=42,
            label="person",
            confidence=0.9,
            camera_id="azure_kinect",
        )
    )
    assert match.identity_key == "camera:azure_kinect:tracker:42"
    assert match.strategy == "tracker_id"
    assert match.track_id == 42


def test_tracker_identity_matcher_requires_camera_id() -> None:
    matcher = TrackerIdIdentityMatcher()
    try:
        matcher.match(
            ObservationContext(
                track_id=1,
                label="person",
                confidence=0.9,
                camera_id=None,
            )
        )
        raise AssertionError("expected ValueError for missing camera_id")
    except ValueError as error:
        assert "camera_id" in str(error)


def test_observation_contains_required_fields() -> None:
    bus, service, entities, observations, *_ = _build_stack()
    service.start()

    bus.publish(
        object_event(
            EventType.OBJECT_ENTERED,
            confidence=0.66,
            frame_id=99,
        )
    )

    entity = entities.get_active_by_identity_key(identity_key(1))
    assert entity is not None
    rows = observations.list_for_entity(entity.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.camera_id == CAMERA
    assert row.confidence == 0.66
    assert row.frame_number == 99
    assert row.bounding_box == {
        "x1": 10,
        "y1": 20,
        "x2": 100,
        "y2": 200,
    }
    assert row.source_event_id is not None
    UUID(str(entity.id))  # valid UUID

    service.stop()


if __name__ == "__main__":
    test_entity_creation_on_entered()
    test_repeated_observations_update_counters()
    test_entity_exit_closes_and_publishes()
    test_multiple_tracker_ids_are_independent()
    test_same_tracker_id_on_different_cameras_are_independent()
    test_duplicate_events_do_not_corrupt_counters()
    test_out_of_order_update_after_close_does_not_reopen()
    test_restart_persistence_survives_service_stop()
    test_database_failure_rolls_back_transaction()
    test_handler_does_not_block_event_bus()
    test_start_stop_unsubscribes_cleanly()
    test_new_enter_after_close_creates_new_entity()
    test_tracker_identity_matcher_key_format()
    test_tracker_identity_matcher_requires_camera_id()
    test_observation_contains_required_fields()
    print("All entity memory service tests passed.")
