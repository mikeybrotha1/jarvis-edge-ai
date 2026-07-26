"""Tests for the Jarvis structured event system."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core.event_bus import EventBus
from core.events import EventType, JarvisEvent
from services.event_logger import JsonlEventLogger


def test_event_delivery() -> None:
    bus = EventBus()
    received: list[JarvisEvent] = []

    bus.subscribe(EventType.OBJECT_DETECTED, received.append)

    event = JarvisEvent.create(
        EventType.OBJECT_DETECTED,
        source="office_camera",
        label="person",
        confidence=0.97,
    )

    bus.publish(event)

    assert received == [event]
    assert event.data["label"] == "person"
    assert event.data["confidence"] == 0.97


def test_global_subscription() -> None:
    bus = EventBus()
    received: list[JarvisEvent] = []

    bus.subscribe(None, received.append)

    bus.publish(
        JarvisEvent.create(
            EventType.SYSTEM_STARTED,
            source="jarvis",
        )
    )

    bus.publish(
        JarvisEvent.create(
            EventType.CAMERA_OPENED,
            source="azure_kinect",
        )
    )

    assert len(received) == 2


def test_handler_failure_is_isolated() -> None:
    bus = EventBus()
    successful_events: list[JarvisEvent] = []

    def broken_handler(event: JarvisEvent) -> None:
        raise RuntimeError("Intentional test failure")

    bus.subscribe(EventType.SYSTEM_STARTED, broken_handler)
    bus.subscribe(EventType.SYSTEM_STARTED, successful_events.append)

    event = JarvisEvent.create(
        EventType.SYSTEM_STARTED,
        source="jarvis",
    )

    bus.publish(event)

    assert successful_events == [event]


def test_jsonl_event_logger() -> None:
    bus = EventBus()

    with tempfile.TemporaryDirectory() as temp_directory:
        event_path = Path(temp_directory) / "events.jsonl"
        event_logger = JsonlEventLogger(event_path)

        bus.subscribe(None, event_logger.handle)

        event = JarvisEvent.create(
            EventType.OBJECT_DETECTED,
            source="office_camera",
            label="person",
            confidence=0.91,
            bounding_box={
                "x1": 100,
                "y1": 80,
                "x2": 400,
                "y2": 650,
            },
        )

        bus.publish(event)

        records = [
            json.loads(line)
            for line in event_path.read_text(encoding="utf-8").splitlines()
        ]

        assert len(records) == 1
        assert records[0]["event_type"] == "vision.object_detected"
        assert records[0]["source"] == "office_camera"
        assert records[0]["data"]["label"] == "person"


if __name__ == "__main__":
    test_event_delivery()
    test_global_subscription()
    test_handler_failure_is_isolated()
    test_jsonl_event_logger()

    print("All event bus tests passed.")
