"""Repositories for alert rules, alerts, evaluator state, checkpoints."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from storage.alert_orm import (
    Alert,
    AlertEvaluatorCheckpoint,
    AlertEvaluatorState,
    AlertRule,
    AlertRuleType,
    AlertSeverity,
    AlertStatus,
    EvaluatorStateKind,
)
from storage.alert_records import (
    AlertListFilter,
    AlertRecord,
    AlertRuleCreate,
    AlertRuleRecord,
    AlertRuleUpdate,
    CheckpointRecord,
    EvaluatorStateRecord,
)
from storage.entity_records import PageResult
from storage.sqlalchemy_db import session_scope

T = TypeVar("T")


class AlertConflictError(LookupError):
    pass


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class AlertRuleRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(
        self, data: AlertRuleCreate, *, session: Session | None = None
    ) -> AlertRuleRecord:
        def _write(active: Session) -> AlertRuleRecord:
            row = AlertRule(
                id=uuid.uuid4(),
                name=data.name,
                rule_type=data.rule_type,
                enabled=data.enabled,
                source_event_types=list(data.source_event_types),
                camera_ids=list(data.camera_ids),
                zone_ids=list(data.zone_ids),
                entity_types=list(data.entity_types),
                occupancy_threshold=data.occupancy_threshold,
                occupancy_duration_seconds=data.occupancy_duration_seconds,
                dwell_threshold_seconds=data.dwell_threshold_seconds,
                active_window_start=data.active_window_start,
                active_window_end=data.active_window_end,
                timezone=data.timezone,
                days_of_week=list(data.days_of_week),
                cooldown_seconds=data.cooldown_seconds,
                severity=data.severity,
                extra=dict(data.metadata),
            )
            active.add(row)
            try:
                active.flush()
            except IntegrityError as error:
                raise AlertConflictError(
                    f"Alert rule name already exists: {data.name!r}"
                ) from error
            return self._to_rule(row)

        return self._with_session(session, _write)

    def get_by_id(
        self, rule_id: UUID, *, session: Session | None = None
    ) -> AlertRuleRecord | None:
        def _read(active: Session) -> AlertRuleRecord | None:
            row = active.get(AlertRule, rule_id)
            return self._to_rule(row) if row else None

        return self._with_session(session, _read)

    def update(
        self,
        rule_id: UUID,
        data: AlertRuleUpdate,
        *,
        session: Session | None = None,
    ) -> AlertRuleRecord:
        def _write(active: Session) -> AlertRuleRecord:
            row = active.get(AlertRule, rule_id)
            if row is None:
                raise LookupError(f"Alert rule not found: {rule_id}")
            if data.name is not None:
                row.name = data.name
            if data.enabled is not None:
                row.enabled = data.enabled
            if data.source_event_types is not None:
                row.source_event_types = list(data.source_event_types)
            if data.camera_ids is not None:
                row.camera_ids = list(data.camera_ids)
            if data.zone_ids is not None:
                row.zone_ids = list(data.zone_ids)
            if data.entity_types is not None:
                row.entity_types = list(data.entity_types)
            if data.clear_occupancy_threshold:
                row.occupancy_threshold = None
            elif data.occupancy_threshold is not None:
                row.occupancy_threshold = data.occupancy_threshold
            if data.clear_occupancy_duration_seconds:
                row.occupancy_duration_seconds = None
            elif data.occupancy_duration_seconds is not None:
                row.occupancy_duration_seconds = data.occupancy_duration_seconds
            if data.clear_dwell_threshold_seconds:
                row.dwell_threshold_seconds = None
            elif data.dwell_threshold_seconds is not None:
                row.dwell_threshold_seconds = data.dwell_threshold_seconds
            if data.clear_active_window_start:
                row.active_window_start = None
            elif data.active_window_start is not None:
                row.active_window_start = data.active_window_start
            if data.clear_active_window_end:
                row.active_window_end = None
            elif data.active_window_end is not None:
                row.active_window_end = data.active_window_end
            if data.timezone is not None:
                row.timezone = data.timezone
            if data.days_of_week is not None:
                row.days_of_week = list(data.days_of_week)
            if data.cooldown_seconds is not None:
                row.cooldown_seconds = data.cooldown_seconds
            if data.severity is not None:
                row.severity = data.severity
            if data.metadata is not None:
                row.extra = dict(data.metadata)
            try:
                active.flush()
            except IntegrityError as error:
                raise AlertConflictError("Alert rule name conflict") from error
            return self._to_rule(row)

        return self._with_session(session, _write)

    def list_rules(
        self,
        *,
        enabled: bool | None = None,
        limit: int = 50,
        offset: int = 0,
        session: Session | None = None,
    ) -> PageResult:
        def _read(active: Session) -> PageResult:
            count_q = select(func.count()).select_from(AlertRule)
            list_q = select(AlertRule).order_by(AlertRule.name.asc())
            if enabled is not None:
                count_q = count_q.where(AlertRule.enabled.is_(enabled))
                list_q = list_q.where(AlertRule.enabled.is_(enabled))
            total = int(active.scalar(count_q) or 0)
            rows = active.scalars(list_q.offset(offset).limit(limit)).all()
            return PageResult(
                items=[self._to_rule(r) for r in rows],
                total=total,
                limit=limit,
                offset=offset,
            )

        return self._with_session(session, _read)

    def list_enabled(
        self, *, session: Session | None = None
    ) -> list[AlertRuleRecord]:
        def _read(active: Session) -> list[AlertRuleRecord]:
            stmt = select(AlertRule).where(AlertRule.enabled.is_(True))
            return [self._to_rule(r) for r in active.scalars(stmt).all()]

        return self._with_session(session, _read)

    def count(self, *, session: Session | None = None) -> int:
        def _read(active: Session) -> int:
            return int(
                active.scalar(select(func.count()).select_from(AlertRule)) or 0
            )

        return self._with_session(session, _read)

    def _to_rule(self, row: AlertRule) -> AlertRuleRecord:
        return AlertRuleRecord(
            id=row.id,
            name=row.name,
            rule_type=row.rule_type,
            enabled=bool(row.enabled),
            source_event_types=list(row.source_event_types or []),
            camera_ids=list(row.camera_ids or []),
            zone_ids=list(row.zone_ids or []),
            entity_types=list(row.entity_types or []),
            occupancy_threshold=row.occupancy_threshold,
            occupancy_duration_seconds=row.occupancy_duration_seconds,
            dwell_threshold_seconds=row.dwell_threshold_seconds,
            active_window_start=row.active_window_start,
            active_window_end=row.active_window_end,
            timezone=row.timezone,
            days_of_week=[int(x) for x in (row.days_of_week or [])],
            cooldown_seconds=int(row.cooldown_seconds),
            severity=row.severity,
            metadata=dict(row.extra or {}),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _with_session(
        self, session: Session | None, operation: Callable[[Session], T]
    ) -> T:
        if session is not None:
            return operation(session)
        with session_scope(self._session_factory) as owned:
            return operation(owned)


class AlertRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_by_id(
        self, alert_id: UUID, *, session: Session | None = None
    ) -> AlertRecord | None:
        def _read(active: Session) -> AlertRecord | None:
            row = active.get(Alert, alert_id)
            return self._to_alert(row, active) if row else None

        return self._with_session(session, _read)

    def get_by_idempotency(
        self, key: str, *, session: Session | None = None
    ) -> AlertRecord | None:
        def _read(active: Session) -> AlertRecord | None:
            stmt = select(Alert).where(Alert.idempotency_key == key).limit(1)
            row = active.scalars(stmt).first()
            return self._to_alert(row, active) if row else None

        return self._with_session(session, _read)

    def get_open_for_subject(
        self,
        rule_id: UUID,
        subject_key: str,
        *,
        session: Session | None = None,
    ) -> AlertRecord | None:
        def _read(active: Session) -> AlertRecord | None:
            stmt = (
                select(Alert)
                .where(Alert.rule_id == rule_id)
                .where(Alert.subject_key == subject_key)
                .where(
                    Alert.status.in_(
                        [AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED]
                    )
                )
                .limit(1)
            )
            row = active.scalars(stmt).first()
            return self._to_alert(row, active) if row else None

        return self._with_session(session, _read)

    def create(
        self,
        *,
        rule_id: UUID,
        severity: AlertSeverity,
        entity_id: UUID,
        zone_id: UUID | None,
        camera_id: str | None,
        source_event_id: str,
        subject_key: str,
        idempotency_key: str,
        triggered_at: datetime,
        summary: str,
        payload: dict[str, Any],
        session: Session | None = None,
    ) -> AlertRecord:
        def _write(active: Session) -> AlertRecord:
            existing = active.scalars(
                select(Alert)
                .where(Alert.idempotency_key == idempotency_key)
                .limit(1)
            ).first()
            if existing is not None:
                return self._to_alert(existing, active)

            open_existing = active.scalars(
                select(Alert)
                .where(Alert.rule_id == rule_id)
                .where(Alert.subject_key == subject_key)
                .where(
                    Alert.status.in_(
                        [AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED]
                    )
                )
                .limit(1)
            ).first()
            if open_existing is not None:
                open_existing.last_matched_at = triggered_at
                active.flush()
                return self._to_alert(open_existing, active)

            row = Alert(
                id=uuid.uuid4(),
                rule_id=rule_id,
                status=AlertStatus.OPEN,
                severity=severity,
                entity_id=entity_id,
                zone_id=zone_id,
                camera_id=camera_id,
                source_event_id=source_event_id,
                subject_key=subject_key,
                idempotency_key=idempotency_key,
                triggered_at=triggered_at,
                last_matched_at=triggered_at,
                summary=summary,
                payload=dict(payload),
            )
            active.add(row)
            try:
                active.flush()
            except IntegrityError as error:
                # Concurrent insert race: re-read without rolling back outer txn
                # by expiring and selecting again is dialect-sensitive; re-raise
                # after logging-friendly conflict unless row already present.
                again = active.scalars(
                    select(Alert)
                    .where(Alert.idempotency_key == idempotency_key)
                    .limit(1)
                ).first()
                if again is not None:
                    return self._to_alert(again, active)
                raise AlertConflictError(
                    "Alert insert conflict"
                ) from error
            return self._to_alert(row, active)

        return self._with_session(session, _write)

    def acknowledge(
        self,
        alert_id: UUID,
        *,
        at: datetime,
        session: Session | None = None,
    ) -> AlertRecord:
        def _write(active: Session) -> AlertRecord:
            row = active.get(Alert, alert_id)
            if row is None:
                raise LookupError(f"Alert not found: {alert_id}")
            if row.status is AlertStatus.RESOLVED:
                return self._to_alert(row, active)
            if row.status is AlertStatus.ACKNOWLEDGED:
                return self._to_alert(row, active)
            row.status = AlertStatus.ACKNOWLEDGED
            row.acknowledged_at = at
            active.flush()
            return self._to_alert(row, active)

        return self._with_session(session, _write)

    def resolve(
        self,
        alert_id: UUID,
        *,
        at: datetime,
        session: Session | None = None,
    ) -> AlertRecord:
        def _write(active: Session) -> AlertRecord:
            row = active.get(Alert, alert_id)
            if row is None:
                raise LookupError(f"Alert not found: {alert_id}")
            if row.status is AlertStatus.RESOLVED:
                return self._to_alert(row, active)
            row.status = AlertStatus.RESOLVED
            row.resolved_at = at
            active.flush()
            return self._to_alert(row, active)

        return self._with_session(session, _write)

    def list_alerts(
        self,
        filters: AlertListFilter,
        *,
        session: Session | None = None,
    ) -> PageResult:
        def _read(active: Session) -> PageResult:
            conditions: list[Any] = []
            if filters.status is not None:
                conditions.append(Alert.status == filters.status)
            if filters.rule_id is not None:
                conditions.append(Alert.rule_id == filters.rule_id)
            if filters.severity is not None:
                conditions.append(Alert.severity == filters.severity)
            if filters.entity_id is not None:
                conditions.append(Alert.entity_id == filters.entity_id)
            if filters.zone_id is not None:
                conditions.append(Alert.zone_id == filters.zone_id)
            if filters.camera_id is not None:
                conditions.append(Alert.camera_id == filters.camera_id)
            if filters.triggered_after is not None:
                conditions.append(Alert.triggered_at >= filters.triggered_after)
            if filters.triggered_before is not None:
                conditions.append(
                    Alert.triggered_at <= filters.triggered_before
                )

            count_q = select(func.count()).select_from(Alert)
            list_q = select(Alert)
            for c in conditions:
                count_q = count_q.where(c)
                list_q = list_q.where(c)
            if filters.sort == "asc":
                list_q = list_q.order_by(
                    Alert.triggered_at.asc(), Alert.id.asc()
                )
            else:
                list_q = list_q.order_by(
                    Alert.triggered_at.desc(), Alert.id.desc()
                )
            total = int(active.scalar(count_q) or 0)
            rows = active.scalars(
                list_q.offset(filters.offset).limit(filters.limit)
            ).all()
            return PageResult(
                items=[self._to_alert(r, active) for r in rows],
                total=total,
                limit=filters.limit,
                offset=filters.offset,
            )

        return self._with_session(session, _read)

    def count_open(self, *, session: Session | None = None) -> int:
        def _read(active: Session) -> int:
            stmt = (
                select(func.count())
                .select_from(Alert)
                .where(
                    Alert.status.in_(
                        [AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED]
                    )
                )
            )
            return int(active.scalar(stmt) or 0)

        return self._with_session(session, _read)

    def last_trigger_for_subject(
        self,
        rule_id: UUID,
        subject_key: str,
        *,
        session: Session | None = None,
    ) -> datetime | None:
        def _read(active: Session) -> datetime | None:
            stmt = (
                select(Alert.triggered_at)
                .where(Alert.rule_id == rule_id)
                .where(Alert.subject_key == subject_key)
                .order_by(Alert.triggered_at.desc())
                .limit(1)
            )
            value = active.scalar(stmt)
            return _aware(value) if value is not None else None

        return self._with_session(session, _read)

    def _to_alert(self, row: Alert, active: Session) -> AlertRecord:
        rule = active.get(AlertRule, row.rule_id)
        return AlertRecord(
            id=row.id,
            rule_id=row.rule_id,
            status=row.status,
            severity=row.severity,
            entity_id=row.entity_id,
            zone_id=row.zone_id,
            camera_id=row.camera_id,
            source_event_id=row.source_event_id,
            subject_key=row.subject_key,
            idempotency_key=row.idempotency_key,
            triggered_at=row.triggered_at,
            acknowledged_at=row.acknowledged_at,
            resolved_at=row.resolved_at,
            last_matched_at=row.last_matched_at,
            summary=row.summary,
            payload=dict(row.payload or {}),
            rule_name=rule.name if rule else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _with_session(
        self, session: Session | None, operation: Callable[[Session], T]
    ) -> T:
        if session is not None:
            return operation(session)
        with session_scope(self._session_factory) as owned:
            return operation(owned)


class AlertEvaluatorStateRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def upsert_pending(
        self,
        *,
        rule_id: UUID,
        subject_key: str,
        entity_id: UUID,
        zone_id: UUID | None,
        source_event_id: str,
        condition_started_at: datetime,
        due_at: datetime,
        session: Session | None = None,
    ) -> EvaluatorStateRecord:
        def _write(active: Session) -> EvaluatorStateRecord:
            stmt = (
                select(AlertEvaluatorState)
                .where(AlertEvaluatorState.rule_id == rule_id)
                .where(AlertEvaluatorState.subject_key == subject_key)
                .limit(1)
            )
            row = active.scalars(stmt).first()
            if row is None:
                row = AlertEvaluatorState(
                    id=uuid.uuid4(),
                    rule_id=rule_id,
                    subject_key=subject_key,
                    entity_id=entity_id,
                    zone_id=zone_id,
                    source_event_id=source_event_id,
                    condition_started_at=condition_started_at,
                    due_at=due_at,
                    state=EvaluatorStateKind.PENDING,
                )
                active.add(row)
            else:
                row.entity_id = entity_id
                row.zone_id = zone_id
                row.source_event_id = source_event_id
                if row.state is EvaluatorStateKind.CLEARED:
                    row.condition_started_at = condition_started_at
                    row.due_at = due_at
                row.state = EvaluatorStateKind.PENDING
            active.flush()
            return self._to_state(row)

        return self._with_session(session, _write)

    def clear(
        self,
        rule_id: UUID,
        subject_key: str,
        *,
        session: Session | None = None,
    ) -> None:
        def _write(active: Session) -> None:
            stmt = (
                select(AlertEvaluatorState)
                .where(AlertEvaluatorState.rule_id == rule_id)
                .where(AlertEvaluatorState.subject_key == subject_key)
                .limit(1)
            )
            row = active.scalars(stmt).first()
            if row is None:
                return
            row.state = EvaluatorStateKind.CLEARED
            active.flush()

        self._with_session(session, _write)

    def mark_triggered(
        self,
        state_id: UUID,
        *,
        alert_id: UUID,
        session: Session | None = None,
    ) -> EvaluatorStateRecord:
        def _write(active: Session) -> EvaluatorStateRecord:
            row = active.get(AlertEvaluatorState, state_id)
            if row is None:
                raise LookupError(f"Evaluator state not found: {state_id}")
            row.state = EvaluatorStateKind.TRIGGERED
            row.alert_id = alert_id
            active.flush()
            return self._to_state(row)

        return self._with_session(session, _write)

    def list_due(
        self,
        *,
        now: datetime,
        limit: int = 100,
        session: Session | None = None,
    ) -> list[EvaluatorStateRecord]:
        def _read(active: Session) -> list[EvaluatorStateRecord]:
            stmt = (
                select(AlertEvaluatorState)
                .where(AlertEvaluatorState.state == EvaluatorStateKind.PENDING)
                .where(AlertEvaluatorState.due_at <= now)
                .order_by(AlertEvaluatorState.due_at.asc())
                .limit(limit)
            )
            return [self._to_state(r) for r in active.scalars(stmt).all()]

        return self._with_session(session, _read)

    def get(
        self,
        rule_id: UUID,
        subject_key: str,
        *,
        session: Session | None = None,
    ) -> EvaluatorStateRecord | None:
        def _read(active: Session) -> EvaluatorStateRecord | None:
            stmt = (
                select(AlertEvaluatorState)
                .where(AlertEvaluatorState.rule_id == rule_id)
                .where(AlertEvaluatorState.subject_key == subject_key)
                .limit(1)
            )
            row = active.scalars(stmt).first()
            return self._to_state(row) if row else None

        return self._with_session(session, _read)

    @staticmethod
    def _to_state(row: AlertEvaluatorState) -> EvaluatorStateRecord:
        return EvaluatorStateRecord(
            id=row.id,
            rule_id=row.rule_id,
            subject_key=row.subject_key,
            entity_id=row.entity_id,
            zone_id=row.zone_id,
            source_event_id=row.source_event_id,
            condition_started_at=row.condition_started_at,
            due_at=row.due_at,
            state=row.state,
            alert_id=row.alert_id,
        )

    def _with_session(
        self, session: Session | None, operation: Callable[[Session], T]
    ) -> T:
        if session is not None:
            return operation(session)
        with session_scope(self._session_factory) as owned:
            return operation(owned)


class AlertCheckpointRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get(
        self, consumer_name: str, *, session: Session | None = None
    ) -> CheckpointRecord | None:
        def _read(active: Session) -> CheckpointRecord | None:
            row = active.get(AlertEvaluatorCheckpoint, consumer_name)
            if row is None:
                return None
            return CheckpointRecord(
                consumer_name=row.consumer_name,
                last_occurred_at=row.last_occurred_at,
                last_event_id=row.last_event_id,
                updated_at=row.updated_at,
            )

        return self._with_session(session, _read)

    def save(
        self,
        consumer_name: str,
        *,
        last_occurred_at: datetime,
        last_event_id: str,
        session: Session | None = None,
    ) -> CheckpointRecord:
        def _write(active: Session) -> CheckpointRecord:
            row = active.get(AlertEvaluatorCheckpoint, consumer_name)
            if row is None:
                row = AlertEvaluatorCheckpoint(
                    consumer_name=consumer_name,
                    last_occurred_at=last_occurred_at,
                    last_event_id=last_event_id,
                )
                active.add(row)
            else:
                row.last_occurred_at = last_occurred_at
                row.last_event_id = last_event_id
            active.flush()
            return CheckpointRecord(
                consumer_name=row.consumer_name,
                last_occurred_at=row.last_occurred_at,
                last_event_id=row.last_event_id,
                updated_at=row.updated_at,
            )

        return self._with_session(session, _write)

    def _with_session(
        self, session: Session | None, operation: Callable[[Session], T]
    ) -> T:
        if session is not None:
            return operation(session)
        with session_scope(self._session_factory) as owned:
            return operation(owned)
