"""Alert timeline provider (v0.8.0)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import and_, cast, func, literal, null, or_, select, union_all
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import Select

from storage.alert_orm import Alert, AlertStatus
from storage.entity_orm import Entity
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
        TimelineEventType.ALERT_TRIGGERED,
        TimelineEventType.ALERT_RESOLVED,
    }
)
_OWNED_PREFIXES = frozenset({"alert-triggered:", "alert-resolved:"})


class AlertTimelineProvider:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "alert"

    @property
    def owned_event_types(self) -> frozenset[TimelineEventType]:
        return _OWNED_TYPES

    @property
    def owned_id_prefixes(self) -> frozenset[str]:
        return _OWNED_PREFIXES

    def supports_event_id(self, event_id: str) -> bool:
        return any(event_id.startswith(p) for p in _OWNED_PREFIXES)

    def can_contribute(self, context: TimelineQueryContext) -> bool:
        return bool(context.relevant_event_types(_OWNED_TYPES))

    def list_events(self, context: TimelineQueryContext) -> list[TimelineEvent]:
        if context.limit < 1 or not self.can_contribute(context):
            return []
        with session_scope(self._session_factory) as session:
            statement = self.build_list_statement(context)
            rows = session.execute(statement).mappings().all()
        return [row_to_timeline_event(row) for row in rows]

    def get_event_by_id(self, event_id: str) -> TimelineEvent | None:
        event_id = event_id.strip()
        if not self.supports_event_id(event_id):
            return None
        with session_scope(self._session_factory) as session:
            if event_id.startswith("alert-triggered:"):
                alert_id = parse_uuid_suffix(event_id, "alert-triggered:")
                if alert_id is None:
                    return None
                alert = session.get(Alert, alert_id)
                if alert is None:
                    return None
                return self._triggered_event(alert, session)
            if event_id.startswith("alert-resolved:"):
                alert_id = parse_uuid_suffix(event_id, "alert-resolved:")
                if alert_id is None:
                    return None
                alert = session.get(Alert, alert_id)
                if alert is None or alert.status is not AlertStatus.RESOLVED:
                    return None
                if alert.resolved_at is None:
                    return None
                return self._resolved_event(alert, session)
        return None

    def build_list_statement(self, context: TimelineQueryContext) -> Select[Any]:
        relevant = context.relevant_event_types(_OWNED_TYPES)
        branches: list[Select[Any]] = []
        for event_type in relevant:
            if event_type is TimelineEventType.ALERT_TRIGGERED:
                branches.append(self._triggered_select(context))
            elif event_type is TimelineEventType.ALERT_RESOLVED:
                branches.append(self._resolved_select(context))
        if not branches:
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
            ).where(literal(False)).limit(0)

        combined = union_all(*branches).subquery("alert_timeline_events")
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
        if context.cursor is not None:
            cursor_at = context.cursor.occurred_at
            cursor_id = context.cursor.event_id
            if context.sort == "asc":
                statement = statement.where(
                    or_(
                        occurred_at > cursor_at,
                        and_(occurred_at == cursor_at, event_id > cursor_id),
                    )
                )
            else:
                statement = statement.where(
                    or_(
                        occurred_at < cursor_at,
                        and_(occurred_at == cursor_at, event_id < cursor_id),
                    )
                )
        if context.sort == "asc":
            statement = statement.order_by(occurred_at.asc(), event_id.asc())
        else:
            statement = statement.order_by(occurred_at.desc(), event_id.desc())
        return statement.limit(context.limit)

    def _triggered_select(self, context: TimelineQueryContext) -> Select[Any]:
        event_id = func.concat(literal("alert-triggered:"), cast(Alert.id, STR))
        nulls = null_projection_defaults()
        statement = (
            select(
                *projection(
                    event_id=event_id,
                    event_type=literal(TimelineEventType.ALERT_TRIGGERED.value),
                    occurred_at=Alert.triggered_at,
                    source=literal("alert"),
                    entity_id=Alert.entity_id,
                    camera_id=Alert.camera_id,
                    entity_type=Entity.label,
                    identity_key=cast(Alert.rule_id, STR),
                    track_id=nulls["track_id"],
                    status=Alert.severity,
                    confidence=nulls["confidence"],
                    frame_number=nulls["frame_number"],
                    source_event_type=Alert.source_event_id,
                    zone_id=Alert.zone_id,
                    zone_name=Alert.status,
                    session_id=Alert.subject_key,
                    occupancy=nulls["occupancy"],
                )
            )
            .select_from(Alert)
            .join(Entity, Entity.id == Alert.entity_id)
        )
        return self._apply_filters(
            statement, context, time_column=Alert.triggered_at
        )

    def _resolved_select(self, context: TimelineQueryContext) -> Select[Any]:
        event_id = func.concat(literal("alert-resolved:"), cast(Alert.id, STR))
        nulls = null_projection_defaults()
        statement = (
            select(
                *projection(
                    event_id=event_id,
                    event_type=literal(TimelineEventType.ALERT_RESOLVED.value),
                    occurred_at=Alert.resolved_at,
                    source=literal("alert"),
                    entity_id=Alert.entity_id,
                    camera_id=Alert.camera_id,
                    entity_type=Entity.label,
                    identity_key=cast(Alert.rule_id, STR),
                    track_id=nulls["track_id"],
                    status=Alert.severity,
                    confidence=nulls["confidence"],
                    frame_number=nulls["frame_number"],
                    source_event_type=Alert.source_event_id,
                    zone_id=Alert.zone_id,
                    zone_name=literal("resolved"),
                    session_id=Alert.subject_key,
                    occupancy=nulls["occupancy"],
                )
            )
            .select_from(Alert)
            .join(Entity, Entity.id == Alert.entity_id)
            .where(Alert.status == AlertStatus.RESOLVED)
            .where(Alert.resolved_at.is_not(None))
        )
        return self._apply_filters(
            statement, context, time_column=Alert.resolved_at
        )

    def _apply_filters(
        self,
        statement: Select[Any],
        context: TimelineQueryContext,
        *,
        time_column: Any,
    ) -> Select[Any]:
        if context.entity_id is not None:
            statement = statement.where(Alert.entity_id == context.entity_id)
        if context.camera_id is not None:
            statement = statement.where(Alert.camera_id == context.camera_id)
        if context.entity_type is not None:
            statement = statement.where(Entity.label == context.entity_type)
        if context.zone_id is not None:
            statement = statement.where(Alert.zone_id == context.zone_id)
        if context.occurred_after is not None:
            statement = statement.where(time_column >= context.occurred_after)
        if context.occurred_before is not None:
            statement = statement.where(time_column <= context.occurred_before)
        return statement

    def _triggered_event(
        self, alert: Alert, session: Session
    ) -> TimelineEvent:
        entity = session.get(Entity, alert.entity_id)
        label = entity.label if entity else "entity"
        return TimelineEvent(
            id=f"alert-triggered:{alert.id}",
            event_type=TimelineEventType.ALERT_TRIGGERED,
            occurred_at=aware_utc(alert.triggered_at),
            source="alert",
            entity_id=alert.entity_id,
            camera_id=alert.camera_id,
            entity_type=label,
            summary=alert.summary,
            payload={
                "alert_id": str(alert.id),
                "rule_id": str(alert.rule_id),
                "severity": alert.severity.value,
                "alert_status": alert.status.value,
                "zone_id": str(alert.zone_id) if alert.zone_id else None,
                "source_event_id": alert.source_event_id,
                "subject_key": alert.subject_key,
            },
        )

    def _resolved_event(
        self, alert: Alert, session: Session
    ) -> TimelineEvent:
        entity = session.get(Entity, alert.entity_id)
        label = entity.label if entity else "entity"
        return TimelineEvent(
            id=f"alert-resolved:{alert.id}",
            event_type=TimelineEventType.ALERT_RESOLVED,
            occurred_at=aware_utc(alert.resolved_at or alert.triggered_at),
            source="alert",
            entity_id=alert.entity_id,
            camera_id=alert.camera_id,
            entity_type=label,
            summary=f"Alert resolved: {alert.summary}",
            payload={
                "alert_id": str(alert.id),
                "rule_id": str(alert.rule_id),
                "severity": alert.severity.value,
                "alert_status": "resolved",
                "zone_id": str(alert.zone_id) if alert.zone_id else None,
                "source_event_id": alert.source_event_id,
                "subject_key": alert.subject_key,
            },
        )
