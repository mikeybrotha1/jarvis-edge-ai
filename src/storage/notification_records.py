"""Domain records for outbound notifications (v0.9.0)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from storage.notification_orm import DeliveryStatus, NotificationChannelType


@dataclass(frozen=True, slots=True)
class NotificationTargetRecord:
    id: UUID
    name: str
    channel_type: NotificationChannelType
    url: str
    enabled: bool
    is_global: bool
    has_signing_secret: bool
    severity_filters: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NotificationTargetCreate:
    name: str
    url: str
    enabled: bool = True
    is_global: bool = False
    signing_secret: str | None = None
    severity_filters: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    channel_type: NotificationChannelType = NotificationChannelType.WEBHOOK


@dataclass(frozen=True, slots=True)
class NotificationTargetUpdate:
    name: str | None = None
    url: str | None = None
    enabled: bool | None = None
    is_global: bool | None = None
    signing_secret: str | None = None
    clear_signing_secret: bool = False
    severity_filters: list[str] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class NotificationDeliveryRecord:
    id: UUID
    alert_id: UUID
    target_id: UUID
    event_type: str
    idempotency_key: str
    status: DeliveryStatus
    attempts: int
    next_attempt_at: datetime
    locked_at: datetime | None
    locked_by: str | None
    first_attempt_at: datetime | None
    last_attempt_at: datetime | None
    delivered_at: datetime | None
    exhausted_at: datetime | None
    response_status: int | None
    response_summary: str | None
    last_error: str | None
    payload: dict[str, Any] = field(default_factory=dict)
    target_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DeliveryAttemptRecord:
    id: UUID
    delivery_id: UUID
    attempt_number: int
    attempted_at: datetime
    duration_ms: float | None
    response_status: int | None
    response_body_truncated: str | None
    error_type: str | None
    error_message_sanitized: str | None


@dataclass(frozen=True, slots=True)
class DeliveryListFilter:
    status: DeliveryStatus | None = None
    alert_id: UUID | None = None
    target_id: UUID | None = None
    rule_id: UUID | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    limit: int = 50
    offset: int = 0
    sort: str = "desc"
