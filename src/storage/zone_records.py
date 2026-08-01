"""Immutable domain records for spatial zones and sessions (v0.6.0)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from storage.zone_orm import ZoneSessionStatus


@dataclass(frozen=True, slots=True)
class ZoneRecord:
    """Persisted camera-specific zone."""

    id: UUID
    name: str
    camera_id: str
    geometry_type: str
    vertices: list[dict[str, float]]
    enabled: bool
    entity_type_filters: list[str] = field(default_factory=list)
    min_confidence: float | None = None
    position_strategy: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ZoneCreate:
    """Fields required to insert a new zone."""

    name: str
    camera_id: str
    vertices: list[dict[str, float]]
    geometry_type: str = "rectangle"
    enabled: bool = True
    entity_type_filters: list[str] = field(default_factory=list)
    min_confidence: float | None = None
    position_strategy: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ZoneUpdate:
    """Partial update fields for a zone (PATCH)."""

    name: str | None = None
    enabled: bool | None = None
    entity_type_filters: list[str] | None = None
    min_confidence: float | None = None
    clear_min_confidence: bool = False
    position_strategy: str | None = None
    clear_position_strategy: bool = False
    vertices: list[dict[str, float]] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ZoneListFilter:
    """Server-side filters for zone collection queries."""

    camera_id: str | None = None
    enabled: bool | None = None
    limit: int = 50
    offset: int = 0
    sort: str = "asc"  # name asc|desc


@dataclass(frozen=True, slots=True)
class EntityZoneSessionRecord:
    """Persisted entity-zone dwell session."""

    id: UUID
    zone_id: UUID
    entity_id: UUID
    camera_id: str
    entered_at: datetime
    last_seen_at: datetime
    exited_at: datetime | None
    status: ZoneSessionStatus
    entry_event_id: str
    exit_event_id: str | None = None
    occupancy_after_enter: int = 1
    occupancy_after_exit: int | None = None
    zone_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def dwell_seconds(self, *, now: datetime | None = None) -> float:
        """Compute dwell from entered_at to exited_at or now."""

        end = self.exited_at
        if end is None:
            end = now or datetime.now(timezone.utc)
        start = self.entered_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return max(0.0, (end - start).total_seconds())


@dataclass(frozen=True, slots=True)
class SessionListFilter:
    """Filters for entity-zone session queries."""

    zone_id: UUID | None = None
    entity_id: UUID | None = None
    camera_id: str | None = None
    status: ZoneSessionStatus | None = None
    entered_after: datetime | None = None
    entered_before: datetime | None = None
    limit: int = 50
    offset: int = 0
    sort: str = "desc"  # entered_at direction


@dataclass(frozen=True, slots=True)
class ZoneOccupancyEntity:
    """One entity currently occupying a zone."""

    entity_id: UUID
    entity_type: str
    label: str
    camera_id: str | None
    status: str
    session_id: UUID
    entered_at: datetime
    last_seen_at: datetime
    dwell_seconds: float
    average_confidence: float | None = None
    track_id: int | None = None


@dataclass(frozen=True, slots=True)
class ZoneOccupancy:
    """Current occupancy snapshot for one zone."""

    zone_id: UUID
    zone_name: str
    camera_id: str
    occupancy: int
    entities: list[ZoneOccupancyEntity]
    updated_at: datetime
