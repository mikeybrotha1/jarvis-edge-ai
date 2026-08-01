"""SQLAlchemy ORM models for persistent entity memory.

Tables
------
- entities: current aggregate state for one identity key
- entity_observations: immutable per-frame / per-event observations
- entity_snapshots: point-in-time copies of entity aggregate state
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, TypeDecorator, Uuid


class Base(DeclarativeBase):
    """Declarative base for entity-memory ORM models."""


class PortableUUID(TypeDecorator):
    """UUID type that works on PostgreSQL and SQLite test engines."""

    impl = Uuid
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return str(value) if dialect.name != "postgresql" else value
        return (
            str(value)
            if dialect.name != "postgresql"
            else uuid.UUID(str(value))
        )

    def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


class PortableJSON(TypeDecorator):
    """JSON/JSONB that falls back to generic JSON outside PostgreSQL."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class EntityStatus(str, enum.Enum):
    """Lifecycle status of a remembered entity."""

    ACTIVE = "active"
    CLOSED = "closed"


_ENTITY_STATUS_ENUM = Enum(
    EntityStatus,
    name="entity_status",
    values_callable=lambda enum_cls: [item.value for item in enum_cls],
    native_enum=False,
    length=32,
)


class Entity(Base):
    """Aggregate state for one identity-keyed tracked object."""

    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    identity_key: Mapped[str] = mapped_column(String(255), nullable=False)
    identity_strategy: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="tracker_id",
    )
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    track_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    camera_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    times_seen: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    average_confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )
    status: Mapped[EntityStatus] = mapped_column(
        _ENTITY_STATUS_ENUM,
        nullable=False,
        default=EntityStatus.ACTIVE,
    )
    last_bounding_box: Mapped[Optional[dict[str, Any]]] = mapped_column(
        PortableJSON(),
        nullable=True,
    )
    extra: Mapped[dict[str, Any]] = mapped_column(
        PortableJSON(),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    observations: Mapped[list["EntityObservation"]] = relationship(
        back_populates="entity",
        cascade="all, delete-orphan",
    )
    snapshots: Mapped[list["EntitySnapshot"]] = relationship(
        back_populates="entity",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_entities_identity_key", "identity_key"),
        Index("ix_entities_status_last_seen", "status", "last_seen"),
        Index("ix_entities_label", "label"),
        # Timeline projection access paths (v0.4.2)
        Index("ix_entities_first_seen_id", "first_seen", "id"),
        Index("ix_entities_status_last_seen_id", "status", "last_seen", "id"),
        Index("ix_entities_camera_first_seen_id", "camera_id", "first_seen", "id"),
        Index("ix_entities_camera_last_seen_id", "camera_id", "last_seen", "id"),
    )


class EntityObservation(Base):
    """One immutable observation of an entity."""

    __tablename__ = "entity_observations"

    id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    camera_id: Mapped[str] = mapped_column(String(128), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    bounding_box: Mapped[Optional[dict[str, Any]]] = mapped_column(
        PortableJSON(),
        nullable=True,
    )
    frame_number: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    track_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    source_event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_event_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        unique=True,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        PortableJSON(),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    entity: Mapped[Entity] = relationship(back_populates="observations")

    __table_args__ = (
        Index(
            "ix_entity_observations_entity_observed",
            "entity_id",
            "observed_at",
        ),
        Index(
            "ix_entity_observations_camera_observed",
            "camera_id",
            "observed_at",
        ),
        Index("ix_entity_observations_frame_number", "frame_number"),
        Index("ix_entity_observations_source_event_id", "source_event_id"),
        # Timeline projection access paths (v0.4.2)
        Index(
            "ix_entity_observations_observed_at_id",
            "observed_at",
            "id",
        ),
        Index(
            "ix_entity_observations_entity_observed_id",
            "entity_id",
            "observed_at",
            "id",
        ),
        Index(
            "ix_entity_observations_camera_observed_id",
            "camera_id",
            "observed_at",
            "id",
        ),
    )


class EntitySnapshot(Base):
    """Point-in-time copy of entity aggregate state."""

    __tablename__ = "entity_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    identity_key: Mapped[str] = mapped_column(String(255), nullable=False)
    identity_strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    track_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    camera_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    times_seen: Mapped[int] = mapped_column(Integer, nullable=False)
    average_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[EntityStatus] = mapped_column(
        _ENTITY_STATUS_ENUM,
        nullable=False,
    )
    bounding_box: Mapped[Optional[dict[str, Any]]] = mapped_column(
        PortableJSON(),
        nullable=True,
    )
    extra: Mapped[dict[str, Any]] = mapped_column(
        PortableJSON(),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    entity: Mapped[Entity] = relationship(back_populates="snapshots")

    __table_args__ = (
        Index(
            "ix_entity_snapshots_entity_snapshot_at",
            "entity_id",
            "snapshot_at",
        ),
        Index("ix_entity_snapshots_reason", "reason"),
    )
