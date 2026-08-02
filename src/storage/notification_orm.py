"""SQLAlchemy ORM for outbound notification delivery (v0.9.0)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from storage.entity_orm import Base, PortableJSON, PortableUUID


class NotificationChannelType(str, enum.Enum):
    WEBHOOK = "webhook"


class DeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DELIVERED = "delivered"
    FAILED = "failed"  # retry scheduled (next_attempt_at set)
    EXHAUSTED = "exhausted"  # terminal after max attempts or non-retryable


_CHANNEL = Enum(
    NotificationChannelType,
    name="notification_channel_type",
    values_callable=lambda e: [i.value for i in e],
    native_enum=False,
    length=32,
)
_DELIVERY_STATUS = Enum(
    DeliveryStatus,
    name="notification_delivery_status",
    values_callable=lambda e: [i.value for i in e],
    native_enum=False,
    length=16,
)


class NotificationTarget(Base):
    __tablename__ = "notification_targets"

    id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    channel_type: Mapped[NotificationChannelType] = mapped_column(
        _CHANNEL, nullable=False, default=NotificationChannelType.WEBHOOK
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_global: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    signing_secret_encrypted: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    severity_filters: Mapped[list[str]] = mapped_column(
        PortableJSON(), nullable=False, default=list
    )
    extra: Mapped[dict[str, Any]] = mapped_column(
        PortableJSON(), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_notification_targets_enabled", "enabled"),
        Index("ix_notification_targets_channel_type", "channel_type"),
        Index("ix_notification_targets_is_global", "is_global"),
    )


class RuleNotificationTarget(Base):
    __tablename__ = "rule_notification_targets"

    id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(), primary_key=True, default=uuid.uuid4
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        ForeignKey("alert_rules.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        ForeignKey("notification_targets.id", ondelete="CASCADE"),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "rule_id", "target_id", name="uq_rule_notification_targets"
        ),
        Index("ix_rnt_rule_id", "rule_id"),
        Index("ix_rnt_target_id", "target_id"),
    )


class NotificationDelivery(Base):
    """One logical delivery per alert + target + event_type (outbox row)."""

    __tablename__ = "notification_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(), primary_key=True, default=uuid.uuid4
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        ForeignKey("alerts.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        ForeignKey("notification_targets.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        PortableJSON(), nullable=False, default=dict
    )
    status: Mapped[DeliveryStatus] = mapped_column(
        _DELIVERY_STATUS, nullable=False, default=DeliveryStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    locked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    locked_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    first_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    exhausted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    response_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_summary: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )
    last_error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_notification_deliveries_idempotency"
        ),
        Index("ix_nd_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_nd_alert_id", "alert_id"),
        Index("ix_nd_target_id", "target_id"),
        Index("ix_nd_locked_at", "locked_at"),
    )


class NotificationDeliveryAttempt(Base):
    __tablename__ = "notification_delivery_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(), primary_key=True, default=uuid.uuid4
    )
    delivery_id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        ForeignKey("notification_deliveries.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    duration_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    response_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_body_truncated: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )
    error_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message_sanitized: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )

    __table_args__ = (
        Index("ix_nda_delivery_attempt", "delivery_id", "attempt_number"),
    )
