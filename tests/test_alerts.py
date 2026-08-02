"""Durable alerts & rule evaluation tests (v0.8.0)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from services.alerts.evaluation_service import AlertEvaluationService
from services.alerts.rule_validation import (
    RuleValidationError,
    validate_rule_create,
)
from storage.activity_notify import ActivityNotificationPublisher
from storage.alert_orm import AlertRuleType, AlertSeverity, AlertStatus
from storage.alert_records import AlertRuleCreate
from storage.alert_repositories import (
    AlertCheckpointRepository,
    AlertEvaluatorStateRepository,
    AlertRepository,
    AlertRuleRepository,
)
from storage.entity_records import EntityCreate
from storage.entity_repository import EntityRepository
from storage.entity_zone_session_repository import EntityZoneSessionRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)
from storage.timeline_models import TimelineEvent, TimelineEventType
from storage.zone_records import ZoneCreate
from storage.zone_repository import ZoneRepository
from timeline.providers.alert import AlertTimelineProvider
from timeline.provider import TimelineQueryContext


def _factory():
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    return create_session_factory(engine)


def _entity(factory, label="person", camera="cam"):
    repo = EntityRepository(factory)
    now = datetime.now(timezone.utc)
    return repo.create(
        EntityCreate(
            identity_key=f"{camera}:{uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label=label,
            track_id=1,
            camera_id=camera,
            first_seen=now,
            last_seen=now,
            confidence=0.9,
        )
    )


def _zone(factory, camera="cam"):
    return ZoneRepository(factory).create(
        ZoneCreate(
            name=f"z-{uuid4().hex[:6]}",
            camera_id=camera,
            vertices=[
                {"x": 0.1, "y": 0.1},
                {"x": 0.9, "y": 0.1},
                {"x": 0.9, "y": 0.9},
                {"x": 0.1, "y": 0.9},
            ],
        )
    )


def _eval_stack(factory):
    pub = ActivityNotificationPublisher()
    return AlertEvaluationService(
        factory,
        AlertRuleRepository(factory),
        AlertRepository(factory),
        AlertEvaluatorStateRepository(factory),
        activity_publisher=pub,
        session_repository=EntityZoneSessionRepository(factory),
    ), pub


def test_rule_validation_event_match() -> None:
    create = validate_rule_create(
        {
            "name": "enter-door",
            "rule_type": "event_match",
            "source_event_types": ["entity_created"],
            "severity": "warning",
        }
    )
    assert create.rule_type is AlertRuleType.EVENT_MATCH
    with pytest.raises(RuleValidationError):
        validate_rule_create(
            {"name": "bad", "rule_type": "event_match", "source_event_types": []}
        )
    with pytest.raises(RuleValidationError):
        validate_rule_create(
            {
                "name": "bad-tz",
                "rule_type": "event_match",
                "source_event_types": ["entity_created"],
                "timezone": "Not/AZone",
            }
        )


def test_rule_validation_occupancy_and_dwell() -> None:
    validate_rule_create(
        {
            "name": "occ",
            "rule_type": "occupancy_threshold",
            "occupancy_threshold": 2,
            "occupancy_duration_seconds": 1200,
            "zone_ids": [str(uuid4())],
        }
    )
    validate_rule_create(
        {
            "name": "dwell",
            "rule_type": "dwell_threshold",
            "dwell_threshold_seconds": 30,
            "zone_ids": [str(uuid4())],
        }
    )
    with pytest.raises(RuleValidationError):
        validate_rule_create(
            {
                "name": "occ-bad",
                "rule_type": "occupancy_threshold",
                "occupancy_threshold": 0,
                "zone_ids": [str(uuid4())],
            }
        )


def test_event_match_trigger_and_idempotency() -> None:
    factory = _factory()
    entity = _entity(factory)
    rules = AlertRuleRepository(factory)
    rule = rules.create(
        AlertRuleCreate(
            name="match",
            rule_type=AlertRuleType.EVENT_MATCH,
            source_event_types=["entity_created"],
            cooldown_seconds=0,
        )
    )
    service, pub = _eval_stack(factory)
    event = TimelineEvent(
        id=f"entity-created:{entity.id}",
        event_type=TimelineEventType.ENTITY_CREATED,
        occurred_at=datetime.now(timezone.utc),
        source="entity",
        entity_id=entity.id,
        camera_id="cam",
        entity_type="person",
        summary="appeared",
    )
    a1 = service.process_source_event(event)
    assert len(a1) == 1
    a2 = service.process_source_event(event)
    assert a2 == []
    assert any(p["event_type"] == "alert_triggered" for p in pub.captured)
    open_count = AlertRepository(factory).count_open()
    assert open_count == 1


def test_acknowledge_resolve_idempotent() -> None:
    factory = _factory()
    entity = _entity(factory)
    rules = AlertRuleRepository(factory)
    rules.create(
        AlertRuleCreate(
            name="m",
            rule_type=AlertRuleType.EVENT_MATCH,
            source_event_types=["entity_created"],
            cooldown_seconds=0,
        )
    )
    service, _ = _eval_stack(factory)
    event = TimelineEvent(
        id=f"entity-created:{entity.id}",
        event_type=TimelineEventType.ENTITY_CREATED,
        occurred_at=datetime.now(timezone.utc),
        source="entity",
        entity_id=entity.id,
        camera_id="cam",
        entity_type="person",
        summary="x",
    )
    alerts = service.process_source_event(event)
    alert_id = alerts[0].id
    repo = AlertRepository(factory)
    now = datetime.now(timezone.utc)
    a = repo.acknowledge(alert_id, at=now)
    assert a.status is AlertStatus.ACKNOWLEDGED
    a2 = repo.acknowledge(alert_id, at=now)
    assert a2.status is AlertStatus.ACKNOWLEDGED
    r = repo.resolve(alert_id, at=now)
    assert r.status is AlertStatus.RESOLVED
    r2 = repo.resolve(alert_id, at=now)
    assert r2.status is AlertStatus.RESOLVED


def test_dwell_schedule_and_due_reconciler() -> None:
    factory = _factory()
    entity = _entity(factory)
    zone = _zone(factory)
    rules = AlertRuleRepository(factory)
    rules.create(
        AlertRuleCreate(
            name="dwell",
            rule_type=AlertRuleType.DWELL_THRESHOLD,
            dwell_threshold_seconds=5,
            zone_ids=[str(zone.id)],
            cooldown_seconds=0,
        )
    )
    # Open real session so due check sees still-open
    sessions = EntityZoneSessionRepository(factory)
    sessions.open_session(
        zone_id=zone.id,
        entity_id=entity.id,
        camera_id="cam",
        entered_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        occupancy_after_enter=1,
    )
    service, pub = _eval_stack(factory)
    entered = TimelineEvent(
        id=f"zone-entered:{uuid4()}",
        event_type=TimelineEventType.ZONE_ENTERED,
        occurred_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        source="spatial",
        entity_id=entity.id,
        camera_id="cam",
        entity_type="person",
        summary="entered",
        payload={"zone_id": str(zone.id)},
    )
    assert service.process_source_event(entered) == []
    due = service.process_due_states(now=datetime.now(timezone.utc))
    assert len(due) == 1
    assert any(p["event_type"] == "alert_triggered" for p in pub.captured)


def test_occupancy_immediate_and_clear_resolves() -> None:
    factory = _factory()
    entity = _entity(factory)
    zone = _zone(factory)
    rules = AlertRuleRepository(factory)
    rules.create(
        AlertRuleCreate(
            name="occ",
            rule_type=AlertRuleType.OCCUPANCY_THRESHOLD,
            occupancy_threshold=1,
            occupancy_duration_seconds=0,
            zone_ids=[str(zone.id)],
            cooldown_seconds=0,
        )
    )
    service, pub = _eval_stack(factory)
    high = TimelineEvent(
        id=f"zone-occupancy:{uuid4()}:entered",
        event_type=TimelineEventType.ZONE_OCCUPANCY_CHANGED,
        occurred_at=datetime.now(timezone.utc),
        source="spatial",
        entity_id=entity.id,
        camera_id="cam",
        entity_type="person",
        summary="occ",
        payload={"zone_id": str(zone.id), "occupancy": 1},
    )
    triggered = service.process_source_event(high)
    assert len(triggered) == 1
    low = TimelineEvent(
        id=f"zone-occupancy:{uuid4()}:exited",
        event_type=TimelineEventType.ZONE_OCCUPANCY_CHANGED,
        occurred_at=datetime.now(timezone.utc),
        source="spatial",
        entity_id=entity.id,
        camera_id="cam",
        entity_type="person",
        summary="occ",
        payload={"zone_id": str(zone.id), "occupancy": 0},
    )
    service.process_source_event(low)
    open_count = AlertRepository(factory).count_open()
    assert open_count == 0
    assert any(p["event_type"] == "alert_resolved" for p in pub.captured)


def test_occupancy_sustained_duration() -> None:
    factory = _factory()
    entity = _entity(factory)
    zone = _zone(factory)
    # Keep zone occupancy durable so due reconciler can re-check count.
    EntityZoneSessionRepository(factory).open_session(
        zone_id=zone.id,
        entity_id=entity.id,
        camera_id="cam",
        entered_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        occupancy_after_enter=1,
    )
    rules = AlertRuleRepository(factory)
    rules.create(
        AlertRuleCreate(
            name="occ-sustained",
            rule_type=AlertRuleType.OCCUPANCY_THRESHOLD,
            occupancy_threshold=1,
            occupancy_duration_seconds=10,
            zone_ids=[str(zone.id)],
            cooldown_seconds=0,
        )
    )
    service, pub = _eval_stack(factory)
    started = datetime.now(timezone.utc) - timedelta(seconds=15)
    high = TimelineEvent(
        id=f"zone-occupancy:{uuid4()}:entered",
        event_type=TimelineEventType.ZONE_OCCUPANCY_CHANGED,
        occurred_at=started,
        source="spatial",
        entity_id=entity.id,
        camera_id="cam",
        entity_type="person",
        summary="occ",
        payload={"zone_id": str(zone.id), "occupancy": 1},
    )
    # Sustained: schedule only, no immediate trigger.
    assert service.process_source_event(high) == []
    due = service.process_due_states(now=datetime.now(timezone.utc))
    assert len(due) == 1
    assert any(p["event_type"] == "alert_triggered" for p in pub.captured)

    # Drop below threshold auto-resolves.
    low = TimelineEvent(
        id=f"zone-occupancy:{uuid4()}:exited",
        event_type=TimelineEventType.ZONE_OCCUPANCY_CHANGED,
        occurred_at=datetime.now(timezone.utc),
        source="spatial",
        entity_id=entity.id,
        camera_id="cam",
        entity_type="person",
        summary="occ",
        payload={"zone_id": str(zone.id), "occupancy": 0},
    )
    service.process_source_event(low)
    assert AlertRepository(factory).count_open() == 0


def test_occupancy_sustained_clears_before_due() -> None:
    factory = _factory()
    entity = _entity(factory)
    zone = _zone(factory)
    rules = AlertRuleRepository(factory)
    rules.create(
        AlertRuleCreate(
            name="occ-clear",
            rule_type=AlertRuleType.OCCUPANCY_THRESHOLD,
            occupancy_threshold=1,
            occupancy_duration_seconds=60,
            zone_ids=[str(zone.id)],
            cooldown_seconds=0,
        )
    )
    service, _ = _eval_stack(factory)
    high = TimelineEvent(
        id=f"zone-occupancy:{uuid4()}:entered",
        event_type=TimelineEventType.ZONE_OCCUPANCY_CHANGED,
        occurred_at=datetime.now(timezone.utc),
        source="spatial",
        entity_id=entity.id,
        camera_id="cam",
        entity_type="person",
        summary="occ",
        payload={"zone_id": str(zone.id), "occupancy": 1},
    )
    assert service.process_source_event(high) == []
    low = TimelineEvent(
        id=f"zone-occupancy:{uuid4()}:exited",
        event_type=TimelineEventType.ZONE_OCCUPANCY_CHANGED,
        occurred_at=datetime.now(timezone.utc),
        source="spatial",
        entity_id=entity.id,
        camera_id="cam",
        entity_type="person",
        summary="occ",
        payload={"zone_id": str(zone.id), "occupancy": 0},
    )
    service.process_source_event(low)
    # Due should not fire after clear.
    due = service.process_due_states(
        now=datetime.now(timezone.utc) + timedelta(seconds=120)
    )
    assert due == []
    assert AlertRepository(factory).count_open() == 0


def test_alert_timeline_provider() -> None:
    factory = _factory()
    entity = _entity(factory)
    rules = AlertRuleRepository(factory)
    rules.create(
        AlertRuleCreate(
            name="t",
            rule_type=AlertRuleType.EVENT_MATCH,
            source_event_types=["entity_created"],
            cooldown_seconds=0,
        )
    )
    service, _ = _eval_stack(factory)
    event = TimelineEvent(
        id=f"entity-created:{entity.id}",
        event_type=TimelineEventType.ENTITY_CREATED,
        occurred_at=datetime.now(timezone.utc),
        source="entity",
        entity_id=entity.id,
        camera_id="cam",
        entity_type="person",
        summary="x",
    )
    alerts = service.process_source_event(event)
    provider = AlertTimelineProvider(factory)
    listed = provider.list_events(
        TimelineQueryContext(
            event_types=(
                TimelineEventType.ALERT_TRIGGERED,
                TimelineEventType.ALERT_RESOLVED,
            ),
            limit=10,
        )
    )
    assert any(e.event_type is TimelineEventType.ALERT_TRIGGERED for e in listed)
    got = provider.get_event_by_id(f"alert-triggered:{alerts[0].id}")
    assert got is not None
    assert got.payload.get("rule_id")


def test_alert_rest_api() -> None:
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        alerts_config=type(
            "C",
            (),
            {
                "enabled": True,
                "consumer_name": "test",
                "queue_size": 50,
                "reconcile_interval_seconds": 2.0,
                "reconcile_batch_size": 10,
                "replay_overlap_seconds": 1.0,
                "max_rules": 50,
                "default_cooldown_seconds": 0,
                "max_metadata_bytes": 4096,
                "startup_catchup_limit": 50,
                "timezone_default": "UTC",
            },
        )(),
    )
    client = TestClient(app)
    created = client.post(
        "/api/v1/alert-rules",
        json={
            "name": "api-rule",
            "rule_type": "event_match",
            "source_event_types": ["entity_created"],
            "cooldown_seconds": 0,
        },
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    listed = client.get("/api/v1/alert-rules")
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1
    entity = _entity(factory)
    # trigger via evaluation service path
    service, _ = _eval_stack(factory)
    # need rule in same DB - already created via API
    service.process_source_event(
        TimelineEvent(
            id=f"entity-created:{entity.id}",
            event_type=TimelineEventType.ENTITY_CREATED,
            occurred_at=datetime.now(timezone.utc),
            source="entity",
            entity_id=entity.id,
            camera_id="cam",
            entity_type="person",
            summary="x",
        )
    )
    alerts = client.get("/api/v1/alerts")
    assert alerts.status_code == 200
    assert alerts.json()["total"] >= 1
    alert_id = alerts.json()["items"][0]["id"]
    ack = client.post(f"/api/v1/alerts/{alert_id}/acknowledge")
    assert ack.status_code == 200
    assert ack.json()["status"] == "acknowledged"
    res = client.post(f"/api/v1/alerts/{alert_id}/resolve")
    assert res.status_code == 200
    assert res.json()["status"] == "resolved"
    # disable rule
    patched = client.patch(
        f"/api/v1/alert-rules/{rule_id}", json={"enabled": False}
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False


def test_checkpoint_repo() -> None:
    factory = _factory()
    repo = AlertCheckpointRepository(factory)
    now = datetime.now(timezone.utc)
    saved = repo.save("c1", last_occurred_at=now, last_event_id="entity-created:x")
    assert saved.last_event_id == "entity-created:x"
    got = repo.get("c1")
    assert got is not None
    assert got.last_event_id == "entity-created:x"
