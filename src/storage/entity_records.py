"""Immutable domain records for persistent entity memory.

These dataclasses form the repository boundary so services and tests do not
depend on live SQLAlchemy identity maps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from .entity_orm import EntityStatus


@dataclass(frozen=True, slots=True)
class EntityRecord:
    """Current aggregate state for one identity-keyed entity."""

    id: UUID
    identity_key: str
    identity_strategy: str
    label: str
    track_id: int | None
    camera_id: str | None
    first_seen: datetime
    last_seen: datetime
    times_seen: int
    average_confidence: float
    status: EntityStatus
    last_bounding_box: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_event_data(self) -> dict[str, Any]:
        """Serialisable payload for ENTITY_* bus events."""

        return {
            "entity_id": str(self.id),
            "identity_key": self.identity_key,
            "identity_strategy": self.identity_strategy,
            "label": self.label,
            "track_id": self.track_id,
            "camera_id": self.camera_id,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "times_seen": self.times_seen,
            "average_confidence": self.average_confidence,
            "status": self.status.value,
            "bounding_box": self.last_bounding_box,
        }


@dataclass(frozen=True, slots=True)
class ObservationCreate:
    """Input fields for recording one entity observation."""

    entity_id: UUID
    observed_at: datetime
    camera_id: str
    confidence: float
    label: str
    source_event_type: str
    bounding_box: dict[str, Any] | None = None
    frame_number: int | None = None
    track_id: int | None = None
    source_event_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    """Persisted observation row."""

    id: UUID
    entity_id: UUID
    observed_at: datetime
    camera_id: str
    confidence: float
    label: str
    source_event_type: str
    bounding_box: dict[str, Any] | None = None
    frame_number: int | None = None
    track_id: int | None = None
    source_event_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    """Persisted entity-state snapshot."""

    id: UUID
    entity_id: UUID
    snapshot_at: datetime
    reason: str
    identity_key: str
    identity_strategy: str
    label: str
    track_id: int | None
    camera_id: str | None
    first_seen: datetime
    last_seen: datetime
    times_seen: int
    average_confidence: float
    status: EntityStatus
    bounding_box: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EntityCreate:
    """Fields required to insert a new entity row."""

    identity_key: str
    identity_strategy: str
    label: str
    track_id: int | None
    camera_id: str | None
    first_seen: datetime
    last_seen: datetime
    confidence: float
    bounding_box: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EntityUpdate:
    """Fields applied when an existing entity is observed again."""

    last_seen: datetime
    confidence: float
    label: str
    track_id: int | None = None
    camera_id: str | None = None
    bounding_box: dict[str, Any] | None = None
    reopen: bool = False
