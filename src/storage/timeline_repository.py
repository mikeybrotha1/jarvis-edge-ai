"""SQLAlchemy Core repository for the derived entity activity timeline.

Builds a read-only UNION ALL projection over ``entities`` and
``entity_observations`` with database-side filtering, cursor conditions,
ordering, and limit. No timeline_events table is created.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import String, and_, cast, func, literal, or_, select, union_all
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import Select

from storage.entity_orm import Entity, EntityObservation, EntityStatus
from storage.sqlalchemy_db import session_scope
from storage.timeline_cursor import encode_cursor
from storage.timeline_models import (
    TimelineEvent,
    TimelineEventType,
    TimelineListFilter,
    TimelinePage,
)


class TimelineRepository:
    """Project entity lifecycle and observation rows into timeline events."""

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

        return None

    def _build_list_statement(self, filters: TimelineListFilter) -> Select[Any]:
        branches: list[Select[Any]] = []

        for event_type in filters.event_types:
            if event_type is TimelineEventType.ENTITY_CREATED:
                branches.append(self._created_select(filters))
            elif event_type is TimelineEventType.ENTITY_CLOSED:
                branches.append(self._closed_select(filters))
            elif event_type is TimelineEventType.OBSERVATION_RECORDED:
                branches.append(self._observation_select(filters))

        if not branches:
            empty = select(
                literal("").label("event_id"),
                literal("").label("event_type"),
                literal(None).label("occurred_at"),
                literal("").label("source"),
                literal("").label("entity_id"),
                literal(None).label("camera_id"),
                literal("").label("entity_type"),
                literal(None).label("identity_key"),
                literal(None).label("track_id"),
                literal(None).label("status"),
                literal(None).label("confidence"),
                literal(None).label("frame_number"),
                literal(None).label("source_event_type"),
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
            cast(Entity.id, String),
        )
        statement = select(
            event_id.label("event_id"),
            literal(TimelineEventType.ENTITY_CREATED.value).label(
                "event_type"
            ),
            Entity.first_seen.label("occurred_at"),
            literal("entity").label("source"),
            cast(Entity.id, String).label("entity_id"),
            Entity.camera_id.label("camera_id"),
            Entity.label.label("entity_type"),
            Entity.identity_key.label("identity_key"),
            Entity.track_id.label("track_id"),
            literal("active").label("status"),
            literal(None).label("confidence"),
            literal(None).label("frame_number"),
            literal(None).label("source_event_type"),
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
            cast(Entity.id, String),
        )
        statement = (
            select(
                event_id.label("event_id"),
                literal(TimelineEventType.ENTITY_CLOSED.value).label(
                    "event_type"
                ),
                Entity.last_seen.label("occurred_at"),
                literal("entity").label("source"),
                cast(Entity.id, String).label("entity_id"),
                Entity.camera_id.label("camera_id"),
                Entity.label.label("entity_type"),
                Entity.identity_key.label("identity_key"),
                Entity.track_id.label("track_id"),
                literal("closed").label("status"),
                literal(None).label("confidence"),
                literal(None).label("frame_number"),
                literal(None).label("source_event_type"),
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
            cast(EntityObservation.id, String),
        )
        statement = select(
            event_id.label("event_id"),
            literal(TimelineEventType.OBSERVATION_RECORDED.value).label(
                "event_type"
            ),
            EntityObservation.observed_at.label("occurred_at"),
            literal("observation").label("source"),
            cast(EntityObservation.entity_id, String).label("entity_id"),
            EntityObservation.camera_id.label("camera_id"),
            EntityObservation.label.label("entity_type"),
            literal(None).label("identity_key"),
            EntityObservation.track_id.label("track_id"),
            literal(None).label("status"),
            EntityObservation.confidence.label("confidence"),
            EntityObservation.frame_number.label("frame_number"),
            EntityObservation.source_event_type.label("source_event_type"),
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
        entity_type = str(row["entity_type"])
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
        else:
            summary = f"{title} observed on {camera_display}"
            payload = {
                "confidence": row["confidence"],
                "frame_number": row["frame_number"],
                "track_id": row["track_id"],
                "source_event_type": row["source_event_type"],
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
