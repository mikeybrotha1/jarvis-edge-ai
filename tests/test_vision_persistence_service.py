"""Tests for the Jarvis vision persistence service lifecycle."""

from __future__ import annotations

from core.event_bus import EventBus
from core.events import EventType, JarvisEvent
from services.vision_persistence_service import (
    VisionPersistenceService,
)
from storage.models import VisionRunRecord


class FakeVisionRepository:
    """Minimal repository double used by lifecycle tests."""

    def __init__(self) -> None:
        self.created_runs: list[VisionRunRecord] = []

    def create_run(self, record: VisionRunRecord) -> None:
        self.created_runs.append(record)


def object_event(event_type: EventType) -> JarvisEvent:
    """Create a representative object-lifecycle event."""

    return JarvisEvent.create(
        event_type,
        source="vision_memory",
        identity="person-1",
        track_id=1,
        label="person",
        confidence=0.95,
        frames_seen=1,
        first_seen="2026-07-26T12:00:00+00:00",
        last_seen="2026-07-26T12:00:00+00:00",
        bounding_box={
            "x1": 100,
            "y1": 100,
            "x2": 300,
            "y2": 500,
        },
    )


def test_start_creates_one_vision_run() -> None:
    bus = EventBus()
    repository = FakeVisionRepository()

    service = VisionPersistenceService(
        bus,
        repository,  # type: ignore[arg-type]
        camera_source="test_camera",
        hostname="test-host",
        metadata={"platform": "raspberry_pi_5"},
    )

    run_id = service.start()

    assert service.is_running is True
    assert service.run_id == run_id
    assert len(repository.created_runs) == 1

    record = repository.created_runs[0]

    assert record.run_id == run_id
    assert record.hostname == "test-host"
    assert record.camera_source == "test_camera"
    assert record.metadata == {
        "platform": "raspberry_pi_5"
    }


def test_start_is_idempotent() -> None:
    bus = EventBus()
    repository = FakeVisionRepository()

    service = VisionPersistenceService(
        bus,
        repository,  # type: ignore[arg-type]
    )

    first_run_id = service.start()
    second_run_id = service.start()

    assert first_run_id == second_run_id
    assert len(repository.created_runs) == 1


def test_service_accepts_object_lifecycle_events() -> None:
    bus = EventBus()
    repository = FakeVisionRepository()

    service = VisionPersistenceService(
        bus,
        repository,  # type: ignore[arg-type]
    )

    service.start()

    bus.publish(object_event(EventType.OBJECT_ENTERED))
    bus.publish(object_event(EventType.OBJECT_UPDATED))
    bus.publish(object_event(EventType.OBJECT_EXITED))

    assert service.is_running is True
    assert len(repository.created_runs) == 1


def test_stop_unsubscribes_service() -> None:
    bus = EventBus()
    repository = FakeVisionRepository()

    service = VisionPersistenceService(
        bus,
        repository,  # type: ignore[arg-type]
    )

    service.start()
    service.stop()
    service.stop()

    assert service.is_running is False

    bus.publish(object_event(EventType.OBJECT_ENTERED))

    assert len(repository.created_runs) == 1


if __name__ == "__main__":
    test_start_creates_one_vision_run()
    test_start_is_idempotent()
    test_service_accepts_object_lifecycle_events()
    test_stop_unsubscribes_service()

    print("All vision persistence service tests passed.")
