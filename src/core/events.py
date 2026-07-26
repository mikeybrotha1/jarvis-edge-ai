"""Structured event definitions for Jarvis Edge AI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class EventType(str, Enum):
    """Event categories shared across Jarvis components."""

    SYSTEM_STARTED = "system.started"
    SYSTEM_STOPPED = "system.stopped"
    SYSTEM_ERROR = "system.error"

    CAMERA_OPENED = "camera.opened"
    CAMERA_CLOSED = "camera.closed"
    CAMERA_ERROR = "camera.error"

    FRAME_PROCESSED = "vision.frame_processed"
    OBJECT_DETECTED = "vision.object_detected"
    OBJECT_ENTERED = "vision.object_entered"
    OBJECT_EXITED = "vision.object_exited"

    SCREENSHOT_SAVED = "media.screenshot_saved"

    METRIC_RECORDED = "metrics.recorded"

    COMMAND_RECEIVED = "assistant.command_received"
    RESPONSE_GENERATED = "assistant.response_generated"


@dataclass(frozen=True, slots=True)
class JarvisEvent:
    """An immutable event exchanged between Jarvis components."""

    event_type: EventType
    source: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    event_id: str = field(default_factory=lambda: str(uuid4()))
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""

        event = asdict(self)
        event["event_type"] = self.event_type.value
        return event

    @classmethod
    def create(
        cls,
        event_type: EventType,
        source: str,
        **data: Any,
    ) -> "JarvisEvent":
        """Convenience factory for creating an event."""

        return cls(
            event_type=event_type,
            source=source,
            data=data,
        )
