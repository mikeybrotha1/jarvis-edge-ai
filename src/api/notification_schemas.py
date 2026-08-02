"""Pydantic schemas for outbound notifications (v0.9.0)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

from storage.notification_records import (
    DeliveryAttemptRecord,
    NotificationDeliveryRecord,
    NotificationTargetRecord,
)

T = TypeVar("T")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class NotificationTargetOut(BaseModel):
    id: UUID
    name: str
    channel_type: str
    url: str
    enabled: bool
    is_global: bool
    has_signing_secret: bool
    severity_filters: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_record(cls, record: NotificationTargetRecord) -> "NotificationTargetOut":
        meta = {
            k: v
            for k, v in dict(record.metadata).items()
            if not str(k).startswith("_")
        }
        return cls(
            id=record.id,
            name=record.name,
            channel_type=(
                record.channel_type.value
                if hasattr(record.channel_type, "value")
                else str(record.channel_type)
            ),
            url=record.url,
            enabled=record.enabled,
            is_global=record.is_global,
            has_signing_secret=record.has_signing_secret,
            severity_filters=list(record.severity_filters),
            metadata=meta,
            created_at=_aware(record.created_at),
            updated_at=_aware(record.updated_at),
        )


class NotificationDeliveryOut(BaseModel):
    id: UUID
    alert_id: UUID
    target_id: UUID
    target_name: str | None = None
    event_type: str
    idempotency_key: str
    status: str
    attempts: int
    next_attempt_at: datetime
    first_attempt_at: datetime | None = None
    last_attempt_at: datetime | None = None
    delivered_at: datetime | None = None
    exhausted_at: datetime | None = None
    response_status: int | None = None
    response_summary: str | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_record(
        cls, record: NotificationDeliveryRecord
    ) -> "NotificationDeliveryOut":
        return cls(
            id=record.id,
            alert_id=record.alert_id,
            target_id=record.target_id,
            target_name=record.target_name,
            event_type=record.event_type,
            idempotency_key=record.idempotency_key,
            status=(
                record.status.value
                if hasattr(record.status, "value")
                else str(record.status)
            ),
            attempts=record.attempts,
            next_attempt_at=_aware(record.next_attempt_at)
            or record.next_attempt_at,
            first_attempt_at=_aware(record.first_attempt_at),
            last_attempt_at=_aware(record.last_attempt_at),
            delivered_at=_aware(record.delivered_at),
            exhausted_at=_aware(record.exhausted_at),
            response_status=record.response_status,
            response_summary=record.response_summary,
            last_error=record.last_error,
            created_at=_aware(record.created_at),
            updated_at=_aware(record.updated_at),
        )


class DeliveryAttemptOut(BaseModel):
    id: UUID
    delivery_id: UUID
    attempt_number: int
    attempted_at: datetime
    duration_ms: float | None = None
    response_status: int | None = None
    response_body_truncated: str | None = None
    error_type: str | None = None
    error_message_sanitized: str | None = None

    @classmethod
    def from_record(cls, record: DeliveryAttemptRecord) -> "DeliveryAttemptOut":
        return cls(
            id=record.id,
            delivery_id=record.delivery_id,
            attempt_number=record.attempt_number,
            attempted_at=_aware(record.attempted_at) or record.attempted_at,
            duration_ms=record.duration_ms,
            response_status=record.response_status,
            response_body_truncated=record.response_body_truncated,
            error_type=record.error_type,
            error_message_sanitized=record.error_message_sanitized,
        )


class CollectionOut(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
