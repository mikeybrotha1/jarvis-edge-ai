"""Apply evaluation actions in separate alert transactions (v0.8.0)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from services.alerts.evaluators import DEFAULT_EVALUATORS, EvaluationAction, RuleEvaluator
from storage.activity_notify import ActivityNotificationPublisher
from storage.alert_orm import AlertRuleType
from storage.alert_records import AlertRecord, AlertRuleRecord
from storage.alert_repositories import (
    AlertCheckpointRepository,
    AlertEvaluatorStateRepository,
    AlertRepository,
    AlertRuleRepository,
)
from storage.entity_zone_session_repository import EntityZoneSessionRepository
from storage.sqlalchemy_db import session_scope
from storage.timeline_models import TimelineEvent, TimelineEventType
from storage.zone_orm import ZoneSessionStatus


SOURCE_EVENT_TYPES_FOR_EVAL: frozenset[str] = frozenset(
    {
        TimelineEventType.ENTITY_CREATED.value,
        TimelineEventType.ENTITY_CLOSED.value,
        TimelineEventType.OBSERVATION_RECORDED.value,
        TimelineEventType.ZONE_ENTERED.value,
        TimelineEventType.ZONE_EXITED.value,
        TimelineEventType.ZONE_OCCUPANCY_CHANGED.value,
    }
)

ALERT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "alert_triggered",
        "alert_resolved",
    }
)


class AlertEvaluationService:
    """Evaluate rules against committed source timeline events."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        rule_repository: AlertRuleRepository,
        alert_repository: AlertRepository,
        state_repository: AlertEvaluatorStateRepository,
        *,
        activity_publisher: ActivityNotificationPublisher | None = None,
        session_repository: EntityZoneSessionRepository | None = None,
        notification_enqueue: Any | None = None,
        evaluators: tuple[RuleEvaluator, ...] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._rules = rule_repository
        self._alerts = alert_repository
        self._states = state_repository
        self._publisher = activity_publisher
        self._sessions = session_repository
        self._notification_enqueue = notification_enqueue
        self._evaluators = evaluators or DEFAULT_EVALUATORS
        self._logger = logger or logging.getLogger(__name__)

    def process_source_event(self, event: TimelineEvent) -> list[AlertRecord]:
        """Evaluate one source event; returns newly triggered alerts."""

        if event.event_type.value in ALERT_EVENT_TYPES:
            return []
        if event.event_type.value not in SOURCE_EVENT_TYPES_FOR_EVAL:
            return []

        now = datetime.now(timezone.utc)
        rules = self._rules.list_enabled()
        actions: list[EvaluationAction] = []
        for rule in rules:
            for evaluator in self._evaluators:
                if not evaluator.supports(rule):
                    continue
                actions.extend(
                    evaluator.evaluate(rule, event, now=now)
                )

        triggered: list[AlertRecord] = []
        if not actions:
            return triggered

        with session_scope(self._session_factory) as session:
            for action in actions:
                result = self._apply_action(action, now=now, session=session)
                if result is not None:
                    triggered.append(result)
        return triggered

    def process_due_states(
        self, *, now: datetime | None = None, batch_size: int = 100
    ) -> list[AlertRecord]:
        """Fire pending dwell (and similar) states whose due_at has passed."""

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        due = self._states.list_due(now=current, limit=batch_size)
        triggered: list[AlertRecord] = []
        for state in due:
            rule = self._rules.get_by_id(state.rule_id)
            if rule is None or not rule.enabled:
                with session_scope(self._session_factory) as session:
                    self._states.clear(
                        state.rule_id, state.subject_key, session=session
                    )
                continue
            if rule.rule_type is AlertRuleType.DWELL_THRESHOLD:
                if not self._session_still_open(state.entity_id, state.zone_id):
                    with session_scope(self._session_factory) as session:
                        self._states.clear(
                            state.rule_id, state.subject_key, session=session
                        )
                        open_alert = self._alerts.get_open_for_subject(
                            state.rule_id,
                            state.subject_key,
                            session=session,
                        )
                        if open_alert is not None:
                            self._resolve_and_notify(
                                open_alert.id, at=current, session=session
                            )
                    continue
            if rule.rule_type is AlertRuleType.OCCUPANCY_THRESHOLD:
                # Sustained occupancy: only fire if zone occupancy still meets
                # threshold at due_at.
                if not self._zone_occupancy_meets(
                    state.zone_id, rule.occupancy_threshold or 0
                ):
                    with session_scope(self._session_factory) as session:
                        self._states.clear(
                            state.rule_id, state.subject_key, session=session
                        )
                    continue
            if rule.rule_type is AlertRuleType.DWELL_THRESHOLD:
                summary = f"Dwell threshold reached for rule {rule.name!r}"
            elif rule.rule_type is AlertRuleType.OCCUPANCY_THRESHOLD:
                summary = (
                    f"Occupancy remained above threshold for rule "
                    f"{rule.name!r}"
                )
            else:
                summary = f"Threshold reached for rule {rule.name!r}"
            action = EvaluationAction(
                kind="trigger",
                rule=rule,
                entity_id=state.entity_id,
                subject_key=state.subject_key,
                idempotency_key=f"{rule.id}:due:{state.id}",
                source_event_id=state.source_event_id,
                summary=summary,
                payload={
                    "rule_name": rule.name,
                    "due_at": state.due_at.isoformat(),
                    "occupancy_duration_seconds": (
                        rule.occupancy_duration_seconds
                    ),
                    "occupancy_threshold": rule.occupancy_threshold,
                },
                zone_id=state.zone_id,
            )
            with session_scope(self._session_factory) as session:
                alert = self._apply_action(action, now=current, session=session)
                if alert is not None:
                    self._states.mark_triggered(
                        state.id, alert_id=alert.id, session=session
                    )
                    triggered.append(alert)
        return triggered

    def _session_still_open(
        self, entity_id: UUID, zone_id: UUID | None
    ) -> bool:
        if self._sessions is None or zone_id is None:
            return True
        open_sess = self._sessions.get_open_session(zone_id, entity_id)
        return open_sess is not None and open_sess.status is ZoneSessionStatus.OPEN

    def _zone_occupancy_meets(
        self, zone_id: UUID | None, threshold: int
    ) -> bool:
        if self._sessions is None or zone_id is None:
            return False
        occupancy = self._sessions.count_open_for_zone(zone_id)
        return occupancy >= threshold

    def _apply_action(
        self,
        action: EvaluationAction,
        *,
        now: datetime,
        session: Session,
    ) -> AlertRecord | None:
        if action.kind == "schedule":
            if action.due_at is None or action.condition_started_at is None:
                return None
            self._states.upsert_pending(
                rule_id=action.rule.id,
                subject_key=action.subject_key,
                entity_id=action.entity_id,
                zone_id=action.zone_id,
                source_event_id=action.source_event_id,
                condition_started_at=action.condition_started_at,
                due_at=action.due_at,
                session=session,
            )
            return None

        if action.kind == "clear_pending":
            self._states.clear(
                action.rule.id, action.subject_key, session=session
            )
            return None

        if action.kind == "resolve_subject":
            open_alert = self._alerts.get_open_for_subject(
                action.rule.id, action.subject_key, session=session
            )
            if open_alert is not None:
                self._resolve_and_notify(
                    open_alert.id, at=now, session=session
                )
            return None

        if action.kind != "trigger":
            return None

        # Cooldown
        last = self._alerts.last_trigger_for_subject(
            action.rule.id, action.subject_key, session=session
        )
        if last is not None:
            elapsed = (now - last).total_seconds()
            if elapsed < action.rule.cooldown_seconds:
                return None

        existing = self._alerts.get_by_idempotency(
            action.idempotency_key, session=session
        )
        if existing is not None:
            return None

        open_alert = self._alerts.get_open_for_subject(
            action.rule.id, action.subject_key, session=session
        )
        if open_alert is not None:
            return None

        alert = self._alerts.create(
            rule_id=action.rule.id,
            severity=action.rule.severity,
            entity_id=action.entity_id,
            zone_id=action.zone_id,
            camera_id=action.camera_id,
            source_event_id=action.source_event_id,
            subject_key=action.subject_key,
            idempotency_key=action.idempotency_key,
            triggered_at=now,
            summary=action.summary,
            payload={
                **action.payload,
                "rule_id": str(action.rule.id),
                "severity": action.rule.severity.value,
            },
            session=session,
        )
        if self._publisher is not None:
            self._publisher.publish_spatial_event(
                session,
                event_id=f"alert-triggered:{alert.id}",
                event_type="alert_triggered",
                occurred_at=alert.triggered_at,
            )
        # Transactional outbox: matching delivery rows are durable alert
        # bookkeeping in *this* session. Failures abort the alert transaction.
        # No HTTP here — worker delivers only after commit.
        self._enqueue_notification(
            alert, event_type="alert_triggered", session=session, now=now
        )
        return alert

    def _resolve_and_notify(
        self,
        alert_id: UUID,
        *,
        at: datetime,
        session: Session,
    ) -> AlertRecord:
        alert = self._alerts.resolve(alert_id, at=at, session=session)
        if (
            self._publisher is not None
            and alert.status.value == "resolved"
            and alert.resolved_at is not None
        ):
            # Only notify when this call actually resolved (or re-resolved).
            self._publisher.publish_spatial_event(
                session,
                event_id=f"alert-resolved:{alert.id}",
                event_type="alert_resolved",
                occurred_at=alert.resolved_at,
            )
        if alert.status.value == "resolved":
            self._enqueue_notification(
                alert, event_type="alert_resolved", session=session, now=at
            )
        return alert

    def _enqueue_notification(
        self,
        alert: AlertRecord,
        *,
        event_type: str,
        session: Session,
        now: datetime,
    ) -> None:
        """Insert required outbox rows in the current alert transaction.

        Local outbox persistence is part of durable alert bookkeeping and
        must succeed or roll back with the alert. External HTTP delivery is
        fully isolated and runs later in the notification worker.
        """

        if self._notification_enqueue is None:
            return
        self._notification_enqueue.enqueue_for_alert(
            alert,
            event_type=event_type,
            session=session,
            now=now,
        )
