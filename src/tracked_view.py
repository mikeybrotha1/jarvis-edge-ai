"""Presentation-friendly views of tracked objects.

This module converts serialisable MemoryService snapshots into immutable
objects that rendering and user-interface components can consume.

It deliberately does not import MemoryService, OpenCV, or the event bus.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class TrackedRenderObject:
    """Immutable object state prepared for presentation."""

    identity: str
    label: str
    confidence: float

    x1: int
    y1: int
    x2: int
    y2: int

    frames_seen: int
    missed_frames: int

    first_seen: datetime
    last_seen: datetime
    age_seconds: float

    @property
    def bounding_box(self) -> tuple[int, int, int, int]:
        """Return the bounding box as ``(x1, y1, x2, y2)``."""

        return self.x1, self.y1, self.x2, self.y2

    @property
    def is_currently_visible(self) -> bool:
        """Return whether the object was detected in the latest frame."""

        return self.missed_frames == 0


def parse_timestamp(value: str | datetime) -> datetime:
    """Parse a timestamp and normalise it to timezone-aware UTC."""

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalised = value.strip()

        if normalised.endswith("Z"):
            normalised = normalised[:-1] + "+00:00"

        parsed = datetime.fromisoformat(normalised)
    else:
        raise TypeError(
            "Timestamp must be an ISO-8601 string or datetime"
        )

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def tracked_object_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> TrackedRenderObject:
    """Convert one MemoryService snapshot into a render object."""

    bounding_box = snapshot.get("bounding_box")

    if not isinstance(bounding_box, Mapping):
        raise TypeError("bounding_box must be a mapping")

    first_seen = parse_timestamp(snapshot["first_seen"])
    last_seen = parse_timestamp(snapshot["last_seen"])

    reference_time = (
        parse_timestamp(now)
        if now is not None
        else datetime.now(timezone.utc)
    )

    age_seconds = max(
        0.0,
        (reference_time - first_seen).total_seconds(),
    )

    x1 = int(bounding_box["x1"])
    y1 = int(bounding_box["y1"])
    x2 = int(bounding_box["x2"])
    y2 = int(bounding_box["y2"])

    if x2 <= x1 or y2 <= y1:
        raise ValueError("Tracked object has an invalid bounding box")

    confidence = float(snapshot["confidence"])

    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0 and 1")

    frames_seen = int(snapshot["frames_seen"])
    missed_frames = int(snapshot.get("missed_frames", 0))

    if frames_seen < 1:
        raise ValueError("frames_seen must be at least 1")

    if missed_frames < 0:
        raise ValueError("missed_frames cannot be negative")

    return TrackedRenderObject(
        identity=str(snapshot["identity"]),
        label=str(snapshot["label"]),
        confidence=confidence,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        frames_seen=frames_seen,
        missed_frames=missed_frames,
        first_seen=first_seen,
        last_seen=last_seen,
        age_seconds=age_seconds,
    )


def build_tracked_view(
    snapshots: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    include_missing: bool = False,
) -> list[TrackedRenderObject]:
    """Convert memory snapshots into renderer-friendly objects.

    By default, tracks that were not detected in the latest frame are excluded.
    MemoryService may retain those tracks temporarily so it can preserve their
    identities if they quickly reappear.
    """

    tracked_objects = [
        tracked_object_from_snapshot(
            snapshot,
            now=now,
        )
        for snapshot in snapshots
    ]

    if not include_missing:
        tracked_objects = [
            tracked
            for tracked in tracked_objects
            if tracked.is_currently_visible
        ]

    return sorted(
        tracked_objects,
        key=lambda tracked: tracked.identity,
    )
