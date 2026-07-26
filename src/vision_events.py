"""Translate detector results into structured Jarvis vision events."""

from __future__ import annotations

from collections.abc import Sequence

from core.event_bus import EventBus
from core.events import EventType, JarvisEvent
from detector import Detection


def detection_to_dict(detection: Detection) -> dict:
    """Convert a detector result into the shared event schema."""

    return {
        "class_id": detection.class_id,
        "label": detection.label,
        "confidence": detection.confidence,
        "bounding_box": {
            "x1": detection.x1,
            "y1": detection.y1,
            "x2": detection.x2,
            "y2": detection.y2,
        },
    }


def create_frame_processed_event(
    detections: Sequence[Detection],
    *,
    frame_id: int,
    source: str = "azure_kinect",
    fps: float | None = None,
) -> JarvisEvent:
    """Create one structured event representing a processed frame."""

    data: dict = {
        "frame_id": frame_id,
        "detection_count": len(detections),
        "detections": [
            detection_to_dict(detection)
            for detection in detections
        ],
    }

    if fps is not None:
        data["fps"] = fps

    return JarvisEvent(
        event_type=EventType.FRAME_PROCESSED,
        source=source,
        data=data,
    )


def publish_frame_processed(
    event_bus: EventBus,
    detections: Sequence[Detection],
    *,
    frame_id: int,
    source: str = "azure_kinect",
    fps: float | None = None,
) -> JarvisEvent:
    """Create and publish a processed-frame event."""

    event = create_frame_processed_event(
        detections,
        frame_id=frame_id,
        source=source,
        fps=fps,
    )

    event_bus.publish(event)
    return event
