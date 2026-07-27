"""Database record models for Jarvis persistent vision storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class VisionRunRecord:
    """One execution of the Jarvis vision pipeline."""

    run_id: UUID
    hostname: str
    camera_source: str
    started_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IdentityEventRecord:
    """One immutable object lifecycle event."""

    run_id: UUID
    identity: str
    track_id: int
    label: str
    event_type: str
    observed_at: datetime
    confidence: float
    frames_seen: int
    bounding_box: dict[str, Any] | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IdentitySessionRecord:
    """Current accumulated state for one identity during one vision run."""

    run_id: UUID
    identity: str
    track_id: int
    label: str
    first_seen: datetime
    last_seen: datetime
    appearance_count: int
    total_frames_seen: int
    highest_confidence: float
    last_confidence: float
    active: bool
    last_bounding_box: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class FrameMetricRecord:
    """Performance and detection metrics for one processed frame."""

    run_id: UUID
    frame_id: int
    observed_at: datetime
    detection_count: int
    fps: float | None = None
    processing_ms: float | None = None
