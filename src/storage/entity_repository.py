"""Repository for persistent entity aggregate state.

Purpose
-------
Keep every entity / snapshot SQL operation behind one repository boundary.

Mirrors the VisionRepository style: validation at the boundary, no event
subscription, and optional shared-session composition for transactional work.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .entity_orm import Entity, EntitySnapshot, EntityStatus
from .entity_records import (
    EntityCreate,
    EntityRecord,
    EntityUpdate,
    SnapshotRecord,
)
from .sqlalchemy_db import session_scope

T = TypeVar("T")


class EntityRepository:
    """Read and write entity aggregate rows and snapshots."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
    ) -> None:
        self._session_factory = session_factory

    def create(
        self,
        data: EntityCreate,
        *,
        session: Session | None = None,
    ) -> EntityRecord:
        """Insert a new active entity and return its record."""

        return self._with_session(
            session,
            lambda active: self._create(active, data),
        )

    def get_by_id(
        self,
        entity_id: UUID,
        *,
        session: Session | None = None,
    ) -> EntityRecord | None:
        """Return one entity by primary key."""

        def _read(active: Session) -> EntityRecord | None:
            entity = active.get(Entity, entity_id)
            if entity is None:
                return None
            return self._to_entity_record(entity)

        return self._with_session(session, _read)

    def get_active_by_identity_key(
        self,
        identity_key: str,
        *,
        session: Session | None = None,
    ) -> EntityRecord | None:
        """Return the active entity for an identity key, if any."""

        if not identity_key.strip():
            raise ValueError("identity_key cannot be empty.")

        def _read(active: Session) -> EntityRecord | None:
            statement = (
                select(Entity)
                .where(Entity.identity_key == identity_key)
                .where(Entity.status == EntityStatus.ACTIVE)
                .order_by(Entity.last_seen.desc())
                .limit(1)
            )
            entity = active.scalars(statement).first()
            if entity is None:
                return None
            return self._to_entity_record(entity)

        return self._with_session(session, _read)

    def get_latest_by_identity_key(
        self,
        identity_key: str,
        *,
        session: Session | None = None,
    ) -> EntityRecord | None:
        """Return the most recently seen entity for an identity key."""

        if not identity_key.strip():
            raise ValueError("identity_key cannot be empty.")

        def _read(active: Session) -> EntityRecord | None:
            statement = (
                select(Entity)
                .where(Entity.identity_key == identity_key)
                .order_by(Entity.last_seen.desc())
                .limit(1)
            )
            entity = active.scalars(statement).first()
            if entity is None:
                return None
            return self._to_entity_record(entity)

        return self._with_session(session, _read)

    def apply_observation(
        self,
        entity_id: UUID,
        update: EntityUpdate,
        *,
        session: Session | None = None,
    ) -> EntityRecord:
        """Update aggregate counters after a new observation.

        ``times_seen`` is incremented and ``average_confidence`` is maintained
        as a running mean. When ``reopen`` is true a closed entity becomes
        active again (new appearance of the same identity key).
        """

        self._validate_confidence(update.confidence, field_name="confidence")

        if not update.label.strip():
            raise ValueError("label cannot be empty.")

        def _write(active: Session) -> EntityRecord:
            entity = active.get(Entity, entity_id)
            if entity is None:
                raise LookupError(f"Entity not found: {entity_id}")

            previous_times = entity.times_seen
            previous_average = entity.average_confidence
            new_times = previous_times + 1
            new_average = (
                (previous_average * previous_times) + float(update.confidence)
            ) / new_times

            entity.times_seen = new_times
            entity.average_confidence = new_average
            entity.last_seen = update.last_seen
            entity.label = update.label

            if update.track_id is not None:
                entity.track_id = update.track_id

            if update.camera_id is not None:
                entity.camera_id = update.camera_id

            if update.bounding_box is not None:
                entity.last_bounding_box = update.bounding_box

            if update.reopen or entity.status is EntityStatus.ACTIVE:
                entity.status = EntityStatus.ACTIVE

            active.flush()
            return self._to_entity_record(entity)

        return self._with_session(session, _write)

    def close(
        self,
        entity_id: UUID,
        *,
        last_seen: datetime | None = None,
        bounding_box: dict[str, Any] | None = None,
        session: Session | None = None,
    ) -> EntityRecord:
        """Mark an entity closed without re-counting observations."""

        def _write(active: Session) -> EntityRecord:
            entity = active.get(Entity, entity_id)
            if entity is None:
                raise LookupError(f"Entity not found: {entity_id}")

            if last_seen is not None:
                entity.last_seen = last_seen

            entity.status = EntityStatus.CLOSED

            if bounding_box is not None:
                entity.last_bounding_box = bounding_box

            active.flush()
            return self._to_entity_record(entity)

        return self._with_session(session, _write)

    def create_snapshot(
        self,
        entity: EntityRecord,
        *,
        reason: str,
        snapshot_at: datetime | None = None,
        session: Session | None = None,
    ) -> SnapshotRecord:
        """Persist a point-in-time copy of entity aggregate state."""

        if not reason.strip():
            raise ValueError("reason cannot be empty.")

        at = snapshot_at or entity.last_seen

        def _write(active: Session) -> SnapshotRecord:
            row = EntitySnapshot(
                id=uuid.uuid4(),
                entity_id=entity.id,
                snapshot_at=at,
                reason=reason,
                identity_key=entity.identity_key,
                identity_strategy=entity.identity_strategy,
                label=entity.label,
                track_id=entity.track_id,
                camera_id=entity.camera_id,
                first_seen=entity.first_seen,
                last_seen=entity.last_seen,
                times_seen=entity.times_seen,
                average_confidence=entity.average_confidence,
                status=entity.status,
                bounding_box=entity.last_bounding_box,
                extra=dict(entity.extra),
            )
            active.add(row)
            active.flush()
            return self._to_snapshot_record(row)

        return self._with_session(session, _write)

    def list_snapshots(
        self,
        entity_id: UUID,
        *,
        session: Session | None = None,
    ) -> list[SnapshotRecord]:
        """Return snapshots for one entity ordered by snapshot time."""

        def _read(active: Session) -> list[SnapshotRecord]:
            statement = (
                select(EntitySnapshot)
                .where(EntitySnapshot.entity_id == entity_id)
                .order_by(EntitySnapshot.snapshot_at.asc())
            )
            return [
                self._to_snapshot_record(row)
                for row in active.scalars(statement).all()
            ]

        return self._with_session(session, _read)

    def _create(self, session: Session, data: EntityCreate) -> EntityRecord:
        self._validate_confidence(data.confidence, field_name="confidence")

        if not data.identity_key.strip():
            raise ValueError("identity_key cannot be empty.")

        if not data.label.strip():
            raise ValueError("label cannot be empty.")

        if data.last_seen < data.first_seen:
            raise ValueError("last_seen cannot be earlier than first_seen.")

        entity = Entity(
            id=uuid.uuid4(),
            identity_key=data.identity_key,
            identity_strategy=data.identity_strategy,
            label=data.label,
            track_id=data.track_id,
            camera_id=data.camera_id,
            first_seen=data.first_seen,
            last_seen=data.last_seen,
            times_seen=1,
            average_confidence=float(data.confidence),
            status=EntityStatus.ACTIVE,
            last_bounding_box=data.bounding_box,
            extra=dict(data.extra),
        )
        session.add(entity)
        session.flush()
        return self._to_entity_record(entity)

    def _with_session(
        self,
        session: Session | None,
        operation: Callable[[Session], T],
    ) -> T:
        if session is not None:
            return operation(session)

        with session_scope(self._session_factory) as owned:
            return operation(owned)

    @staticmethod
    def _to_entity_record(entity: Entity) -> EntityRecord:
        return EntityRecord(
            id=entity.id,
            identity_key=entity.identity_key,
            identity_strategy=entity.identity_strategy,
            label=entity.label,
            track_id=entity.track_id,
            camera_id=entity.camera_id,
            first_seen=entity.first_seen,
            last_seen=entity.last_seen,
            times_seen=entity.times_seen,
            average_confidence=entity.average_confidence,
            status=entity.status,
            last_bounding_box=entity.last_bounding_box,
            extra=dict(entity.extra or {}),
        )

    @staticmethod
    def _to_snapshot_record(row: EntitySnapshot) -> SnapshotRecord:
        return SnapshotRecord(
            id=row.id,
            entity_id=row.entity_id,
            snapshot_at=row.snapshot_at,
            reason=row.reason,
            identity_key=row.identity_key,
            identity_strategy=row.identity_strategy,
            label=row.label,
            track_id=row.track_id,
            camera_id=row.camera_id,
            first_seen=row.first_seen,
            last_seen=row.last_seen,
            times_seen=row.times_seen,
            average_confidence=row.average_confidence,
            status=row.status,
            bounding_box=row.bounding_box,
            extra=dict(row.extra or {}),
        )

    @staticmethod
    def _validate_confidence(value: float, *, field_name: str) -> None:
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"{field_name} must be between 0 and 1.")
