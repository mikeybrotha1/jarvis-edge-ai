"""Repository for immutable entity observations.

Purpose
-------
Append and query per-observation history for tracked entities.

Duplicate protection
--------------------
``source_event_id`` is unique when present. Replaying the same vision event
returns the existing observation and does not create a second row.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .entity_orm import EntityObservation
from .entity_records import ObservationCreate, ObservationRecord
from .sqlalchemy_db import session_scope

T = TypeVar("T")


class ObservationRepository:
    """Append-only store for entity observations."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
    ) -> None:
        self._session_factory = session_factory

    def append(
        self,
        data: ObservationCreate,
        *,
        session: Session | None = None,
    ) -> tuple[ObservationRecord, bool]:
        """Insert one observation.

        Returns
        -------
        (record, created)
            ``created`` is False when ``source_event_id`` already exists.
        """

        self._validate(data)

        def _write(active: Session) -> tuple[ObservationRecord, bool]:
            if data.source_event_id:
                existing = self._get_by_source_event_id(
                    active,
                    data.source_event_id,
                )
                if existing is not None:
                    return existing, False

            row = EntityObservation(
                id=uuid.uuid4(),
                entity_id=data.entity_id,
                observed_at=data.observed_at,
                camera_id=data.camera_id,
                confidence=float(data.confidence),
                bounding_box=data.bounding_box,
                frame_number=data.frame_number,
                label=data.label,
                track_id=data.track_id,
                source_event_type=data.source_event_type,
                source_event_id=data.source_event_id,
                payload=dict(data.payload),
            )
            active.add(row)
            active.flush()
            return self._to_record(row), True

        return self._with_session(session, _write)

    def has_source_event(
        self,
        source_event_id: str,
        *,
        session: Session | None = None,
    ) -> bool:
        """Return True when an observation already recorded this event id."""

        if not source_event_id.strip():
            return False

        def _read(active: Session) -> bool:
            return (
                self._get_by_source_event_id(active, source_event_id)
                is not None
            )

        return self._with_session(session, _read)

    def list_for_entity(
        self,
        entity_id: UUID,
        *,
        session: Session | None = None,
    ) -> list[ObservationRecord]:
        """Return observations for one entity ordered by time."""

        def _read(active: Session) -> list[ObservationRecord]:
            statement = (
                select(EntityObservation)
                .where(EntityObservation.entity_id == entity_id)
                .order_by(EntityObservation.observed_at.asc())
            )
            return [
                self._to_record(row)
                for row in active.scalars(statement).all()
            ]

        return self._with_session(session, _read)

    def count_for_entity(
        self,
        entity_id: UUID,
        *,
        session: Session | None = None,
    ) -> int:
        """Return the number of stored observations for one entity."""

        def _read(active: Session) -> int:
            statement = (
                select(func.count())
                .select_from(EntityObservation)
                .where(EntityObservation.entity_id == entity_id)
            )
            return int(active.scalar(statement) or 0)

        return self._with_session(session, _read)

    def _get_by_source_event_id(
        self,
        session: Session,
        source_event_id: str,
    ) -> ObservationRecord | None:
        statement = (
            select(EntityObservation)
            .where(EntityObservation.source_event_id == source_event_id)
            .limit(1)
        )
        row = session.scalars(statement).first()
        if row is None:
            return None
        return self._to_record(row)

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
    def _validate(data: ObservationCreate) -> None:
        if not data.camera_id.strip():
            raise ValueError("camera_id cannot be empty.")

        if not data.label.strip():
            raise ValueError("label cannot be empty.")

        if not data.source_event_type.strip():
            raise ValueError("source_event_type cannot be empty.")

        if not 0.0 <= float(data.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1.")

        if data.frame_number is not None and data.frame_number < 0:
            raise ValueError("frame_number cannot be negative.")

    @staticmethod
    def _to_record(row: EntityObservation) -> ObservationRecord:
        return ObservationRecord(
            id=row.id,
            entity_id=row.entity_id,
            observed_at=row.observed_at,
            camera_id=row.camera_id,
            confidence=row.confidence,
            label=row.label,
            source_event_type=row.source_event_type,
            bounding_box=row.bounding_box,
            frame_number=row.frame_number,
            track_id=row.track_id,
            source_event_id=row.source_event_id,
            payload=dict(row.payload or {}),
        )
