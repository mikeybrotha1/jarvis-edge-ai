"""Integration tests for detector events and short-term memory."""

from __future__ import annotations

from core.event_bus import EventBus
from core.events import EventType, JarvisEvent
from detector import Detection
from services.memory_service import MemoryService
from vision_events import (
    create_frame_processed_event,
    detection_to_dict,
    publish_frame_processed,
)


def make_person(
    x1: int = 100,
    y1: int = 80,
    x2: int = 350,
    y2: int = 650,
    confidence: float = 0.96,
) -> Detection:
    return Detection(
        class_id=0,
        label="person",
        confidence=confidence,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
    )


def test_detection_converts_to_event_schema() -> None:
    detection = make_person()

    converted = detection_to_dict(detection)

    assert converted == {
        "class_id": 0,
        "label": "person",
        "confidence": 0.96,
        "bounding_box": {
            "x1": 100,
            "y1": 80,
            "x2": 350,
            "y2": 650,
        },
    }


def test_frame_event_contains_all_detections() -> None:
    detections = [
        make_person(),
        Detection(
            class_id=56,
            label="chair",
            confidence=0.88,
            x1=500,
            y1=300,
            x2=720,
            y2=700,
        ),
    ]

    event = create_frame_processed_event(
        detections,
        frame_id=42,
        source="test_camera",
        fps=18.5,
    )

    assert event.event_type == EventType.FRAME_PROCESSED
    assert event.source == "test_camera"
    assert event.data["frame_id"] == 42
    assert event.data["detection_count"] == 2
    assert event.data["fps"] == 18.5
    assert event.data["detections"][0]["label"] == "person"
    assert event.data["detections"][1]["label"] == "chair"


def test_published_detection_reaches_memory_service() -> None:
    bus = EventBus()
    memory = MemoryService(bus)
    entered: list[JarvisEvent] = []

    bus.subscribe(EventType.OBJECT_ENTERED, entered.append)
    memory.start()

    publish_frame_processed(
        bus,
        [make_person()],
        frame_id=1,
    )

    objects = memory.active_objects()

    assert len(objects) == 1
    assert objects[0]["identity"] == "person-1"
    assert objects[0]["frames_seen"] == 1

    assert len(entered) == 1
    assert entered[0].data["identity"] == "person-1"


def test_identity_persists_across_live_style_frames() -> None:
    bus = EventBus()
    memory = MemoryService(
        bus,
        iou_threshold=0.25,
    )
    updated: list[JarvisEvent] = []

    bus.subscribe(EventType.OBJECT_UPDATED, updated.append)
    memory.start()

    publish_frame_processed(
        bus,
        [make_person()],
        frame_id=1,
    )

    publish_frame_processed(
        bus,
        [
            make_person(
                x1=110,
                y1=84,
                x2=360,
                y2=654,
                confidence=0.93,
            )
        ],
        frame_id=2,
    )

    objects = memory.active_objects()

    assert len(objects) == 1
    assert objects[0]["identity"] == "person-1"
    assert objects[0]["frames_seen"] == 2
    assert objects[0]["confidence"] == 0.93

    assert len(updated) == 1
    assert updated[0].data["identity"] == "person-1"


def test_empty_frame_advances_missing_state() -> None:
    bus = EventBus()
    memory = MemoryService(
        bus,
        max_missed_frames=2,
    )
    memory.start()

    publish_frame_processed(
        bus,
        [make_person()],
        frame_id=1,
    )

    publish_frame_processed(
        bus,
        [],
        frame_id=2,
    )

    objects = memory.active_objects()

    assert len(objects) == 1
    assert objects[0]["identity"] == "person-1"
    assert objects[0]["missed_frames"] == 1


if __name__ == "__main__":
    test_detection_converts_to_event_schema()
    test_frame_event_contains_all_detections()
    test_published_detection_reaches_memory_service()
    test_identity_persists_across_live_style_frames()
    test_empty_frame_advances_missing_state()

    print("All vision-memory integration tests passed.")
