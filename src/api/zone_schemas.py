"""Pydantic schemas for spatial zone REST API (v0.6.0)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from storage.zone_records import (
    EntityZoneSessionRecord,
    ZoneOccupancy,
    ZoneOccupancyEntity,
    ZoneRecord,
)

T = TypeVar("T")


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class VertexOut(BaseModel):
    x: float
    y: float


class ZoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    camera_id: str
    geometry_type: str
    vertices: list[VertexOut]
    enabled: bool
    entity_type_filters: list[str] = Field(default_factory=list)
    min_confidence: float | None = None
    position_strategy: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_record(cls, record: ZoneRecord) -> "ZoneOut":
        return cls(
            id=record.id,
            name=record.name,
            camera_id=record.camera_id,
            geometry_type=record.geometry_type,
            vertices=[
                VertexOut(x=float(v["x"]), y=float(v["y"]))
                for v in record.vertices
            ],
            enabled=record.enabled,
            entity_type_filters=list(record.entity_type_filters),
            min_confidence=record.min_confidence,
            position_strategy=record.position_strategy,
            metadata=dict(record.metadata),
            created_at=(
                _ensure_aware(record.created_at)
                if record.created_at
                else None
            ),
            updated_at=(
                _ensure_aware(record.updated_at)
                if record.updated_at
                else None
            ),
        )


class ZoneCreateIn(BaseModel):
    name: str
    camera_id: str
    x_min: float | None = None
    y_min: float | None = None
    x_max: float | None = None
    y_max: float | None = None
    vertices: list[dict[str, float]] | None = None
    enabled: bool = True
    entity_type_filters: list[str] = Field(default_factory=list)
    min_confidence: float | None = None
    position_strategy: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _trim_name(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("name is required")
        return text

    @field_validator("camera_id")
    @classmethod
    def _trim_camera(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("camera_id is required")
        return text


class ZonePatchIn(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    entity_type_filters: list[str] | None = None
    min_confidence: float | None = None
    clear_min_confidence: bool = False
    position_strategy: str | None = None
    clear_position_strategy: bool = False
    x_min: float | None = None
    y_min: float | None = None
    x_max: float | None = None
    y_max: float | None = None
    vertices: list[dict[str, float]] | None = None
    metadata: dict[str, Any] | None = None


class ZoneOccupancyEntityOut(BaseModel):
    entity_id: UUID
    entity_type: str
    label: str
    camera_id: str | None = None
    status: str
    session_id: UUID
    entered_at: datetime
    last_seen_at: datetime
    dwell_seconds: float
    average_confidence: float | None = None
    track_id: int | None = None

    @classmethod
    def from_record(cls, record: ZoneOccupancyEntity) -> "ZoneOccupancyEntityOut":
        return cls(
            entity_id=record.entity_id,
            entity_type=record.entity_type,
            label=record.label,
            camera_id=record.camera_id,
            status=record.status,
            session_id=record.session_id,
            entered_at=_ensure_aware(record.entered_at),
            last_seen_at=_ensure_aware(record.last_seen_at),
            dwell_seconds=record.dwell_seconds,
            average_confidence=record.average_confidence,
            track_id=record.track_id,
        )


class ZoneOccupancyOut(BaseModel):
    zone_id: UUID
    zone_name: str
    camera_id: str
    occupancy: int
    entities: list[ZoneOccupancyEntityOut]
    updated_at: datetime

    @classmethod
    def from_record(cls, record: ZoneOccupancy) -> "ZoneOccupancyOut":
        return cls(
            zone_id=record.zone_id,
            zone_name=record.zone_name,
            camera_id=record.camera_id,
            occupancy=record.occupancy,
            entities=[
                ZoneOccupancyEntityOut.from_record(item)
                for item in record.entities
            ],
            updated_at=_ensure_aware(record.updated_at),
        )


class ZoneSessionOut(BaseModel):
    id: UUID
    zone_id: UUID
    zone_name: str | None = None
    entity_id: UUID
    camera_id: str
    entered_at: datetime
    last_seen_at: datetime
    exited_at: datetime | None = None
    status: str
    dwell_seconds: float
    entry_event_id: str
    exit_event_id: str | None = None

    @classmethod
    def from_record(cls, record: EntityZoneSessionRecord) -> "ZoneSessionOut":
        now = datetime.now(timezone.utc)
        return cls(
            id=record.id,
            zone_id=record.zone_id,
            zone_name=record.zone_name,
            entity_id=record.entity_id,
            camera_id=record.camera_id,
            entered_at=_ensure_aware(record.entered_at),
            last_seen_at=_ensure_aware(record.last_seen_at),
            exited_at=(
                _ensure_aware(record.exited_at) if record.exited_at else None
            ),
            status=record.status.value,
            dwell_seconds=record.dwell_seconds(now=now),
            entry_event_id=record.entry_event_id,
            exit_event_id=record.exit_event_id,
        )


class CollectionOut(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
