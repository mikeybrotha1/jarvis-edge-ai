"""SQLAlchemy ORM for durable alerts and rule evaluation (v0.8.0)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from storage.entity_orm import Base, PortableJSON, PortableUUID


class AlertRuleType(str, enum.Enum):
    EVENT_MATCH = "event_match"
    OCCUPANCY_THRESHOLD = "occupancy_threshold"
    DWELL_THRESHOLD = "dwell_threshold"


class AlertSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(str, enum.Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class EvaluatorStateKind(str, enum.Enum):
    PENDING = "pending"
    TRIGGERED = "triggered"
    CLEARED = "cleared"


_RULE_TYPE = Enum(
    AlertRuleType,
    name="alert_rule_type",
    values_callable=lambda e: [i.value for i in e],
    native_enum=False,
    length=32,
)
_SEVERITY = Enum(
    AlertSeverity,
    name="alert_severity",
    values_callable=lambda e: [i.value for i in e],
    native_enum=False,
    length=16,
)
_ALERT_STATUS = Enum(
    AlertStatus,
    name="alert_status",
    values_callable=lambda e: [i.value for i in e],
    native_enum=False,
    length=16,
)
_EVAL_STATE = Enum(
    EvaluatorStateKind,
    name="alert_evaluator_state_kind",
    values_callable=lambda e: [i.value for i in e],
    native_enum=False,
    length=16,
)


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    rule_type: Mapped[AlertRuleType] = mapped_column(_RULE_TYPE, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    source_event_types: Mapped[list[str]] = mapped_column(
        PortableJSON(), nullable=False, default=list
    )
    camera_ids: Mapped[list[str]] = mapped_column(
        PortableJSON(), nullable=False, default=list
    )
    zone_ids: Mapped[list[str]] = mapped_column(
        PortableJSON(), nullable=False, default=list
    )
    entity_types: Mapped[list[str]] = mapped_column(
        PortableJSON(), nullable=False, default=list
    )
    occupancy_threshold: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    occupancy_duration_seconds: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    dwell_threshold_seconds: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    active_window_start: Mapped[Optional[str]] = mapped_column(
        String(8), nullable=True
    )
    active_window_end: Mapped[Optional[str]] = mapped_column(
        String(8), nullable=True
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC"
    )
    days_of_week: Mapped[list[int]] = mapped_column(
        PortableJSON(), nullable=False, default=list
    )
    cooldown_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60
    )
    severity: Mapped[AlertSeverity] = mapped_column(
        _SEVERITY, nullable=False, default=AlertSeverity.WARNING
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
        Index("ix_alert_rules_enabled", "enabled"),
        Index("ix_alert_rules_rule_type", "rule_type"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(), primary_key=True, default=uuid.uuid4
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        ForeignKey("alert_rules.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[AlertStatus] = mapped_column(
        _ALERT_STATUS, nullable=False, default=AlertStatus.OPEN
    )
    severity: Mapped[AlertSeverity] = mapped_column(_SEVERITY, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    zone_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PortableUUID(),
        ForeignKey("zones.id", ondelete="SET NULL"),
        nullable=True,
    )
    camera_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    source_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(512), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    summary: Mapped[str] = mapped_column(String(512), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
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
        UniqueConstraint("idempotency_key", name="uq_alerts_idempotency_key"),
        Index("ix_alerts_status", "status"),
        Index("ix_alerts_rule_id_status", "rule_id", "status"),
        Index("ix_alerts_entity_id", "entity_id"),
        Index("ix_alerts_zone_id", "zone_id"),
        Index("ix_alerts_triggered_at_id", "triggered_at", "id"),
        Index("ix_alerts_resolved_at_id", "resolved_at", "id"),
        Index(
            "uq_alerts_open_rule_subject",
            "rule_id",
            "subject_key",
            unique=True,
            sqlite_where=text("status IN ('open', 'acknowledged')"),
            postgresql_where=text("status IN ('open', 'acknowledged')"),
        ),
    )


class AlertEvaluatorState(Base):
    __tablename__ = "alert_evaluator_state"

    id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(), primary_key=True, default=uuid.uuid4
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        ForeignKey("alert_rules.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_key: Mapped[str] = mapped_column(String(512), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    zone_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PortableUUID(), nullable=True
    )
    source_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    condition_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    state: Mapped[EvaluatorStateKind] = mapped_column(
        _EVAL_STATE, nullable=False, default=EvaluatorStateKind.PENDING
    )
    alert_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PortableUUID(), nullable=True
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
        Index("ix_aes_due_at_state", "due_at", "state"),
        Index("ix_aes_rule_id_subject_key", "rule_id", "subject_key"),
        Index("ix_aes_entity_id", "entity_id"),
        Index("ix_aes_zone_id", "zone_id"),
        UniqueConstraint(
            "rule_id",
            "subject_key",
            name="uq_aes_rule_subject",
        ),
    )


class AlertEvaluatorCheckpoint(Base):
    __tablename__ = "alert_evaluator_checkpoint"

    consumer_name: Mapped[str] = mapped_column(
        String(128), primary_key=True
    )
    last_occurred_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_event_id: Mapped[Optional[str]] = mapped_column(
        String(256), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
