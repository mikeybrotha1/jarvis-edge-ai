"""PostgreSQL regression: VARCHAR(36) identifiers + UUID Python values.

Skipped unless JARVIS_DATABASE_URL is a live PostgreSQL DSN. Creates
isolated smoke rows, does not drop shared schema.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

from services.alerts.evaluation_service import AlertEvaluationService
from services.notifications.enqueue import NotificationEnqueueService
from storage.activity_notify import ActivityNotificationPublisher
from storage.alert_orm import Alert, AlertRuleType, AlertSeverity
from storage.alert_records import AlertRuleCreate
from storage.alert_repositories import (
    AlertEvaluatorStateRepository,
    AlertRepository,
    AlertRuleRepository,
)
from storage.entity_records import EntityCreate
from storage.entity_repository import EntityRepository
from storage.notification_orm import DeliveryStatus
from storage.notification_records import (
    DeliveryListFilter,
    NotificationTargetCreate,
)
from storage.notification_repositories import (
    NotificationDeliveryRepository,
    NotificationTargetRepository,
)
from storage.sqlalchemy_db import create_entity_engine, create_session_factory
from storage.timeline_models import TimelineEvent, TimelineEventType


def _pg_url() -> str | None:
    url = os.environ.get("JARVIS_DATABASE_URL", "").strip()
    if url.startswith(("postgresql://", "postgres://")):
        return url
    return None


pytestmark = pytest.mark.skipif(
    _pg_url() is None,
    reason="JARVIS_DATABASE_URL PostgreSQL DSN not set",
)


@pytest.fixture
def pg_stack():
    url = _pg_url()
    assert url is not None
    engine = create_entity_engine(url)
    # Do not create_entity_schema — use released VARCHAR migration schema.
    factory = create_session_factory(engine)
    yield factory, engine
    engine.dispose()


def test_pg_column_types_are_varchar_not_uuid(pg_stack) -> None:
    _factory, engine = pg_stack
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT column_name, data_type, character_maximum_length
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'alerts'
                  AND column_name IN ('id', 'rule_id', 'entity_id', 'zone_id')
                ORDER BY column_name
                """
            )
        ).all()
    by_name = {r[0]: (r[1], r[2]) for r in rows}
    for col in ("id", "rule_id", "entity_id", "zone_id"):
        assert col in by_name, f"missing alerts.{col}"
        data_type, length = by_name[col]
        assert data_type in ("character varying", "varchar", "text"), (
            f"alerts.{col} expected VARCHAR, got {data_type}"
        )
        if data_type != "text" and length is not None:
            assert int(length) == 36


def test_pg_last_trigger_and_eval_without_uuid_cast(pg_stack) -> None:
    factory, _engine = pg_stack
    rules = AlertRuleRepository(factory)
    alerts = AlertRepository(factory)
    entities = EntityRepository(factory)
    targets = NotificationTargetRepository(factory)
    deliveries = NotificationDeliveryRepository(factory)
    enqueue = NotificationEnqueueService(targets, deliveries)

    now = datetime.now(timezone.utc)
    suffix = uuid.uuid4().hex[:10]
    entity = entities.create(
        EntityCreate(
            identity_key=f"pg-smoke:{suffix}",
            identity_strategy="tracker_id",
            label="person",
            track_id=99,
            camera_id="pg_smoke",
            first_seen=now,
            last_seen=now,
            confidence=0.9,
        )
    )
    rule = rules.create(
        AlertRuleCreate(
            name=f"pg-smoke-rule-{suffix}",
            rule_type=AlertRuleType.EVENT_MATCH,
            source_event_types=["entity_created"],
            severity=AlertSeverity.WARNING,
            cooldown_seconds=0,
        )
    )
    assert isinstance(rule.id, uuid.UUID)

    # 1–2: UUID in service code vs VARCHAR predicates
    assert alerts.last_trigger_for_subject(rule.id, f"e:{entity.id}") is None
    assert alerts.get_open_for_subject(rule.id, f"e:{entity.id}") is None

    # Global target for transactional outbox (use public host; no network)
    targets.create(
        NotificationTargetCreate(
            name=f"pg-smoke-target-{suffix}",
            url=f"https://hooks.example.com/pg-smoke-{suffix}",
            is_global=True,
            enabled=True,
            severity_filters=[],
        )
    )

    eval_svc = AlertEvaluationService(
        factory,
        rules,
        alerts,
        AlertEvaluatorStateRepository(factory),
        activity_publisher=ActivityNotificationPublisher(),
        notification_enqueue=enqueue,
    )
    event = TimelineEvent(
        id=f"entity-created:{entity.id}",
        event_type=TimelineEventType.ENTITY_CREATED,
        occurred_at=now,
        source="entity",
        entity_id=entity.id,
        camera_id="pg_smoke",
        entity_type="person",
        summary="pg smoke created",
    )
    # 3–4: catch-up style process without datatype mismatch
    triggered = eval_svc.process_source_event(event)
    assert len(triggered) == 1
    alert = triggered[0]
    assert alert.rule_id == rule.id
    assert alert.entity_id == entity.id

    page = deliveries.list_deliveries(
        DeliveryListFilter(alert_id=alert.id, limit=10)
    )
    assert page.total >= 1
    assert all(d.status is DeliveryStatus.PENDING for d in page.items)

    # 5: API-shaped list still works
    listed = alerts.list_alerts(
        __import__(
            "storage.alert_records", fromlist=["AlertListFilter"]
        ).AlertListFilter(rule_id=rule.id, limit=10)
    )
    assert listed.total >= 1

    # 6: compiled SQL for open lookup has no ::UUID
    stmt = (
        select(Alert)
        .where(Alert.rule_id == rule.id)
        .where(Alert.subject_key == f"e:{entity.id}")
    )
    from sqlalchemy.dialects import postgresql

    sql = str(stmt.compile(dialect=postgresql.dialect())).upper().replace(
        " ", ""
    )
    assert "::UUID" not in sql
