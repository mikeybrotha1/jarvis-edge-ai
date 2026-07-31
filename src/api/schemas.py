"""Pydantic response and collection schemas for the entity query API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from storage.entity_orm import EntityStatus
from storage.entity_records import EntityRecord, ObservationRecord, PageResult

T = TypeVar("T")


def _ensure_aware(value: datetime) -> datetime:
    """Return a timezone-aware datetime (UTC when naive)."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class EntityOut(BaseModel):
    """Serialised entity aggregate."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    identity_key: str
    identity_strategy: str
    entity_type: str = Field(
        description="Detector label (person, car, …).",
    )
    label: str
    track_id: int | None = None
    camera_id: str | None = None
    first_seen: datetime
    last_seen: datetime
    times_seen: int
    average_confidence: float
    status: EntityStatus
    bounding_box: dict[str, Any] | None = None

    @classmethod
    def from_record(cls, record: EntityRecord) -> "EntityOut":
        return cls(
            id=record.id,
            identity_key=record.identity_key,
            identity_strategy=record.identity_strategy,
            entity_type=record.label,
            label=record.label,
            track_id=record.track_id,
            camera_id=record.camera_id,
            first_seen=_ensure_aware(record.first_seen),
            last_seen=_ensure_aware(record.last_seen),
            times_seen=record.times_seen,
            average_confidence=record.average_confidence,
            status=record.status,
            bounding_box=record.last_bounding_box,
        )


class ObservationOut(BaseModel):
    """Serialised observation row."""

    model_config = ConfigDict(from_attributes=True)

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

    @classmethod
    def from_record(cls, record: ObservationRecord) -> "ObservationOut":
        return cls(
            id=record.id,
            entity_id=record.entity_id,
            observed_at=_ensure_aware(record.observed_at),
            camera_id=record.camera_id,
            confidence=record.confidence,
            label=record.label,
            source_event_type=record.source_event_type,
            bounding_box=record.bounding_box,
            frame_number=record.frame_number,
            track_id=record.track_id,
        )


class CollectionOut(BaseModel, Generic[T]):
    """Paginated collection envelope."""

    items: list[T]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_page(
        cls,
        page: PageResult,
        *,
        map_item: Any,
    ) -> "CollectionOut[Any]":
        return cls(
            items=[map_item(item) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )


class HealthOut(BaseModel):
    """Liveness response."""

    status: str = "ok"
    service: str = "jarvis-entity-query-api"
