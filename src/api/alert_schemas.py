"""Pydantic schemas for alert rules and alerts (v0.8.0)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

from storage.alert_records import AlertRecord, AlertRuleRecord

T = TypeVar("T")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class AlertRuleOut(BaseModel):
    id: UUID
    name: str
    rule_type: str
    enabled: bool
    source_event_types: list[str]
    camera_ids: list[str]
    zone_ids: list[str]
    entity_types: list[str]
    occupancy_threshold: int | None = None
    occupancy_duration_seconds: int | None = None
    dwell_threshold_seconds: int | None = None
    active_window_start: str | None = None
    active_window_end: str | None = None
    timezone: str
    days_of_week: list[int]
    cooldown_seconds: int
    severity: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_record(cls, record: AlertRuleRecord) -> "AlertRuleOut":
        return cls(
            id=record.id,
            name=record.name,
            rule_type=record.rule_type.value,
            enabled=record.enabled,
            source_event_types=list(record.source_event_types),
            camera_ids=list(record.camera_ids),
            zone_ids=list(record.zone_ids),
            entity_types=list(record.entity_types),
            occupancy_threshold=record.occupancy_threshold,
            occupancy_duration_seconds=record.occupancy_duration_seconds,
            dwell_threshold_seconds=record.dwell_threshold_seconds,
            active_window_start=record.active_window_start,
            active_window_end=record.active_window_end,
            timezone=record.timezone,
            days_of_week=list(record.days_of_week),
            cooldown_seconds=record.cooldown_seconds,
            severity=record.severity.value,
            metadata=dict(record.metadata),
            created_at=_aware(record.created_at),
            updated_at=_aware(record.updated_at),
        )


class AlertOut(BaseModel):
    id: UUID
    rule_id: UUID
    rule_name: str | None = None
    status: str
    severity: str
    entity_id: UUID
    zone_id: UUID | None = None
    camera_id: str | None = None
    source_event_id: str
    subject_key: str
    triggered_at: datetime
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    last_matched_at: datetime
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_record(cls, record: AlertRecord) -> "AlertOut":
        return cls(
            id=record.id,
            rule_id=record.rule_id,
            rule_name=record.rule_name,
            status=record.status.value,
            severity=record.severity.value,
            entity_id=record.entity_id,
            zone_id=record.zone_id,
            camera_id=record.camera_id,
            source_event_id=record.source_event_id,
            subject_key=record.subject_key,
            triggered_at=_aware(record.triggered_at) or record.triggered_at,
            acknowledged_at=_aware(record.acknowledged_at),
            resolved_at=_aware(record.resolved_at),
            last_matched_at=_aware(record.last_matched_at)
            or record.last_matched_at,
            summary=record.summary,
            payload=dict(record.payload),
        )


class CollectionOut(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
