"""SQLAlchemy Core repository for the derived entity activity timeline.

Builds a read-only UNION ALL projection over ``entities``,
``entity_observations``, and ``entity_zone_sessions`` with database-side
filtering, cursor conditions, ordering, and limit. No timeline_events table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    Integer,
    String,
    and_,
    cast,
    func,
    literal,
    null,
    or_,
    select,
    union_all,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import ColumnElement, Select
from sqlalchemy.sql.elements import Label, Null

from storage.entity_orm import Entity, EntityObservation, EntityStatus
from storage.sqlalchemy_db import session_scope
from storage.timeline_cursor import encode_cursor
from storage.timeline_models import (
    TimelineEvent,
    TimelineEventType,
    TimelineListFilter,
    TimelinePage,
)
from storage.zone_orm import EntityZoneSession, Zone, ZoneSessionStatus

# Canonical UNION projection types (PostgreSQL requires matching types per
# column position across every branch). Keep SQLite-compatible.
_STR = String()
_INT = Integer()
_BIGINT = BigInteger()
_FLOAT = Float()
_DT = DateTime(timezone=True)

# Ordered column names for the timeline UNION contract (regression tests).
TIMELINE_UNION_COLUMN_NAMES: tuple[str, ...] = (
    "event_id",
    "event_type",
    "occurred_at",
    "source",
    "entity_id",
    "camera_id",
    "entity_type",
    "identity_key",
    "track_id",
    "status",
    "confidence",
    "frame_number",
    "source_event_type",
    "zone_id",
    "zone_name",
    "session_id",
    "occupancy",
)


def _is_sql_null(value: Any) -> bool:
    return isinstance(value, Null)


def _typed_null(sql_type: Any, label: str) -> Label[Any]:
    """Typed SQL NULL so PostgreSQL UNION branches share one type."""

    return cast(null(), sql_type).label(label)


def _typed_str(value: Any, label: str) -> Label[Any]:
    """Cast strings / UUIDs / NULL to VARCHAR for a uniform UNION type."""

    if _is_sql_null(value):
        return cast(null(), _STR).label(label)
    return cast(value, _STR).label(label)


def _typed_int(value: Any, label: str) -> Label[Any]:
    if _is_sql_null(value):
        return cast(null(), _INT).label(label)
    return cast(value, _INT).label(label)


def _typed_bigint(value: Any, label: str) -> Label[Any]:
    if _is_sql_null(value):
        return cast(null(), _BIGINT).label(label)
    return cast(value, _BIGINT).label(label)


def _typed_float(value: Any, label: str) -> Label[Any]:
    if _is_sql_null(value):
        return cast(null(), _FLOAT).label(label)
    return cast(value, _FLOAT).label(label)


def _typed_dt(value: Any, label: str) -> Label[Any]:
    """Label datetime columns; cast only SQL NULL (avoid SQLite result bugs)."""

    if _is_sql_null(value):
        return cast(null(), _DT).label(label)
    # Real TIMESTAMP columns already have the correct type for both dialects.
    return value.label(label)


def _projection(
    *,
    event_id: Any,
    event_type: Any,
    occurred_at: Any,
    source: Any,
    entity_id: Any,
    camera_id: Any,
    entity_type: Any,
    identity_key: Any,
    track_id: Any,
    status: Any,
    confidence: Any,
    frame_number: Any,
    source_event_type: Any,
    zone_id: Any,
    zone_name: Any,
    session_id: Any,
    occupancy: Any,
) -> tuple[ColumnElement[Any], ...]:
    """Return the canonical typed column list for one UNION branch."""

    return (
        _typed_str(event_id, "event_id"),
        _typed_str(event_type, "event_type"),
        _typed_dt(occurred_at, "occurred_at"),
        _typed_str(source, "source"),
        _typed_str(entity_id, "entity_id"),
        _typed_str(camera_id, "camera_id"),
        _typed_str(entity_type, "entity_type"),
        _typed_str(identity_key, "identity_key"),
        _typed_bigint(track_id, "track_id"),
        _typed_str(status, "status"),
        _typed_float(confidence, "confidence"),
        _typed_bigint(frame_number, "frame_number"),
        _typed_str(source_event_type, "source_event_type"),
        _typed_str(zone_id, "zone_id"),
        _typed_str(zone_name, "zone_name"),
        _typed_str(session_id, "session_id"),
        _typed_int(occupancy, "occupancy"),
    )


def _null_projection_defaults() -> dict[str, Any]:
    """Typed NULL expressions for optional projection columns."""

    return {
        "identity_key": null(),
        "track_id": null(),
        "status": null(),
        "confidence": null(),
        "frame_number": null(),
        "source_event_type": null(),
        "zone_id": null(),
        "zone_name": null(),
        "session_id": null(),
        "occupancy": null(),
    }


class TimelineRepository:
    """Project entity lifecycle, observations, and spatial sessions."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_events(self, filters: TimelineListFilter) -> TimelinePage:
        """Return one cursor page of timeline events."""

        if filters.limit < 1:
            raise ValueError("limit must be >= 1")

        with session_scope(self._session_factory) as session:
            statement = self._build_list_statement(filters)
            rows = session.execute(statement).mappings().all()

        has_more = len(rows) > filters.limit
        page_rows = rows[: filters.limit]
        items = [self._row_to_event(row) for row in page_rows]

        next_cursor: str | None = None
        if has_more and items:
            last = items[-1]
            next_cursor = encode_cursor(last.occurred_at, last.id)

        return TimelinePage(
            items=items,
            limit=filters.limit,
            next_cursor=next_cursor,
        )

    def get_event_by_id(self, event_id: str) -> TimelineEvent | None:
        """Resolve one stable namespaced timeline event id."""

        event_id = event_id.strip()
        if not event_id:
            return None

        with session_scope(self._session_factory) as session:
            if event_id.startswith("entity-created:"):
                entity_id = self._parse_uuid_suffix(
                    event_id,
                    "entity-created:",
                )
                if entity_id is None:
                    return None
                entity = session.get(Entity, entity_id)
                if entity is None:
                    return None
                return self._entity_created_event(entity)

            if event_id.startswith("entity-closed:"):
                entity_id = self._parse_uuid_suffix(
                    event_id,
                    "entity-closed:",
                )
                if entity_id is None:
                    return None
                entity = session.get(Entity, entity_id)
                if entity is None or entity.status is not EntityStatus.CLOSED:
                    return None
                return self._entity_closed_event(entity)

            if event_id.startswith("observation:"):
                observation_id = self._parse_uuid_suffix(
                    event_id,
                    "observation:",
                )
                if observation_id is None:
                    return None
                observation = session.get(EntityObservation, observation_id)
                if observation is None:
                    return None
                return self._observation_event(observation)

            if event_id.startswith("zone-entered:"):
                session_id = self._parse_uuid_suffix(event_id, "zone-entered:")
                if session_id is None:
                    return None
                return self._zone_session_event(
                    session,
                    session_id,
                    TimelineEventType.ZONE_ENTERED,
                )

            if event_id.startswith("zone-exited:"):
                session_id = self._parse_uuid_suffix(event_id, "zone-exited:")
                if session_id is None:
                    return None
                return self._zone_session_event(
                    session,
                    session_id,
                    TimelineEventType.ZONE_EXITED,
                )

            if event_id.startswith("zone-occupancy:"):
                return self._zone_occupancy_event_by_id(session, event_id)

        return None

    def _build_list_statement(self, filters: TimelineListFilter) -> Select[Any]:
        branches: list[Select[Any]] = []

        for event_type in filters.event_types:
            if event_type is TimelineEventType.ENTITY_CREATED:
                if filters.zone_id is None:
                    branches.append(self._created_select(filters))
            elif event_type is TimelineEventType.ENTITY_CLOSED:
                if filters.zone_id is None:
                    branches.append(self._closed_select(filters))
            elif event_type is TimelineEventType.OBSERVATION_RECORDED:
                if filters.zone_id is None:
                    branches.append(self._observation_select(filters))
            elif event_type is TimelineEventType.ZONE_ENTERED:
                branches.append(self._zone_entered_select(filters))
            elif event_type is TimelineEventType.ZONE_EXITED:
                branches.append(self._zone_exited_select(filters))
            elif event_type is TimelineEventType.ZONE_OCCUPANCY_CHANGED:
                branches.append(self._zone_occupancy_entered_select(filters))
                branches.append(self._zone_occupancy_exited_select(filters))

        if not branches:
            empty = select(
                *_projection(
                    event_id=literal(""),
                    event_type=literal(""),
                    occurred_at=null(),
                    source=literal(""),
                    entity_id=literal(""),
                    camera_id=null(),
                    entity_type=literal(""),
                    **_null_projection_defaults(),
                )
            ).where(literal(False))
            return empty.limit(0)

        combined = union_all(*branches).subquery("timeline_events")
        occurred_at = combined.c.occurred_at
        event_id = combined.c.event_id

        statement = select(
            combined.c.event_id,
            combined.c.event_type,
            combined.c.occurred_at,
            combined.c.source,
            combined.c.entity_id,
            combined.c.camera_id,
            combined.c.entity_type,
            combined.c.identity_key,
            combined.c.track_id,
            combined.c.status,
            combined.c.confidence,
            combined.c.frame_number,
            combined.c.source_event_type,
            combined.c.zone_id,
            combined.c.zone_name,
            combined.c.session_id,
            combined.c.occupancy,
        )

        if filters.cursor is not None:
            cursor_at = filters.cursor.occurred_at
            cursor_id = filters.cursor.event_id
            if filters.sort == "asc":
                statement = statement.where(
                    or_(
                        occurred_at > cursor_at,
                        and_(
                            occurred_at == cursor_at,
                            event_id > cursor_id,
                        ),
                    )
                )
            else:
                statement = statement.where(
                    or_(
                        occurred_at < cursor_at,
                        and_(
                            occurred_at == cursor_at,
                            event_id < cursor_id,
                        ),
                    )
                )

        if filters.sort == "asc":
            statement = statement.order_by(occurred_at.asc(), event_id.asc())
        else:
            statement = statement.order_by(
                occurred_at.desc(),
                event_id.desc(),
            )

        return statement.limit(filters.limit + 1)

    def _created_select(self, filters: TimelineListFilter) -> Select[Any]:
        event_id = func.concat(
            literal("entity-created:"),
            cast(Entity.id, _STR),
        )
        nulls = _null_projection_defaults()
        statement = select(
            *_projection(
                event_id=event_id,
                event_type=literal(TimelineEventType.ENTITY_CREATED.value),
                occurred_at=Entity.first_seen,
                source=literal("entity"),
                entity_id=Entity.id,
                camera_id=Entity.camera_id,
                entity_type=Entity.label,
                identity_key=Entity.identity_key,
                track_id=Entity.track_id,
                status=literal("active"),
                confidence=nulls["confidence"],
                frame_number=nulls["frame_number"],
                source_event_type=nulls["source_event_type"],
                zone_id=nulls["zone_id"],
                zone_name=nulls["zone_name"],
                session_id=nulls["session_id"],
                occupancy=nulls["occupancy"],
            )
        ).select_from(Entity)

        return self._apply_entity_filters(
            statement,
            filters,
            time_column=Entity.first_seen,
            camera_column=Entity.camera_id,
            label_column=Entity.label,
            entity_id_column=Entity.id,
        )

    def _closed_select(self, filters: TimelineListFilter) -> Select[Any]:
        event_id = func.concat(
            literal("entity-closed:"),
            cast(Entity.id, _STR),
        )
        nulls = _null_projection_defaults()
        statement = (
            select(
                *_projection(
                    event_id=event_id,
                    event_type=literal(TimelineEventType.ENTITY_CLOSED.value),
                    occurred_at=Entity.last_seen,
                    source=literal("entity"),
                    entity_id=Entity.id,
                    camera_id=Entity.camera_id,
                    entity_type=Entity.label,
                    identity_key=Entity.identity_key,
                    track_id=Entity.track_id,
                    status=literal("closed"),
                    confidence=nulls["confidence"],
                    frame_number=nulls["frame_number"],
                    source_event_type=nulls["source_event_type"],
                    zone_id=nulls["zone_id"],
                    zone_name=nulls["zone_name"],
                    session_id=nulls["session_id"],
                    occupancy=nulls["occupancy"],
                )
            )
            .select_from(Entity)
            .where(Entity.status == EntityStatus.CLOSED)
        )

        return self._apply_entity_filters(
            statement,
            filters,
            time_column=Entity.last_seen,
            camera_column=Entity.camera_id,
            label_column=Entity.label,
            entity_id_column=Entity.id,
        )

    def _observation_select(self, filters: TimelineListFilter) -> Select[Any]:
        event_id = func.concat(
            literal("observation:"),
            cast(EntityObservation.id, _STR),
        )
        nulls = _null_projection_defaults()
        statement = select(
            *_projection(
                event_id=event_id,
                event_type=literal(
                    TimelineEventType.OBSERVATION_RECORDED.value
                ),
                occurred_at=EntityObservation.observed_at,
                source=literal("observation"),
                entity_id=EntityObservation.entity_id,
                camera_id=EntityObservation.camera_id,
                entity_type=EntityObservation.label,
                identity_key=nulls["identity_key"],
                track_id=EntityObservation.track_id,
                status=nulls["status"],
                confidence=EntityObservation.confidence,
                frame_number=EntityObservation.frame_number,
                source_event_type=EntityObservation.source_event_type,
                zone_id=nulls["zone_id"],
                zone_name=nulls["zone_name"],
                session_id=nulls["session_id"],
                occupancy=nulls["occupancy"],
            )
        ).select_from(EntityObservation)

        if filters.entity_id is not None:
            statement = statement.where(
                EntityObservation.entity_id == filters.entity_id
            )
        if filters.camera_id is not None:
            statement = statement.where(
                EntityObservation.camera_id == filters.camera_id
            )
        if filters.entity_type is not None:
            statement = statement.where(
                EntityObservation.label == filters.entity_type
            )
        if filters.occurred_after is not None:
            statement = statement.where(
                EntityObservation.observed_at >= filters.occurred_after
            )
        if filters.occurred_before is not None:
            statement = statement.where(
                EntityObservation.observed_at <= filters.occurred_before
            )
        return statement

    def _zone_entered_select(self, filters: TimelineListFilter) -> Select[Any]:
        event_id = func.concat(
            literal("zone-entered:"),
            cast(EntityZoneSession.id, _STR),
        )
        nulls = _null_projection_defaults()
        statement = (
            select(
                *_projection(
                    event_id=event_id,
                    event_type=literal(TimelineEventType.ZONE_ENTERED.value),
                    occurred_at=EntityZoneSession.entered_at,
                    source=literal("spatial"),
                    entity_id=EntityZoneSession.entity_id,
                    camera_id=EntityZoneSession.camera_id,
                    entity_type=Entity.label,
                    identity_key=nulls["identity_key"],
                    track_id=Entity.track_id,
                    status=nulls["status"],
                    confidence=nulls["confidence"],
                    frame_number=nulls["frame_number"],
                    source_event_type=nulls["source_event_type"],
                    zone_id=EntityZoneSession.zone_id,
                    zone_name=Zone.name,
                    session_id=EntityZoneSession.id,
                    occupancy=EntityZoneSession.occupancy_after_enter,
                )
            )
            .select_from(EntityZoneSession)
            .join(Zone, Zone.id == EntityZoneSession.zone_id)
            .join(Entity, Entity.id == EntityZoneSession.entity_id)
        )
        return self._apply_spatial_filters(
            statement,
            filters,
            time_column=EntityZoneSession.entered_at,
        )

    def _zone_exited_select(self, filters: TimelineListFilter) -> Select[Any]:
        event_id = func.concat(
            literal("zone-exited:"),
            cast(EntityZoneSession.id, _STR),
        )
        nulls = _null_projection_defaults()
        statement = (
            select(
                *_projection(
                    event_id=event_id,
                    event_type=literal(TimelineEventType.ZONE_EXITED.value),
                    occurred_at=EntityZoneSession.exited_at,
                    source=literal("spatial"),
                    entity_id=EntityZoneSession.entity_id,
                    camera_id=EntityZoneSession.camera_id,
                    entity_type=Entity.label,
                    identity_key=nulls["identity_key"],
                    track_id=Entity.track_id,
                    status=nulls["status"],
                    confidence=nulls["confidence"],
                    frame_number=nulls["frame_number"],
                    source_event_type=nulls["source_event_type"],
                    zone_id=EntityZoneSession.zone_id,
                    zone_name=Zone.name,
                    session_id=EntityZoneSession.id,
                    occupancy=EntityZoneSession.occupancy_after_exit,
                )
            )
            .select_from(EntityZoneSession)
            .join(Zone, Zone.id == EntityZoneSession.zone_id)
            .join(Entity, Entity.id == EntityZoneSession.entity_id)
            .where(EntityZoneSession.status == ZoneSessionStatus.CLOSED)
            .where(EntityZoneSession.exited_at.is_not(None))
        )
        return self._apply_spatial_filters(
            statement,
            filters,
            time_column=EntityZoneSession.exited_at,
        )

    def _zone_occupancy_entered_select(
        self,
        filters: TimelineListFilter,
    ) -> Select[Any]:
        event_id = func.concat(
            literal("zone-occupancy:"),
            cast(EntityZoneSession.id, _STR),
            literal(":entered"),
        )
        nulls = _null_projection_defaults()
        statement = (
            select(
                *_projection(
                    event_id=event_id,
                    event_type=literal(
                        TimelineEventType.ZONE_OCCUPANCY_CHANGED.value
                    ),
                    occurred_at=EntityZoneSession.entered_at,
                    source=literal("spatial"),
                    entity_id=EntityZoneSession.entity_id,
                    camera_id=EntityZoneSession.camera_id,
                    entity_type=Entity.label,
                    identity_key=nulls["identity_key"],
                    track_id=Entity.track_id,
                    status=literal("entered"),
                    confidence=nulls["confidence"],
                    frame_number=nulls["frame_number"],
                    source_event_type=nulls["source_event_type"],
                    zone_id=EntityZoneSession.zone_id,
                    zone_name=Zone.name,
                    session_id=EntityZoneSession.id,
                    occupancy=EntityZoneSession.occupancy_after_enter,
                )
            )
            .select_from(EntityZoneSession)
            .join(Zone, Zone.id == EntityZoneSession.zone_id)
            .join(Entity, Entity.id == EntityZoneSession.entity_id)
        )
        return self._apply_spatial_filters(
            statement,
            filters,
            time_column=EntityZoneSession.entered_at,
        )

    def _zone_occupancy_exited_select(
        self,
        filters: TimelineListFilter,
    ) -> Select[Any]:
        event_id = func.concat(
            literal("zone-occupancy:"),
            cast(EntityZoneSession.id, _STR),
            literal(":exited"),
        )
        nulls = _null_projection_defaults()
        statement = (
            select(
                *_projection(
                    event_id=event_id,
                    event_type=literal(
                        TimelineEventType.ZONE_OCCUPANCY_CHANGED.value
                    ),
                    occurred_at=EntityZoneSession.exited_at,
                    source=literal("spatial"),
                    entity_id=EntityZoneSession.entity_id,
                    camera_id=EntityZoneSession.camera_id,
                    entity_type=Entity.label,
                    identity_key=nulls["identity_key"],
                    track_id=Entity.track_id,
                    status=literal("exited"),
                    confidence=nulls["confidence"],
                    frame_number=nulls["frame_number"],
                    source_event_type=nulls["source_event_type"],
                    zone_id=EntityZoneSession.zone_id,
                    zone_name=Zone.name,
                    session_id=EntityZoneSession.id,
                    occupancy=EntityZoneSession.occupancy_after_exit,
                )
            )
            .select_from(EntityZoneSession)
            .join(Zone, Zone.id == EntityZoneSession.zone_id)
            .join(Entity, Entity.id == EntityZoneSession.entity_id)
            .where(EntityZoneSession.status == ZoneSessionStatus.CLOSED)
            .where(EntityZoneSession.exited_at.is_not(None))
        )
        return self._apply_spatial_filters(
            statement,
            filters,
            time_column=EntityZoneSession.exited_at,
        )

    def _apply_spatial_filters(
        self,
        statement: Select[Any],
        filters: TimelineListFilter,
        *,
        time_column: Any,
    ) -> Select[Any]:
        if filters.entity_id is not None:
            statement = statement.where(
                EntityZoneSession.entity_id == filters.entity_id
            )
        if filters.camera_id is not None:
            statement = statement.where(
                EntityZoneSession.camera_id == filters.camera_id
            )
        if filters.entity_type is not None:
            statement = statement.where(Entity.label == filters.entity_type)
        if filters.zone_id is not None:
            statement = statement.where(
                EntityZoneSession.zone_id == filters.zone_id
            )
        if filters.occurred_after is not None:
            statement = statement.where(time_column >= filters.occurred_after)
        if filters.occurred_before is not None:
            statement = statement.where(time_column <= filters.occurred_before)
        return statement

    def _apply_entity_filters(
        self,
        statement: Select[Any],
        filters: TimelineListFilter,
        *,
        time_column: Any,
        camera_column: Any,
        label_column: Any,
        entity_id_column: Any,
    ) -> Select[Any]:
        if filters.entity_id is not None:
            statement = statement.where(entity_id_column == filters.entity_id)
        if filters.camera_id is not None:
            statement = statement.where(camera_column == filters.camera_id)
        if filters.entity_type is not None:
            statement = statement.where(label_column == filters.entity_type)
        if filters.occurred_after is not None:
            statement = statement.where(time_column >= filters.occurred_after)
        if filters.occurred_before is not None:
            statement = statement.where(time_column <= filters.occurred_before)
        return statement

    def _row_to_event(self, row: Any) -> TimelineEvent:
        event_type = TimelineEventType(str(row["event_type"]))
        entity_type = str(row["entity_type"] or "unknown")
        camera_id = row["camera_id"]
        camera_display = camera_id or "unknown"
        title = f"{entity_type[:1].upper()}{entity_type[1:]}"

        if event_type is TimelineEventType.ENTITY_CREATED:
            summary = f"{title} appeared on {camera_display}"
            payload = {
                "identity_key": row["identity_key"],
                "track_id": row["track_id"],
                "status": "active",
            }
        elif event_type is TimelineEventType.ENTITY_CLOSED:
            summary = f"{title} left {camera_display}"
            payload = {
                "identity_key": row["identity_key"],
                "track_id": row["track_id"],
                "status": "closed",
            }
        elif event_type is TimelineEventType.OBSERVATION_RECORDED:
            summary = f"{title} observed on {camera_display}"
            payload = {
                "confidence": row["confidence"],
                "frame_number": row["frame_number"],
                "track_id": row["track_id"],
                "source_event_type": row["source_event_type"],
            }
        elif event_type is TimelineEventType.ZONE_ENTERED:
            zone_name = row["zone_name"] or "zone"
            summary = f"{title} entered {zone_name}"
            payload = {
                "zone_id": row["zone_id"],
                "zone_name": zone_name,
                "session_id": row["session_id"],
                "occupancy": row["occupancy"],
            }
        elif event_type is TimelineEventType.ZONE_EXITED:
            zone_name = row["zone_name"] or "zone"
            summary = f"{title} exited {zone_name}"
            payload = {
                "zone_id": row["zone_id"],
                "zone_name": zone_name,
                "session_id": row["session_id"],
                "occupancy": row["occupancy"],
            }
        else:
            # zone_occupancy_changed
            zone_name = row["zone_name"] or "zone"
            occupancy = row["occupancy"]
            summary = f"{zone_name} occupancy is now {occupancy}"
            cause = row["status"]  # entered | exited marker
            payload = {
                "zone_id": row["zone_id"],
                "zone_name": zone_name,
                "session_id": row["session_id"],
                "occupancy": occupancy,
                "cause": cause,
            }

        occurred_at = row["occurred_at"]
        if isinstance(occurred_at, str):
            occurred_at = datetime.fromisoformat(
                occurred_at.replace("Z", "+00:00")
            )
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)

        return TimelineEvent(
            id=str(row["event_id"]),
            event_type=event_type,
            occurred_at=occurred_at,
            source=str(row["source"]),
            entity_id=UUID(str(row["entity_id"])),
            camera_id=camera_id,
            entity_type=entity_type,
            summary=summary,
            payload=payload,
        )

    def _entity_created_event(self, entity: Entity) -> TimelineEvent:
        camera = entity.camera_id or "unknown"
        label = entity.label
        return TimelineEvent(
            id=f"entity-created:{entity.id}",
            event_type=TimelineEventType.ENTITY_CREATED,
            occurred_at=self._aware(entity.first_seen),
            source="entity",
            entity_id=entity.id,
            camera_id=entity.camera_id,
            entity_type=label,
            summary=f"{label[:1].upper()}{label[1:]} appeared on {camera}",
            payload={
                "identity_key": entity.identity_key,
                "track_id": entity.track_id,
                "status": "active",
            },
        )

    def _entity_closed_event(self, entity: Entity) -> TimelineEvent:
        camera = entity.camera_id or "unknown"
        label = entity.label
        return TimelineEvent(
            id=f"entity-closed:{entity.id}",
            event_type=TimelineEventType.ENTITY_CLOSED,
            occurred_at=self._aware(entity.last_seen),
            source="entity",
            entity_id=entity.id,
            camera_id=entity.camera_id,
            entity_type=label,
            summary=f"{label[:1].upper()}{label[1:]} left {camera}",
            payload={
                "identity_key": entity.identity_key,
                "track_id": entity.track_id,
                "status": "closed",
            },
        )

    def _observation_event(
        self,
        observation: EntityObservation,
    ) -> TimelineEvent:
        label = observation.label
        return TimelineEvent(
            id=f"observation:{observation.id}",
            event_type=TimelineEventType.OBSERVATION_RECORDED,
            occurred_at=self._aware(observation.observed_at),
            source="observation",
            entity_id=observation.entity_id,
            camera_id=observation.camera_id,
            entity_type=label,
            summary=(
                f"{label[:1].upper()}{label[1:]} observed on "
                f"{observation.camera_id}"
            ),
            payload={
                "confidence": observation.confidence,
                "frame_number": observation.frame_number,
                "track_id": observation.track_id,
                "source_event_type": observation.source_event_type,
            },
        )

    def _zone_session_event(
        self,
        session: Session,
        session_id: UUID,
        event_type: TimelineEventType,
    ) -> TimelineEvent | None:
        row = session.get(EntityZoneSession, session_id)
        if row is None:
            return None
        zone = session.get(Zone, row.zone_id)
        entity = session.get(Entity, row.entity_id)
        zone_name = zone.name if zone is not None else "zone"
        label = entity.label if entity is not None else "entity"
        title = f"{label[:1].upper()}{label[1:]}"

        if event_type is TimelineEventType.ZONE_ENTERED:
            return TimelineEvent(
                id=f"zone-entered:{row.id}",
                event_type=TimelineEventType.ZONE_ENTERED,
                occurred_at=self._aware(row.entered_at),
                source="spatial",
                entity_id=row.entity_id,
                camera_id=row.camera_id,
                entity_type=label,
                summary=f"{title} entered {zone_name}",
                payload={
                    "zone_id": str(row.zone_id),
                    "zone_name": zone_name,
                    "session_id": str(row.id),
                    "occupancy": row.occupancy_after_enter,
                },
            )

        if row.status is not ZoneSessionStatus.CLOSED or row.exited_at is None:
            return None
        return TimelineEvent(
            id=f"zone-exited:{row.id}",
            event_type=TimelineEventType.ZONE_EXITED,
            occurred_at=self._aware(row.exited_at),
            source="spatial",
            entity_id=row.entity_id,
            camera_id=row.camera_id,
            entity_type=label,
            summary=f"{title} exited {zone_name}",
            payload={
                "zone_id": str(row.zone_id),
                "zone_name": zone_name,
                "session_id": str(row.id),
                "occupancy": row.occupancy_after_exit,
            },
        )

    def _zone_occupancy_event_by_id(
        self,
        session: Session,
        event_id: str,
    ) -> TimelineEvent | None:
        # zone-occupancy:{session_id}:entered|exited
        rest = event_id[len("zone-occupancy:") :]
        if rest.endswith(":entered"):
            cause = "entered"
            raw_id = rest[: -len(":entered")]
        elif rest.endswith(":exited"):
            cause = "exited"
            raw_id = rest[: -len(":exited")]
        else:
            return None
        try:
            session_id = UUID(raw_id)
        except ValueError:
            return None

        row = session.get(EntityZoneSession, session_id)
        if row is None:
            return None
        zone = session.get(Zone, row.zone_id)
        entity = session.get(Entity, row.entity_id)
        zone_name = zone.name if zone is not None else "zone"
        label = entity.label if entity is not None else "entity"

        if cause == "entered":
            occurred = row.entered_at
            occupancy = row.occupancy_after_enter
        else:
            if row.status is not ZoneSessionStatus.CLOSED or row.exited_at is None:
                return None
            occurred = row.exited_at
            occupancy = row.occupancy_after_exit

        return TimelineEvent(
            id=event_id,
            event_type=TimelineEventType.ZONE_OCCUPANCY_CHANGED,
            occurred_at=self._aware(occurred),
            source="spatial",
            entity_id=row.entity_id,
            camera_id=row.camera_id,
            entity_type=label,
            summary=f"{zone_name} occupancy is now {occupancy}",
            payload={
                "zone_id": str(row.zone_id),
                "zone_name": zone_name,
                "session_id": str(row.id),
                "occupancy": occupancy,
                "cause": cause,
            },
        )

    @staticmethod
    def _parse_uuid_suffix(event_id: str, prefix: str) -> UUID | None:
        raw = event_id[len(prefix) :]
        try:
            return UUID(raw)
        except ValueError:
            return None

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
