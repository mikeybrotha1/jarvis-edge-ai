"""Spatial timeline provider (v0.7.0).

Owns: zone_entered, zone_exited, zone_occupancy_changed.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import and_, cast, func, literal, null, or_, select, union_all
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import Select

from storage.entity_orm import Entity
from storage.sqlalchemy_db import session_scope
from storage.timeline_models import TimelineEvent, TimelineEventType
from storage.zone_orm import EntityZoneSession, Zone, ZoneSessionStatus
from timeline.contracts import (
    STR,
    aware_utc,
    null_projection_defaults,
    parse_uuid_suffix,
    projection,
    row_to_timeline_event,
)
from timeline.provider import TimelineQueryContext

_OWNED_TYPES = frozenset(
    {
        TimelineEventType.ZONE_ENTERED,
        TimelineEventType.ZONE_EXITED,
        TimelineEventType.ZONE_OCCUPANCY_CHANGED,
    }
)
_OWNED_PREFIXES = frozenset(
    {
        "zone-entered:",
        "zone-exited:",
        "zone-occupancy:",
    }
)


class SpatialTimelineProvider:
    """Project spatial zone session events."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "spatial"

    @property
    def owned_event_types(self) -> frozenset[TimelineEventType]:
        return _OWNED_TYPES

    @property
    def owned_id_prefixes(self) -> frozenset[str]:
        return _OWNED_PREFIXES

    def supports_event_id(self, event_id: str) -> bool:
        return any(event_id.startswith(prefix) for prefix in _OWNED_PREFIXES)

    def can_contribute(self, context: TimelineQueryContext) -> bool:
        return bool(context.relevant_event_types(_OWNED_TYPES))

    def list_events(self, context: TimelineQueryContext) -> list[TimelineEvent]:
        if context.limit < 1:
            return []
        if not self.can_contribute(context):
            return []

        with session_scope(self._session_factory) as session:
            statement = self.build_list_statement(context)
            rows = session.execute(statement).mappings().all()
        return [row_to_timeline_event(row) for row in rows]

    def get_event_by_id(self, event_id: str) -> TimelineEvent | None:
        event_id = event_id.strip()
        if not event_id or not self.supports_event_id(event_id):
            return None

        with session_scope(self._session_factory) as session:
            if event_id.startswith("zone-entered:"):
                session_id = parse_uuid_suffix(event_id, "zone-entered:")
                if session_id is None:
                    return None
                return self._zone_session_event(
                    session,
                    session_id,
                    TimelineEventType.ZONE_ENTERED,
                )

            if event_id.startswith("zone-exited:"):
                session_id = parse_uuid_suffix(event_id, "zone-exited:")
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

    def build_list_statement(self, context: TimelineQueryContext) -> Select[Any]:
        relevant = context.relevant_event_types(_OWNED_TYPES)
        branches: list[Select[Any]] = []
        for event_type in relevant:
            if event_type is TimelineEventType.ZONE_ENTERED:
                branches.append(self._zone_entered_select(context))
            elif event_type is TimelineEventType.ZONE_EXITED:
                branches.append(self._zone_exited_select(context))
            elif event_type is TimelineEventType.ZONE_OCCUPANCY_CHANGED:
                branches.append(self._zone_occupancy_entered_select(context))
                branches.append(self._zone_occupancy_exited_select(context))

        if not branches:
            return self._empty_select().limit(0)

        combined = union_all(*branches).subquery("spatial_timeline_events")
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
        statement = self._apply_cursor(statement, context, occurred_at, event_id)

        if context.sort == "asc":
            statement = statement.order_by(occurred_at.asc(), event_id.asc())
        else:
            statement = statement.order_by(occurred_at.desc(), event_id.desc())

        return statement.limit(context.limit)

    def _empty_select(self) -> Select[Any]:
        return select(
            *projection(
                event_id=literal(""),
                event_type=literal(""),
                occurred_at=null(),
                source=literal(""),
                entity_id=literal(""),
                camera_id=null(),
                entity_type=literal(""),
                **null_projection_defaults(),
            )
        ).where(literal(False))

    def _zone_entered_select(self, context: TimelineQueryContext) -> Select[Any]:
        event_id = func.concat(
            literal("zone-entered:"),
            cast(EntityZoneSession.id, STR),
        )
        nulls = null_projection_defaults()
        statement = (
            select(
                *projection(
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
            context,
            time_column=EntityZoneSession.entered_at,
        )

    def _zone_exited_select(self, context: TimelineQueryContext) -> Select[Any]:
        event_id = func.concat(
            literal("zone-exited:"),
            cast(EntityZoneSession.id, STR),
        )
        nulls = null_projection_defaults()
        statement = (
            select(
                *projection(
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
            context,
            time_column=EntityZoneSession.exited_at,
        )

    def _zone_occupancy_entered_select(
        self,
        context: TimelineQueryContext,
    ) -> Select[Any]:
        event_id = func.concat(
            literal("zone-occupancy:"),
            cast(EntityZoneSession.id, STR),
            literal(":entered"),
        )
        nulls = null_projection_defaults()
        statement = (
            select(
                *projection(
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
            context,
            time_column=EntityZoneSession.entered_at,
        )

    def _zone_occupancy_exited_select(
        self,
        context: TimelineQueryContext,
    ) -> Select[Any]:
        event_id = func.concat(
            literal("zone-occupancy:"),
            cast(EntityZoneSession.id, STR),
            literal(":exited"),
        )
        nulls = null_projection_defaults()
        statement = (
            select(
                *projection(
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
            context,
            time_column=EntityZoneSession.exited_at,
        )

    @staticmethod
    def _apply_spatial_filters(
        statement: Select[Any],
        context: TimelineQueryContext,
        *,
        time_column: Any,
    ) -> Select[Any]:
        if context.entity_id is not None:
            statement = statement.where(
                EntityZoneSession.entity_id == context.entity_id
            )
        if context.camera_id is not None:
            statement = statement.where(
                EntityZoneSession.camera_id == context.camera_id
            )
        if context.entity_type is not None:
            statement = statement.where(Entity.label == context.entity_type)
        if context.zone_id is not None:
            statement = statement.where(
                EntityZoneSession.zone_id == context.zone_id
            )
        if context.occurred_after is not None:
            statement = statement.where(time_column >= context.occurred_after)
        if context.occurred_before is not None:
            statement = statement.where(time_column <= context.occurred_before)
        return statement

    @staticmethod
    def _apply_cursor(
        statement: Select[Any],
        context: TimelineQueryContext,
        occurred_at: Any,
        event_id: Any,
    ) -> Select[Any]:
        if context.cursor is None:
            return statement
        cursor_at = context.cursor.occurred_at
        cursor_id = context.cursor.event_id
        if context.sort == "asc":
            return statement.where(
                or_(
                    occurred_at > cursor_at,
                    and_(occurred_at == cursor_at, event_id > cursor_id),
                )
            )
        return statement.where(
            or_(
                occurred_at < cursor_at,
                and_(occurred_at == cursor_at, event_id < cursor_id),
            )
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
                occurred_at=aware_utc(row.entered_at),
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
            occurred_at=aware_utc(row.exited_at),
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
            occurred_at=aware_utc(occurred),
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
