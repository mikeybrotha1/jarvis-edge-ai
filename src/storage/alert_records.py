"""Domain records for durable alerts (v0.8.0)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from storage.alert_orm import (
    AlertRuleType,
    AlertSeverity,
    AlertStatus,
    EvaluatorStateKind,
)


@dataclass(frozen=True, slots=True)
class AlertRuleRecord:
    id: UUID
    name: str
    rule_type: AlertRuleType
    enabled: bool
    source_event_types: list[str]
    camera_ids: list[str]
    zone_ids: list[str]
    entity_types: list[str]
    occupancy_threshold: int | None
    occupancy_duration_seconds: int | None
    dwell_threshold_seconds: int | None
    active_window_start: str | None
    active_window_end: str | None
    timezone: str
    days_of_week: list[int]
    cooldown_seconds: int
    severity: AlertSeverity
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AlertRuleCreate:
    name: str
    rule_type: AlertRuleType
    source_event_types: list[str] = field(default_factory=list)
    enabled: bool = True
    camera_ids: list[str] = field(default_factory=list)
    zone_ids: list[str] = field(default_factory=list)
    entity_types: list[str] = field(default_factory=list)
    occupancy_threshold: int | None = None
    occupancy_duration_seconds: int | None = None
    dwell_threshold_seconds: int | None = None
    active_window_start: str | None = None
    active_window_end: str | None = None
    timezone: str = "UTC"
    days_of_week: list[int] = field(default_factory=list)
    cooldown_seconds: int = 60
    severity: AlertSeverity = AlertSeverity.WARNING
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AlertRuleUpdate:
    name: str | None = None
    enabled: bool | None = None
    source_event_types: list[str] | None = None
    camera_ids: list[str] | None = None
    zone_ids: list[str] | None = None
    entity_types: list[str] | None = None
    occupancy_threshold: int | None = None
    clear_occupancy_threshold: bool = False
    occupancy_duration_seconds: int | None = None
    clear_occupancy_duration_seconds: bool = False
    dwell_threshold_seconds: int | None = None
    clear_dwell_threshold_seconds: bool = False
    active_window_start: str | None = None
    clear_active_window_start: bool = False
    active_window_end: str | None = None
    clear_active_window_end: bool = False
    timezone: str | None = None
    days_of_week: list[int] | None = None
    cooldown_seconds: int | None = None
    severity: AlertSeverity | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AlertRecord:
    id: UUID
    rule_id: UUID
    status: AlertStatus
    severity: AlertSeverity
    entity_id: UUID
    zone_id: UUID | None
    camera_id: str | None
    source_event_id: str
    subject_key: str
    idempotency_key: str
    triggered_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    last_matched_at: datetime
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    rule_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EvaluatorStateRecord:
    id: UUID
    rule_id: UUID
    subject_key: str
    entity_id: UUID
    zone_id: UUID | None
    source_event_id: str
    condition_started_at: datetime
    due_at: datetime
    state: EvaluatorStateKind
    alert_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    consumer_name: str
    last_occurred_at: datetime | None
    last_event_id: str | None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AlertListFilter:
    status: AlertStatus | None = None
    rule_id: UUID | None = None
    severity: AlertSeverity | None = None
    entity_id: UUID | None = None
    zone_id: UUID | None = None
    camera_id: str | None = None
    triggered_after: datetime | None = None
    triggered_before: datetime | None = None
    limit: int = 50
    offset: int = 0
    sort: str = "desc"
