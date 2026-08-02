"""Entity lifecycle timeline provider (v0.7.0).

Owns: entity_created, entity_closed, observation_recorded.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import and_, cast, func, literal, null, or_, select, union_all
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import Select

from storage.entity_orm import Entity, EntityObservation, EntityStatus
from storage.sqlalchemy_db import session_scope
from storage.timeline_models import TimelineEvent, TimelineEventType
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
        TimelineEventType.ENTITY_CREATED,
        TimelineEventType.ENTITY_CLOSED,
        TimelineEventType.OBSERVATION_RECORDED,
    }
)
_OWNED_PREFIXES = frozenset(
    {
        "entity-created:",
        "entity-closed:",
        "observation:",
    }
)


class EntityLifecycleTimelineProvider:
    """Project entity lifecycle and observation events."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "entity_lifecycle"

    @property
    def owned_event_types(self) -> frozenset[TimelineEventType]:
        return _OWNED_TYPES

    @property
    def owned_id_prefixes(self) -> frozenset[str]:
        return _OWNED_PREFIXES

    def supports_event_id(self, event_id: str) -> bool:
        return any(event_id.startswith(prefix) for prefix in _OWNED_PREFIXES)

    def can_contribute(self, context: TimelineQueryContext) -> bool:
        if context.zone_id is not None:
            return False
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
            if event_id.startswith("entity-created:"):
                entity_id = parse_uuid_suffix(event_id, "entity-created:")
                if entity_id is None:
                    return None
                entity = session.get(Entity, entity_id)
                if entity is None:
                    return None
                return self._entity_created_event(entity)

            if event_id.startswith("entity-closed:"):
                entity_id = parse_uuid_suffix(event_id, "entity-closed:")
                if entity_id is None:
                    return None
                entity = session.get(Entity, entity_id)
                if entity is None or entity.status is not EntityStatus.CLOSED:
                    return None
                return self._entity_closed_event(entity)

            if event_id.startswith("observation:"):
                observation_id = parse_uuid_suffix(event_id, "observation:")
                if observation_id is None:
                    return None
                observation = session.get(EntityObservation, observation_id)
                if observation is None:
                    return None
                return self._observation_event(observation)

        return None

    def build_list_statement(self, context: TimelineQueryContext) -> Select[Any]:
        """Build bounded ordered SELECT for this provider (typed contract)."""

        relevant = context.relevant_event_types(_OWNED_TYPES)
        branches: list[Select[Any]] = []
        for event_type in relevant:
            if event_type is TimelineEventType.ENTITY_CREATED:
                branches.append(self._created_select(context))
            elif event_type is TimelineEventType.ENTITY_CLOSED:
                branches.append(self._closed_select(context))
            elif event_type is TimelineEventType.OBSERVATION_RECORDED:
                branches.append(self._observation_select(context))

        if not branches:
            return self._empty_select().limit(0)

        combined = union_all(*branches).subquery("entity_lifecycle_events")
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

    def _created_select(self, context: TimelineQueryContext) -> Select[Any]:
        event_id = func.concat(
            literal("entity-created:"),
            cast(Entity.id, STR),
        )
        nulls = null_projection_defaults()
        statement = select(
            *projection(
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
            context,
            time_column=Entity.first_seen,
            camera_column=Entity.camera_id,
            label_column=Entity.label,
            entity_id_column=Entity.id,
        )

    def _closed_select(self, context: TimelineQueryContext) -> Select[Any]:
        event_id = func.concat(
            literal("entity-closed:"),
            cast(Entity.id, STR),
        )
        nulls = null_projection_defaults()
        statement = (
            select(
                *projection(
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
            context,
            time_column=Entity.last_seen,
            camera_column=Entity.camera_id,
            label_column=Entity.label,
            entity_id_column=Entity.id,
        )

    def _observation_select(self, context: TimelineQueryContext) -> Select[Any]:
        event_id = func.concat(
            literal("observation:"),
            cast(EntityObservation.id, STR),
        )
        nulls = null_projection_defaults()
        statement = select(
            *projection(
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

        if context.entity_id is not None:
            statement = statement.where(
                EntityObservation.entity_id == context.entity_id
            )
        if context.camera_id is not None:
            statement = statement.where(
                EntityObservation.camera_id == context.camera_id
            )
        if context.entity_type is not None:
            statement = statement.where(
                EntityObservation.label == context.entity_type
            )
        if context.occurred_after is not None:
            statement = statement.where(
                EntityObservation.observed_at >= context.occurred_after
            )
        if context.occurred_before is not None:
            statement = statement.where(
                EntityObservation.observed_at <= context.occurred_before
            )
        return statement

    @staticmethod
    def _apply_entity_filters(
        statement: Select[Any],
        context: TimelineQueryContext,
        *,
        time_column: Any,
        camera_column: Any,
        label_column: Any,
        entity_id_column: Any,
    ) -> Select[Any]:
        if context.entity_id is not None:
            statement = statement.where(entity_id_column == context.entity_id)
        if context.camera_id is not None:
            statement = statement.where(camera_column == context.camera_id)
        if context.entity_type is not None:
            statement = statement.where(label_column == context.entity_type)
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

    @staticmethod
    def _entity_created_event(entity: Entity) -> TimelineEvent:
        camera = entity.camera_id or "unknown"
        label = entity.label
        return TimelineEvent(
            id=f"entity-created:{entity.id}",
            event_type=TimelineEventType.ENTITY_CREATED,
            occurred_at=aware_utc(entity.first_seen),
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

    @staticmethod
    def _entity_closed_event(entity: Entity) -> TimelineEvent:
        camera = entity.camera_id or "unknown"
        label = entity.label
        return TimelineEvent(
            id=f"entity-closed:{entity.id}",
            event_type=TimelineEventType.ENTITY_CLOSED,
            occurred_at=aware_utc(entity.last_seen),
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

    @staticmethod
    def _observation_event(observation: EntityObservation) -> TimelineEvent:
        label = observation.label
        return TimelineEvent(
            id=f"observation:{observation.id}",
            event_type=TimelineEventType.OBSERVATION_RECORDED,
            occurred_at=aware_utc(observation.observed_at),
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
