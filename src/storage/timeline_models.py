"""Domain models for the read-only entity activity timeline (v0.4.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID


class TimelineEventType(str, Enum):
    """Derived timeline event kinds (not stored as a table)."""

    ENTITY_CREATED = "entity_created"
    ENTITY_CLOSED = "entity_closed"
    OBSERVATION_RECORDED = "observation_recorded"
    ZONE_ENTERED = "zone_entered"
    ZONE_EXITED = "zone_exited"
    ZONE_OCCUPANCY_CHANGED = "zone_occupancy_changed"


DEFAULT_TIMELINE_EVENT_TYPES: tuple[TimelineEventType, ...] = (
    TimelineEventType.ENTITY_CREATED,
    TimelineEventType.ENTITY_CLOSED,
    TimelineEventType.ZONE_ENTERED,
    TimelineEventType.ZONE_EXITED,
    TimelineEventType.ZONE_OCCUPANCY_CHANGED,
)

ALL_TIMELINE_EVENT_TYPES: frozenset[str] = frozenset(
    item.value for item in TimelineEventType
)

SPATIAL_TIMELINE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        TimelineEventType.ZONE_ENTERED.value,
        TimelineEventType.ZONE_EXITED.value,
        TimelineEventType.ZONE_OCCUPANCY_CHANGED.value,
    }
)


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    """One projected timeline event."""

    id: str
    event_type: TimelineEventType
    occurred_at: datetime
    source: str
    entity_id: UUID
    camera_id: str | None
    entity_type: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TimelineCursor:
    """Decoded cursor position (opaque on the wire)."""

    occurred_at: datetime
    event_id: str


@dataclass(frozen=True, slots=True)
class TimelineListFilter:
    """Server-side filters for timeline queries."""

    event_types: tuple[TimelineEventType, ...]
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None
    entity_id: UUID | None = None
    camera_id: str | None = None
    entity_type: str | None = None
    zone_id: UUID | None = None
    limit: int = 50
    cursor: TimelineCursor | None = None
    sort: str = "desc"


@dataclass(frozen=True, slots=True)
class TimelinePage:
    """Cursor-paginated timeline page (no total count)."""

    items: list[TimelineEvent]
    limit: int
    next_cursor: str | None
