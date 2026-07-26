"""Tests for Jarvis short-term object memory."""

from __future__ import annotations

from core.event_bus import EventBus
from core.events import EventType, JarvisEvent
from services.memory_service import MemoryService


def frame_event(
    frame_id: int,
    detections: list[dict],
) -> JarvisEvent:
    return JarvisEvent.create(
        EventType.FRAME_PROCESSED,
        source="azure_kinect",
        frame_id=frame_id,
        detections=detections,
    )


def person(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    confidence: float = 0.95,
) -> dict:
    return {
        "label": "person",
        "confidence": confidence,
        "bounding_box": {
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
        },
    }


def test_new_detection_receives_identity() -> None:
    bus = EventBus()
    memory = MemoryService(bus)
    entered: list[JarvisEvent] = []

    bus.subscribe(EventType.OBJECT_ENTERED, entered.append)
    memory.start()

    bus.publish(
        frame_event(
            1,
            [person(100, 100, 300, 500)],
        )
    )

    objects = memory.active_objects()

    assert len(objects) == 1
    assert objects[0]["track_id"] == 1
    assert objects[0]["identity"] == "person-1"
    assert memory.object_count("person") == 1

    assert len(entered) == 1
    assert entered[0].data["identity"] == "person-1"


def test_overlapping_detection_keeps_identity() -> None:
    bus = EventBus()
    memory = MemoryService(bus, iou_threshold=0.25)
    memory.start()

    bus.publish(
        frame_event(
            1,
            [person(100, 100, 300, 500)],
        )
    )

    bus.publish(
        frame_event(
            2,
            [person(110, 105, 310, 505, confidence=0.91)],
        )
    )

    objects = memory.active_objects()

    assert len(objects) == 1
    assert objects[0]["track_id"] == 1
    assert objects[0]["identity"] == "person-1"
    assert objects[0]["frames_seen"] == 2
    assert objects[0]["confidence"] == 0.91


def test_two_people_receive_distinct_identities() -> None:
    bus = EventBus()
    memory = MemoryService(bus)
    memory.start()

    bus.publish(
        frame_event(
            1,
            [
                person(50, 100, 220, 500),
                person(500, 100, 700, 520),
            ],
        )
    )

    objects = memory.active_objects()

    assert len(objects) == 2
    assert objects[0]["identity"] == "person-1"
    assert objects[1]["identity"] == "person-2"


def test_missing_object_expires_after_threshold() -> None:
    bus = EventBus()
    memory = MemoryService(
        bus,
        max_missed_frames=2,
    )
    exited: list[JarvisEvent] = []

    bus.subscribe(EventType.OBJECT_EXITED, exited.append)
    memory.start()

    bus.publish(
        frame_event(
            1,
            [person(100, 100, 300, 500)],
        )
    )

    bus.publish(frame_event(2, []))
    bus.publish(frame_event(3, []))

    assert memory.object_count() == 1
    assert exited == []

    bus.publish(frame_event(4, []))

    assert memory.object_count() == 0
    assert len(exited) == 1
    assert exited[0].data["identity"] == "person-1"


def test_labels_are_not_cross_matched() -> None:
    bus = EventBus()
    memory = MemoryService(bus)
    memory.start()

    bus.publish(
        frame_event(
            1,
            [person(100, 100, 300, 500)],
        )
    )

    bus.publish(
        frame_event(
            2,
            [
                {
                    "label": "chair",
                    "confidence": 0.88,
                    "bounding_box": {
                        "x1": 105,
                        "y1": 105,
                        "x2": 305,
                        "y2": 505,
                    },
                }
            ],
        )
    )

    objects = memory.active_objects()

    assert len(objects) == 2
    assert {item["label"] for item in objects} == {
        "person",
        "chair",
    }


def test_stop_unsubscribes_memory() -> None:
    bus = EventBus()
    memory = MemoryService(bus)

    memory.start()
    memory.stop()

    bus.publish(
        frame_event(
            1,
            [person(100, 100, 300, 500)],
        )
    )

    assert memory.object_count() == 0


if __name__ == "__main__":
    test_new_detection_receives_identity()
    test_overlapping_detection_keeps_identity()
    test_two_people_receive_distinct_identities()
    test_missing_object_expires_after_threshold()
    test_labels_are_not_cross_matched()
    test_stop_unsubscribes_memory()

    print("All memory service tests passed.")
