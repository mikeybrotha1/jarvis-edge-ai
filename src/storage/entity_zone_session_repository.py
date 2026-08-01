"""Repository for entity-zone dwell sessions (v0.6.0)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from storage.entity_records import PageResult
from storage.sqlalchemy_db import session_scope
from storage.zone_orm import EntityZoneSession, Zone, ZoneSessionStatus
from storage.zone_records import (
    EntityZoneSessionRecord,
    SessionListFilter,
)

T = TypeVar("T")


class EntityZoneSessionRepository:
    """Read and write entity-zone session rows."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def open_session(
        self,
        *,
        zone_id: UUID,
        entity_id: UUID,
        camera_id: str,
        entered_at: datetime,
        occupancy_after_enter: int,
        session: Session | None = None,
    ) -> EntityZoneSessionRecord:
        session_id = uuid.uuid4()
        entry_event_id = f"zone-entered:{session_id}"

        def _write(active: Session) -> EntityZoneSessionRecord:
            row = EntityZoneSession(
                id=session_id,
                zone_id=zone_id,
                entity_id=entity_id,
                camera_id=camera_id,
                entered_at=entered_at,
                last_seen_at=entered_at,
                exited_at=None,
                status=ZoneSessionStatus.OPEN,
                entry_event_id=entry_event_id,
                exit_event_id=None,
                occupancy_after_enter=int(occupancy_after_enter),
                occupancy_after_exit=None,
            )
            active.add(row)
            active.flush()
            return self._to_record(row)

        return self._with_session(session, _write)

    def close_session(
        self,
        session_id: UUID,
        *,
        exited_at: datetime,
        occupancy_after_exit: int,
        session: Session | None = None,
    ) -> EntityZoneSessionRecord:
        exit_event_id = f"zone-exited:{session_id}"

        def _write(active: Session) -> EntityZoneSessionRecord:
            row = active.get(EntityZoneSession, session_id)
            if row is None:
                raise LookupError(f"Zone session not found: {session_id}")
            if row.status is ZoneSessionStatus.CLOSED:
                return self._to_record(row)

            row.status = ZoneSessionStatus.CLOSED
            row.exited_at = exited_at
            row.last_seen_at = exited_at
            row.exit_event_id = exit_event_id
            row.occupancy_after_exit = int(occupancy_after_exit)
            active.flush()
            return self._to_record(row)

        return self._with_session(session, _write)

    def touch_session(
        self,
        session_id: UUID,
        *,
        last_seen_at: datetime,
        session: Session | None = None,
    ) -> EntityZoneSessionRecord | None:
        def _write(active: Session) -> EntityZoneSessionRecord | None:
            row = active.get(EntityZoneSession, session_id)
            if row is None or row.status is not ZoneSessionStatus.OPEN:
                return None
            row.last_seen_at = last_seen_at
            active.flush()
            return self._to_record(row)

        return self._with_session(session, _write)

    def get_open_session(
        self,
        zone_id: UUID,
        entity_id: UUID,
        *,
        session: Session | None = None,
    ) -> EntityZoneSessionRecord | None:
        def _read(active: Session) -> EntityZoneSessionRecord | None:
            statement = (
                select(EntityZoneSession)
                .where(EntityZoneSession.zone_id == zone_id)
                .where(EntityZoneSession.entity_id == entity_id)
                .where(EntityZoneSession.status == ZoneSessionStatus.OPEN)
                .limit(1)
            )
            row = active.scalars(statement).first()
            if row is None:
                return None
            return self._to_record(row)

        return self._with_session(session, _read)

    def list_open_for_entity(
        self,
        entity_id: UUID,
        *,
        session: Session | None = None,
    ) -> list[EntityZoneSessionRecord]:
        def _read(active: Session) -> list[EntityZoneSessionRecord]:
            statement = (
                select(EntityZoneSession)
                .where(EntityZoneSession.entity_id == entity_id)
                .where(EntityZoneSession.status == ZoneSessionStatus.OPEN)
            )
            return [
                self._to_record(row) for row in active.scalars(statement).all()
            ]

        return self._with_session(session, _read)

    def list_open_for_zone(
        self,
        zone_id: UUID,
        *,
        session: Session | None = None,
    ) -> list[EntityZoneSessionRecord]:
        def _read(active: Session) -> list[EntityZoneSessionRecord]:
            statement = (
                select(EntityZoneSession)
                .where(EntityZoneSession.zone_id == zone_id)
                .where(EntityZoneSession.status == ZoneSessionStatus.OPEN)
                .order_by(EntityZoneSession.entered_at.asc())
            )
            return [
                self._to_record(row) for row in active.scalars(statement).all()
            ]

        return self._with_session(session, _read)

    def count_open_for_zone(
        self,
        zone_id: UUID,
        *,
        session: Session | None = None,
    ) -> int:
        def _read(active: Session) -> int:
            statement = (
                select(func.count())
                .select_from(EntityZoneSession)
                .where(EntityZoneSession.zone_id == zone_id)
                .where(EntityZoneSession.status == ZoneSessionStatus.OPEN)
            )
            return int(active.scalar(statement) or 0)

        return self._with_session(session, _read)

    def list_stale_open(
        self,
        *,
        older_than: datetime,
        limit: int = 100,
        session: Session | None = None,
    ) -> list[EntityZoneSessionRecord]:
        def _read(active: Session) -> list[EntityZoneSessionRecord]:
            statement = (
                select(EntityZoneSession)
                .where(EntityZoneSession.status == ZoneSessionStatus.OPEN)
                .where(EntityZoneSession.last_seen_at < older_than)
                .order_by(EntityZoneSession.last_seen_at.asc())
                .limit(limit)
            )
            return [
                self._to_record(row) for row in active.scalars(statement).all()
            ]

        return self._with_session(session, _read)

    def get_by_id(
        self,
        session_id: UUID,
        *,
        session: Session | None = None,
    ) -> EntityZoneSessionRecord | None:
        def _read(active: Session) -> EntityZoneSessionRecord | None:
            row = active.get(EntityZoneSession, session_id)
            if row is None:
                return None
            return self._to_record(row, include_zone_name=True, active=active)

        return self._with_session(session, _read)

    def list_sessions(
        self,
        filters: SessionListFilter,
        *,
        session: Session | None = None,
    ) -> PageResult:
        def _read(active: Session) -> PageResult:
            conditions: list[Any] = []
            if filters.zone_id is not None:
                conditions.append(EntityZoneSession.zone_id == filters.zone_id)
            if filters.entity_id is not None:
                conditions.append(
                    EntityZoneSession.entity_id == filters.entity_id
                )
            if filters.camera_id is not None:
                conditions.append(
                    EntityZoneSession.camera_id == filters.camera_id
                )
            if filters.status is not None:
                conditions.append(EntityZoneSession.status == filters.status)
            if filters.entered_after is not None:
                conditions.append(
                    EntityZoneSession.entered_at >= filters.entered_after
                )
            if filters.entered_before is not None:
                conditions.append(
                    EntityZoneSession.entered_at <= filters.entered_before
                )

            count_statement = select(func.count()).select_from(EntityZoneSession)
            list_statement = select(EntityZoneSession)
            for condition in conditions:
                count_statement = count_statement.where(condition)
                list_statement = list_statement.where(condition)

            if filters.sort == "asc":
                list_statement = list_statement.order_by(
                    EntityZoneSession.entered_at.asc(),
                    EntityZoneSession.id.asc(),
                )
            else:
                list_statement = list_statement.order_by(
                    EntityZoneSession.entered_at.desc(),
                    EntityZoneSession.id.desc(),
                )

            list_statement = list_statement.offset(filters.offset).limit(
                filters.limit
            )
            total = int(active.scalar(count_statement) or 0)
            items = [
                self._to_record(row, include_zone_name=True, active=active)
                for row in active.scalars(list_statement).all()
            ]
            return PageResult(
                items=items,
                total=total,
                limit=filters.limit,
                offset=filters.offset,
            )

        return self._with_session(session, _read)

    def _to_record(
        self,
        row: EntityZoneSession,
        *,
        include_zone_name: bool = False,
        active: Session | None = None,
    ) -> EntityZoneSessionRecord:
        zone_name: str | None = None
        if include_zone_name and active is not None:
            zone = active.get(Zone, row.zone_id)
            if zone is not None:
                zone_name = zone.name

        return EntityZoneSessionRecord(
            id=row.id,
            zone_id=row.zone_id,
            entity_id=row.entity_id,
            camera_id=row.camera_id,
            entered_at=row.entered_at,
            last_seen_at=row.last_seen_at,
            exited_at=row.exited_at,
            status=row.status,
            entry_event_id=row.entry_event_id,
            exit_event_id=row.exit_event_id,
            occupancy_after_enter=int(row.occupancy_after_enter or 1),
            occupancy_after_exit=row.occupancy_after_exit,
            zone_name=zone_name,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _with_session(
        self,
        session: Session | None,
        operation: Callable[[Session], T],
    ) -> T:
        if session is not None:
            return operation(session)
        with session_scope(self._session_factory) as owned:
            return operation(owned)
