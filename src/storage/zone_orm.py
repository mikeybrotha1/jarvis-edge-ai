"""SQLAlchemy ORM models for spatial zones and entity-zone sessions (v0.6.0)."""

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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from storage.entity_orm import Base, PortableJSON, PortableUUID


class ZoneSessionStatus(str, enum.Enum):
    """Lifecycle status of an entity-zone session."""

    OPEN = "open"
    CLOSED = "closed"


_ZONE_SESSION_STATUS_ENUM = Enum(
    ZoneSessionStatus,
    name="zone_session_status",
    values_callable=lambda enum_cls: [item.value for item in enum_cls],
    native_enum=False,
    length=16,
)


class Zone(Base):
    """Camera-specific spatial zone with polygon-ready rectangle geometry."""

    __tablename__ = "zones"

    id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    camera_id: Mapped[str] = mapped_column(String(128), nullable=False)
    geometry_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="rectangle",
    )
    vertices: Mapped[list[dict[str, Any]]] = mapped_column(
        PortableJSON(),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    entity_type_filters: Mapped[list[str]] = mapped_column(
        PortableJSON(),
        nullable=False,
        default=list,
    )
    min_confidence: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
    )
    position_strategy: Mapped[Optional[str]] = mapped_column(
        String(32),
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

    sessions: Mapped[list["EntityZoneSession"]] = relationship(
        back_populates="zone",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "camera_id",
            "name",
            name="uq_zones_camera_id_name",
        ),
        Index("ix_zones_camera_id", "camera_id"),
        Index("ix_zones_enabled", "enabled"),
        Index("ix_zones_camera_id_enabled", "camera_id", "enabled"),
    )


class EntityZoneSession(Base):
    """Durable entity dwell session inside one zone."""

    __tablename__ = "entity_zone_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    zone_id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        ForeignKey("zones.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        PortableUUID(),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    camera_id: Mapped[str] = mapped_column(String(128), nullable=False)
    entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    exited_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[ZoneSessionStatus] = mapped_column(
        _ZONE_SESSION_STATUS_ENUM,
        nullable=False,
        default=ZoneSessionStatus.OPEN,
    )
    entry_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    exit_event_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
    )
    occupancy_after_enter: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    occupancy_after_exit: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
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

    zone: Mapped[Zone] = relationship(back_populates="sessions")

    __table_args__ = (
        Index("ix_ezs_zone_id_status", "zone_id", "status"),
        Index("ix_ezs_entity_id_status", "entity_id", "status"),
        Index("ix_ezs_camera_id_status", "camera_id", "status"),
        Index("ix_ezs_zone_id_entered_at", "zone_id", "entered_at"),
        Index("ix_ezs_entity_id_entered_at", "entity_id", "entered_at"),
        Index("ix_ezs_entered_at_id", "entered_at", "id"),
        Index("ix_ezs_exited_at_id", "exited_at", "id"),
        # Partial unique: at most one open session per zone+entity.
        # PostgreSQL and SQLite both support partial unique indexes.
        Index(
            "uq_ezs_open_zone_entity",
            "zone_id",
            "entity_id",
            unique=True,
            sqlite_where=text("status = 'open'"),
            postgresql_where=text("status = 'open'"),
        ),
    )
