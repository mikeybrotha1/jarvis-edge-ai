"""Small rule evaluators behind a minimal interface (v0.8.0)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID

from services.alerts.time_windows import rule_is_active_at
from storage.alert_orm import AlertRuleType
from storage.alert_records import AlertRuleRecord
from storage.timeline_models import TimelineEvent, TimelineEventType


@dataclass(frozen=True, slots=True)
class EvaluationAction:
    """One evaluation outcome to apply transactionally."""

    kind: str  # trigger | schedule | clear_pending | resolve_subject
    rule: AlertRuleRecord
    entity_id: UUID
    subject_key: str
    idempotency_key: str
    source_event_id: str
    summary: str
    payload: dict[str, Any]
    zone_id: UUID | None = None
    camera_id: str | None = None
    due_at: datetime | None = None
    condition_started_at: datetime | None = None


class RuleEvaluator(Protocol):
    def supports(self, rule: AlertRuleRecord) -> bool: ...

    def evaluate(
        self,
        rule: AlertRuleRecord,
        event: TimelineEvent,
        *,
        now: datetime,
    ) -> list[EvaluationAction]: ...


def common_filters_match(rule: AlertRuleRecord, event: TimelineEvent) -> bool:
    if rule.camera_ids:
        if event.camera_id is None or event.camera_id not in rule.camera_ids:
            return False
    if rule.entity_types:
        if event.entity_type not in rule.entity_types:
            return False
    if rule.zone_ids:
        zone_raw = event.payload.get("zone_id")
        if zone_raw is None or str(zone_raw) not in {
            str(z) for z in rule.zone_ids
        }:
            return False
    return True


def parse_zone_id(event: TimelineEvent) -> UUID | None:
    raw = event.payload.get("zone_id")
    if raw is None:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


class EventMatchEvaluator:
    def supports(self, rule: AlertRuleRecord) -> bool:
        return rule.rule_type is AlertRuleType.EVENT_MATCH

    def evaluate(
        self,
        rule: AlertRuleRecord,
        event: TimelineEvent,
        *,
        now: datetime,
    ) -> list[EvaluationAction]:
        if event.event_type.value not in rule.source_event_types:
            return []
        if not common_filters_match(rule, event):
            return []
        if not rule_is_active_at(rule, now):
            return []
        subject = f"{rule.id}:{event.entity_id}"
        return [
            EvaluationAction(
                kind="trigger",
                rule=rule,
                entity_id=event.entity_id,
                subject_key=subject,
                idempotency_key=f"{rule.id}:{event.id}",
                source_event_id=event.id,
                summary=f"Rule {rule.name!r} matched {event.event_type.value}",
                payload={
                    "rule_name": rule.name,
                    "matched_event_type": event.event_type.value,
                },
                zone_id=parse_zone_id(event),
                camera_id=event.camera_id,
            )
        ]


class OccupancyThresholdEvaluator:
    def supports(self, rule: AlertRuleRecord) -> bool:
        return rule.rule_type is AlertRuleType.OCCUPANCY_THRESHOLD

    def evaluate(
        self,
        rule: AlertRuleRecord,
        event: TimelineEvent,
        *,
        now: datetime,
    ) -> list[EvaluationAction]:
        if event.event_type not in {
            TimelineEventType.ZONE_OCCUPANCY_CHANGED,
            TimelineEventType.ZONE_ENTERED,
            TimelineEventType.ZONE_EXITED,
        }:
            return []
        if rule.source_event_types and event.event_type.value not in rule.source_event_types:
            return []
        if not common_filters_match(rule, event):
            return []
        if not rule_is_active_at(rule, now):
            return []
        zone_id = parse_zone_id(event)
        if zone_id is None:
            return []
        occupancy = event.payload.get("occupancy")
        try:
            occupancy_i = int(occupancy)
        except (TypeError, ValueError):
            return []
        threshold = rule.occupancy_threshold or 0
        subject = f"{rule.id}:{zone_id}:{event.entity_id}"
        if occupancy_i < threshold:
            return [
                EvaluationAction(
                    kind="clear_pending",
                    rule=rule,
                    entity_id=event.entity_id,
                    subject_key=subject,
                    idempotency_key=f"{rule.id}:{event.id}:clear",
                    source_event_id=event.id,
                    summary="",
                    payload={},
                    zone_id=zone_id,
                    camera_id=event.camera_id,
                ),
                EvaluationAction(
                    kind="resolve_subject",
                    rule=rule,
                    entity_id=event.entity_id,
                    subject_key=subject,
                    idempotency_key=f"{rule.id}:{event.id}:resolve",
                    source_event_id=event.id,
                    summary="",
                    payload={},
                    zone_id=zone_id,
                    camera_id=event.camera_id,
                ),
            ]

        duration = rule.occupancy_duration_seconds
        # null or 0 => immediate trigger; >0 => sustained pending due_at.
        if duration is None or int(duration) <= 0:
            return [
                EvaluationAction(
                    kind="trigger",
                    rule=rule,
                    entity_id=event.entity_id,
                    subject_key=subject,
                    idempotency_key=f"{rule.id}:{event.id}",
                    source_event_id=event.id,
                    summary=(
                        f"Occupancy {occupancy_i} reached threshold "
                        f"{threshold} for rule {rule.name!r}"
                    ),
                    payload={
                        "rule_name": rule.name,
                        "occupancy": occupancy_i,
                        "threshold": threshold,
                        "occupancy_duration_seconds": 0,
                    },
                    zone_id=zone_id,
                    camera_id=event.camera_id,
                )
            ]

        started = event.occurred_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        due = started + timedelta(seconds=int(duration))
        return [
            EvaluationAction(
                kind="schedule",
                rule=rule,
                entity_id=event.entity_id,
                subject_key=subject,
                idempotency_key=f"{rule.id}:{event.id}:schedule",
                source_event_id=event.id,
                summary="",
                payload={
                    "occupancy": occupancy_i,
                    "threshold": threshold,
                    "occupancy_duration_seconds": int(duration),
                },
                zone_id=zone_id,
                camera_id=event.camera_id,
                due_at=due,
                condition_started_at=started,
            )
        ]


class DwellThresholdEvaluator:
    def supports(self, rule: AlertRuleRecord) -> bool:
        return rule.rule_type is AlertRuleType.DWELL_THRESHOLD

    def evaluate(
        self,
        rule: AlertRuleRecord,
        event: TimelineEvent,
        *,
        now: datetime,
    ) -> list[EvaluationAction]:
        if event.event_type not in {
            TimelineEventType.ZONE_ENTERED,
            TimelineEventType.ZONE_EXITED,
        }:
            return []
        if rule.source_event_types and event.event_type.value not in rule.source_event_types:
            return []
        if not common_filters_match(rule, event):
            return []
        if not rule_is_active_at(rule, now):
            return []
        zone_id = parse_zone_id(event)
        if zone_id is None:
            return []
        subject = f"{rule.id}:{zone_id}:{event.entity_id}"
        seconds = rule.dwell_threshold_seconds or 0
        if event.event_type is TimelineEventType.ZONE_ENTERED:
            due = event.occurred_at + timedelta(seconds=seconds)
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            return [
                EvaluationAction(
                    kind="schedule",
                    rule=rule,
                    entity_id=event.entity_id,
                    subject_key=subject,
                    idempotency_key=f"{rule.id}:{event.id}:schedule",
                    source_event_id=event.id,
                    summary="",
                    payload={},
                    zone_id=zone_id,
                    camera_id=event.camera_id,
                    due_at=due,
                    condition_started_at=event.occurred_at,
                )
            ]
        # zone_exited
        return [
            EvaluationAction(
                kind="clear_pending",
                rule=rule,
                entity_id=event.entity_id,
                subject_key=subject,
                idempotency_key=f"{rule.id}:{event.id}:clear",
                source_event_id=event.id,
                summary="",
                payload={},
                zone_id=zone_id,
                camera_id=event.camera_id,
            ),
            EvaluationAction(
                kind="resolve_subject",
                rule=rule,
                entity_id=event.entity_id,
                subject_key=subject,
                idempotency_key=f"{rule.id}:{event.id}:resolve",
                source_event_id=event.id,
                summary="",
                payload={},
                zone_id=zone_id,
                camera_id=event.camera_id,
            ),
        ]


DEFAULT_EVALUATORS: tuple[RuleEvaluator, ...] = (
    EventMatchEvaluator(),
    OccupancyThresholdEvaluator(),
    DwellThresholdEvaluator(),
)
