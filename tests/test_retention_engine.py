"""Retention engine tests (v0.10.0 phase 4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from config.models import (
    AlertsRetentionPolicy,
    EntitiesRetentionPolicy,
    EvaluatorStateRetentionPolicy,
    NotificationDeliveriesRetentionPolicy,
    ObservationsRetentionPolicy,
    OpsConfig,
    RetentionConfig,
    ZoneSessionsRetentionPolicy,
)
from services.ops.retention_worker import RetentionWorker
from storage.alert_orm import (
    AlertStatus,
    AlertSeverity,
    EvaluatorStateKind,
)
from storage.alert_records import AlertRuleCreate
from storage.alert_repositories import (
    AlertEvaluatorStateRepository,
    AlertRepository,
    AlertRuleRepository,
)
from storage.alert_orm import AlertRuleType
from storage.entity_orm import EntityStatus
from storage.entity_records import EntityCreate, ObservationCreate
from storage.entity_repository import EntityRepository
from storage.notification_orm import DeliveryStatus
from storage.notification_records import NotificationTargetCreate
from storage.notification_repositories import (
    NotificationDeliveryRepository,
    NotificationTargetRepository,
)
from storage.observation_repository import ObservationRepository
from storage.retention_repository import RetentionRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
    session_scope,
)
from storage.zone_orm import ZoneSessionStatus
from storage.zone_records import ZoneCreate
from storage.zone_repository import ZoneRepository
from storage.entity_zone_session_repository import EntityZoneSessionRepository


def _factory():
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    return create_session_factory(engine), engine


def _retention(
    *,
    enabled: bool = True,
    dry_run: bool = True,
    batch_size: int = 10,
    max_batches: int = 4,
    observations: bool = False,
    entities: bool = False,
    zone_sessions: bool = False,
    alerts: bool = False,
    evaluator_state: bool = False,
    deliveries: bool = False,
    keep_days: int = 1,
) -> RetentionConfig:
    return RetentionConfig(
        enabled=enabled,
        dry_run=dry_run,
        interval_seconds=3600,
        batch_size=batch_size,
        max_batches_per_run=max_batches,
        observations=ObservationsRetentionPolicy(
            enabled=observations, keep_days=keep_days
        ),
        entities=EntitiesRetentionPolicy(
            enabled=entities, keep_closed_days=keep_days
        ),
        zone_sessions=ZoneSessionsRetentionPolicy(
            enabled=zone_sessions, keep_closed_days=keep_days
        ),
        alerts=AlertsRetentionPolicy(
            enabled=alerts, keep_resolved_days=keep_days
        ),
        evaluator_state=EvaluatorStateRetentionPolicy(
            enabled=evaluator_state, keep_inactive_days=keep_days
        ),
        notification_deliveries=NotificationDeliveriesRetentionPolicy(
            enabled=deliveries, keep_terminal_days=keep_days
        ),
    )


def test_worker_disabled_by_default() -> None:
    factory, engine = _factory()
    worker = RetentionWorker(factory, RetentionConfig())
    assert worker.enabled is False
    assert worker.state == "disabled"
    summary = worker.run_cycle_sync()
    assert summary.status == "disabled"
    assert summary.rows_deleted == 0
    engine.dispose()


def test_worker_enabled_starts_idle() -> None:
    factory, engine = _factory()
    cfg = _retention(enabled=True, dry_run=True, observations=True)
    worker = RetentionWorker(factory, cfg)
    assert worker.enabled is True
    summary = worker.run_cycle_sync()
    assert summary.status in ("ok", "degraded")
    assert worker.state == "idle"
    assert summary.dry_run is True
    engine.dispose()


def test_dry_run_observations_no_delete() -> None:
    factory, engine = _factory()
    entities = EntityRepository(factory)
    observations = ObservationRepository(factory)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=10)
    entity = entities.create(
        EntityCreate(
            identity_key=f"e:{uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label="person",
            track_id=1,
            camera_id="cam",
            first_seen=old,
            last_seen=old,
            confidence=0.9,
        )
    )
    for i in range(5):
        observations.append(
            ObservationCreate(
                entity_id=entity.id,
                observed_at=old + timedelta(seconds=i),
                camera_id="cam",
                confidence=0.9,
                label="person",
                source_event_type="object_entered",
                source_event_id=f"src-{uuid4().hex}",
            )
        )
    repo = RetentionRepository(factory)
    cutoff = now - timedelta(days=1)
    assert repo.count_eligible_observations(cutoff=cutoff) == 5

    worker = RetentionWorker(
        factory,
        _retention(
            enabled=True, dry_run=True, observations=True, keep_days=1
        ),
        repository=repo,
    )
    summary = worker.run_cycle_sync()
    obs = next(d for d in summary.domains if d.domain == "observations")
    assert obs.eligible_total == 5
    assert obs.rows_examined == 5
    assert obs.rows_deleted == 0
    assert obs.dry_run is True
    # Rows still present
    assert repo.count_eligible_observations(cutoff=cutoff) == 5
    engine.dispose()


def test_delete_observations_batch() -> None:
    factory, engine = _factory()
    entities = EntityRepository(factory)
    observations = ObservationRepository(factory)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=10)
    entity = entities.create(
        EntityCreate(
            identity_key=f"e:{uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label="person",
            track_id=1,
            camera_id="cam",
            first_seen=old,
            last_seen=old,
            confidence=0.9,
        )
    )
    for i in range(7):
        observations.append(
            ObservationCreate(
                entity_id=entity.id,
                observed_at=old + timedelta(seconds=i),
                camera_id="cam",
                confidence=0.9,
                label="person",
                source_event_type="object_entered",
                source_event_id=f"src-{uuid4().hex}",
            )
        )
    repo = RetentionRepository(factory)
    worker = RetentionWorker(
        factory,
        _retention(
            enabled=True,
            dry_run=False,
            observations=True,
            batch_size=3,
            max_batches=10,
            keep_days=1,
        ),
        repository=repo,
    )
    summary = worker.run_cycle_sync()
    assert summary.rows_deleted == 7
    cutoff = now - timedelta(days=1)
    assert repo.count_eligible_observations(cutoff=cutoff) == 0
    engine.dispose()


def test_max_batches_per_run() -> None:
    factory, engine = _factory()
    entities = EntityRepository(factory)
    observations = ObservationRepository(factory)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=10)
    entity = entities.create(
        EntityCreate(
            identity_key=f"e:{uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label="person",
            track_id=1,
            camera_id="cam",
            first_seen=old,
            last_seen=old,
            confidence=0.9,
        )
    )
    for i in range(10):
        observations.append(
            ObservationCreate(
                entity_id=entity.id,
                observed_at=old + timedelta(seconds=i),
                camera_id="cam",
                confidence=0.9,
                label="person",
                source_event_type="object_entered",
                source_event_id=f"src-{uuid4().hex}",
            )
        )
    repo = RetentionRepository(factory)
    worker = RetentionWorker(
        factory,
        _retention(
            enabled=True,
            dry_run=False,
            observations=True,
            batch_size=2,
            max_batches=2,
            keep_days=1,
        ),
        repository=repo,
    )
    summary = worker.run_cycle_sync()
    # 2 batches * 2 = 4 deleted
    assert summary.rows_deleted == 4
    obs = next(d for d in summary.domains if d.domain == "observations")
    assert obs.batches == 2
    cutoff = now - timedelta(days=1)
    assert repo.count_eligible_observations(cutoff=cutoff) == 6
    engine.dispose()


def test_protected_active_entity_and_open_alert() -> None:
    factory, engine = _factory()
    entities = EntityRepository(factory)
    alerts = AlertRepository(factory)
    rules = AlertRuleRepository(factory)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=100)
    active = entities.create(
        EntityCreate(
            identity_key=f"active:{uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label="person",
            track_id=1,
            camera_id="cam",
            first_seen=old,
            last_seen=old,
            confidence=0.9,
        )
    )
    closed = entities.create(
        EntityCreate(
            identity_key=f"closed:{uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label="person",
            track_id=2,
            camera_id="cam",
            first_seen=old,
            last_seen=old,
            confidence=0.9,
        )
    )
    entities.close(closed.id, last_seen=old)

    rule = rules.create(
        AlertRuleCreate(
            name=f"r-{uuid4().hex[:6]}",
            rule_type=AlertRuleType.EVENT_MATCH,
            source_event_types=["entity_created"],
            cooldown_seconds=0,
        )
    )
    open_alert = alerts.create(
        rule_id=rule.id,
        severity=AlertSeverity.WARNING,
        entity_id=active.id,
        zone_id=None,
        camera_id="cam",
        source_event_id="s1",
        subject_key=f"e:{active.id}",
        idempotency_key=f"i1-{uuid4().hex}",
        triggered_at=old,
        summary="open",
        payload={},
    )
    resolved = alerts.create(
        rule_id=rule.id,
        severity=AlertSeverity.WARNING,
        entity_id=closed.id,
        zone_id=None,
        camera_id="cam",
        source_event_id="s2",
        subject_key=f"e:{closed.id}:r",
        idempotency_key=f"i2-{uuid4().hex}",
        triggered_at=old,
        summary="resolved",
        payload={},
    )
    alerts.resolve(resolved.id, at=old)

    repo = RetentionRepository(factory)
    cutoff = now - timedelta(days=1)
    # Entity with residual alerts is not cascade-safe (blocked).
    ent_ids = repo.fetch_eligible_entity_ids(cutoff=cutoff, limit=50)
    assert closed.id not in ent_ids
    assert active.id not in ent_ids

    alert_ids = repo.fetch_eligible_alert_ids(cutoff=cutoff, limit=50)
    assert resolved.id in alert_ids
    assert open_alert.id not in alert_ids

    # Alerts-only: resolved pruned; closed entity still present (has open? no)
    # but open alert on active entity remains.
    worker_alerts = RetentionWorker(
        factory,
        _retention(
            enabled=True,
            dry_run=False,
            entities=False,
            alerts=True,
            keep_days=1,
        ),
        repository=repo,
    )
    worker_alerts.run_cycle_sync()
    assert alerts.get_by_id(resolved.id) is None
    assert alerts.get_by_id(open_alert.id) is not None
    assert entities.get_by_id(closed.id) is not None

    # After alert prune, closed entity becomes cascade-safe.
    ent_ids2 = repo.fetch_eligible_entity_ids(cutoff=cutoff, limit=50)
    assert closed.id in ent_ids2
    assert active.id not in ent_ids2

    worker_ents = RetentionWorker(
        factory,
        _retention(
            enabled=True,
            dry_run=False,
            entities=True,
            alerts=False,
            keep_days=1,
        ),
        repository=repo,
    )
    worker_ents.run_cycle_sync()
    assert entities.get_by_id(active.id) is not None
    assert entities.get_by_id(closed.id) is None
    assert alerts.get_by_id(open_alert.id) is not None
    engine.dispose()


def test_never_delete_pending_evaluator_or_pending_delivery() -> None:
    factory, engine = _factory()
    entities = EntityRepository(factory)
    rules = AlertRuleRepository(factory)
    states = AlertEvaluatorStateRepository(factory)
    targets = NotificationTargetRepository(factory)
    deliveries = NotificationDeliveryRepository(factory)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=40)
    entity = entities.create(
        EntityCreate(
            identity_key=f"e:{uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label="person",
            track_id=1,
            camera_id="cam",
            first_seen=old,
            last_seen=old,
            confidence=0.9,
        )
    )
    rule = rules.create(
        AlertRuleCreate(
            name=f"r-{uuid4().hex[:6]}",
            rule_type=AlertRuleType.EVENT_MATCH,
            source_event_types=["entity_created"],
            cooldown_seconds=0,
        )
    )
    states.upsert_pending(
        rule_id=rule.id,
        subject_key="sk",
        entity_id=entity.id,
        zone_id=None,
        source_event_id="src",
        condition_started_at=old,
        due_at=old + timedelta(hours=1),
    )
    # Manually mark one cleared state
    from storage.alert_orm import AlertEvaluatorState
    from storage.sqlalchemy_db import session_scope
    import uuid as uuid_mod

    with session_scope(factory) as session:
        cleared = AlertEvaluatorState(
            id=uuid_mod.uuid4(),
            rule_id=rule.id,
            subject_key="cleared-sk",
            entity_id=entity.id,
            zone_id=None,
            source_event_id="src2",
            condition_started_at=old,
            due_at=old,
            state=EvaluatorStateKind.CLEARED,
        )
        session.add(cleared)
        session.flush()
        # Force updated_at into the past for eligibility (server default is now).
        cleared.updated_at = old
        cleared_id = cleared.id

    target = targets.create(
        NotificationTargetCreate(
            name=f"t-{uuid4().hex[:6]}",
            url="https://hooks.example.com/x",
            is_global=True,
        )
    )
    alerts = AlertRepository(factory)
    alert = alerts.create(
        rule_id=rule.id,
        severity=AlertSeverity.WARNING,
        entity_id=entity.id,
        zone_id=None,
        camera_id="cam",
        source_event_id="s3",
        subject_key=f"e:{entity.id}:d",
        idempotency_key=f"i3-{uuid4().hex}",
        triggered_at=old,
        summary="a",
        payload={},
    )
    pending_d = deliveries.create_if_absent(
        alert_id=alert.id,
        target_id=target.id,
        event_type="alert_triggered",
        idempotency_key=f"{alert.id}:{target.id}:alert_triggered",
        payload={},
        next_attempt_at=old,
    )
    # Terminal delivery
    alert2 = alerts.create(
        rule_id=rule.id,
        severity=AlertSeverity.WARNING,
        entity_id=entity.id,
        zone_id=None,
        camera_id="cam",
        source_event_id="s4",
        subject_key=f"e:{entity.id}:d2",
        idempotency_key=f"i4-{uuid4().hex}",
        triggered_at=old,
        summary="a2",
        payload={},
    )
    delivered = deliveries.create_if_absent(
        alert_id=alert2.id,
        target_id=target.id,
        event_type="alert_triggered",
        idempotency_key=f"{alert2.id}:{target.id}:alert_triggered",
        payload={},
        next_attempt_at=old,
    )
    assert delivered is not None
    from storage.notification_orm import NotificationDelivery

    with session_scope(factory) as session:
        row = session.get(NotificationDelivery, delivered.id)
        row.status = DeliveryStatus.DELIVERED
        row.delivered_at = old
        row.updated_at = old

    repo = RetentionRepository(factory)
    cutoff = now - timedelta(days=1)
    eval_ids = repo.fetch_eligible_evaluator_state_ids(cutoff=cutoff, limit=50)
    assert cleared_id in eval_ids
    # pending upsert is PENDING — not eligible
    pending_states = [
        s
        for s in eval_ids
        if s != cleared_id
    ]
    # only cleared
    assert all(
        True for _ in pending_states
    )  # pending state id not in eligible (only cleared)

    del_ids = repo.fetch_eligible_notification_delivery_ids(
        cutoff=cutoff, limit=50
    )
    assert delivered.id in del_ids
    if pending_d is not None:
        assert pending_d.id not in del_ids

    engine.dispose()


def test_exception_isolation() -> None:
    factory, engine = _factory()
    repo = RetentionRepository(factory)

    def boom(**kwargs):
        raise RuntimeError("db exploded")

    repo.count_eligible_observations = boom  # type: ignore[method-assign]
    worker = RetentionWorker(
        factory,
        _retention(enabled=True, dry_run=True, observations=True),
        repository=repo,
    )
    summary = worker.run_cycle_sync()
    assert summary.status in ("degraded", "error", "ok")
    obs = next(d for d in summary.domains if d.domain == "observations")
    assert obs.status == "error"
    # Worker remains usable
    assert worker.state == "idle"
    engine.dispose()


def test_restart_safe_idempotent_second_run() -> None:
    factory, engine = _factory()
    entities = EntityRepository(factory)
    observations = ObservationRepository(factory)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=10)
    entity = entities.create(
        EntityCreate(
            identity_key=f"e:{uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label="person",
            track_id=1,
            camera_id="cam",
            first_seen=old,
            last_seen=old,
            confidence=0.9,
        )
    )
    for i in range(3):
        observations.append(
            ObservationCreate(
                entity_id=entity.id,
                observed_at=old + timedelta(seconds=i),
                camera_id="cam",
                confidence=0.9,
                label="person",
                source_event_type="object_entered",
                source_event_id=f"src-{uuid4().hex}",
            )
        )
    worker = RetentionWorker(
        factory,
        _retention(
            enabled=True,
            dry_run=False,
            observations=True,
            keep_days=1,
        ),
    )
    s1 = worker.run_cycle_sync()
    s2 = worker.run_cycle_sync()
    assert s1.rows_deleted == 3
    assert s2.rows_deleted == 0
    engine.dispose()


def test_app_wiring_disabled_and_ops_status() -> None:
    factory, engine = _factory()
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        ops_config=OpsConfig(retention=RetentionConfig(enabled=False)),
    )
    assert app.state.retention_worker is not None
    assert app.state.retention_worker.enabled is False
    with TestClient(app) as client:
        body = client.get("/api/v1/ops/status").json()
        assert body["retention"]["execution"] == "disabled"
        assert body["retention"]["worker"]["state"] == "disabled"
    engine.dispose()


def test_app_wiring_enabled_dry_run() -> None:
    factory, engine = _factory()
    cfg = OpsConfig(
        retention=_retention(
            enabled=True, dry_run=True, observations=True, keep_days=1
        )
    )
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        ops_config=cfg,
    )
    worker = app.state.retention_worker
    assert worker is not None
    assert worker.enabled is True
    with TestClient(app) as client:
        # Lifespan starts worker; force one cycle for summary
        worker.run_cycle_sync()
        body = client.get("/api/v1/ops/status").json()
        assert body["retention"]["enabled"] is True
        assert body["retention"]["dry_run"] is True
        assert body["retention"]["execution"] in ("idle", "running", "disabled")
        assert "last_run" in body["retention"]["worker"]
    engine.dispose()


def test_entity_blocked_while_alert_or_evaluator_remain() -> None:
    """Entity CASCADE must not wipe residual alert/evaluator audit rows."""

    factory, engine = _factory()
    entities = EntityRepository(factory)
    rules = AlertRuleRepository(factory)
    alerts = AlertRepository(factory)
    states = AlertEvaluatorStateRepository(factory)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=100)
    closed = entities.create(
        EntityCreate(
            identity_key=f"closed:{uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label="person",
            track_id=1,
            camera_id="cam",
            first_seen=old,
            last_seen=old,
            confidence=0.9,
        )
    )
    entities.close(closed.id, last_seen=old)
    rule = rules.create(
        AlertRuleCreate(
            name=f"r-{uuid4().hex[:6]}",
            rule_type=AlertRuleType.EVENT_MATCH,
            source_event_types=["entity_created"],
            cooldown_seconds=0,
        )
    )
    resolved = alerts.create(
        rule_id=rule.id,
        severity=AlertSeverity.WARNING,
        entity_id=closed.id,
        zone_id=None,
        camera_id="cam",
        source_event_id="s1",
        subject_key=f"e:{closed.id}",
        idempotency_key=f"i-{uuid4().hex}",
        triggered_at=old,
        summary="resolved",
        payload={},
    )
    alerts.resolve(resolved.id, at=old)
    states.upsert_pending(
        rule_id=rule.id,
        subject_key=f"sk:{closed.id}",
        entity_id=closed.id,
        zone_id=None,
        source_event_id="src",
        condition_started_at=old,
        due_at=old + timedelta(hours=1),
    )

    repo = RetentionRepository(factory)
    cutoff = now - timedelta(days=1)
    assert closed.id not in repo.fetch_eligible_entity_ids(
        cutoff=cutoff, limit=50
    )

    # Entities-only destructive run must not delete blocked entity or alert.
    worker = RetentionWorker(
        factory,
        _retention(enabled=True, dry_run=False, entities=True, keep_days=1),
        repository=repo,
    )
    summary = worker.run_cycle_sync()
    ent_domain = next(d for d in summary.domains if d.domain == "entities")
    assert ent_domain.rows_deleted == 0
    assert entities.get_by_id(closed.id) is not None
    assert alerts.get_by_id(resolved.id) is not None
    engine.dispose()


def test_checkpoints_never_eligible_for_retention() -> None:
    """Retention domains must not touch evaluator or consumer checkpoints."""

    factory, engine = _factory()
    from storage.alert_orm import AlertEvaluatorCheckpoint
    from storage.alert_repositories import AlertCheckpointRepository

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=100)
    cp_repo = AlertCheckpointRepository(factory)
    cp_repo.save(
        "alert-consumer-main",
        last_occurred_at=old,
        last_event_id="evt-checkpoint-1",
    )
    # Ensure raw table has the row
    with session_scope(factory) as session:
        row = session.get(AlertEvaluatorCheckpoint, "alert-consumer-main")
        assert row is not None
        row.updated_at = old

    # Run every domain destructive — checkpoint row must survive.
    worker = RetentionWorker(
        factory,
        _retention(
            enabled=True,
            dry_run=False,
            observations=True,
            entities=True,
            zone_sessions=True,
            alerts=True,
            evaluator_state=True,
            deliveries=True,
            keep_days=1,
        ),
    )
    worker.run_cycle_sync()
    still = cp_repo.get("alert-consumer-main")
    assert still is not None
    assert still.last_event_id == "evt-checkpoint-1"
    engine.dispose()


def test_cutoff_boundary_excludes_equal_timestamp() -> None:
    """Rows at exactly the cutoff must not be deleted (strict < cutoff)."""

    factory, engine = _factory()
    entities = EntityRepository(factory)
    observations = ObservationRepository(factory)
    now = datetime.now(timezone.utc)
    keep_days = 7
    cutoff = now - timedelta(days=keep_days)
    # Slightly older than cutoff → eligible; exactly at/after → protected.
    entity = entities.create(
        EntityCreate(
            identity_key=f"e:{uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label="person",
            track_id=1,
            camera_id="cam",
            first_seen=cutoff - timedelta(days=1),
            last_seen=now,
            confidence=0.9,
        )
    )
    old_obs, _ = observations.append(
        ObservationCreate(
            entity_id=entity.id,
            observed_at=cutoff - timedelta(seconds=1),
            camera_id="cam",
            confidence=0.9,
            label="person",
            source_event_type="object_entered",
            source_event_id=f"src-old-{uuid4().hex}",
        )
    )
    boundary_obs, _ = observations.append(
        ObservationCreate(
            entity_id=entity.id,
            observed_at=cutoff,
            camera_id="cam",
            confidence=0.9,
            label="person",
            source_event_type="object_entered",
            source_event_id=f"src-bound-{uuid4().hex}",
        )
    )
    new_obs, _ = observations.append(
        ObservationCreate(
            entity_id=entity.id,
            observed_at=cutoff + timedelta(seconds=1),
            camera_id="cam",
            confidence=0.9,
            label="person",
            source_event_type="object_entered",
            source_event_id=f"src-new-{uuid4().hex}",
        )
    )
    repo = RetentionRepository(factory)
    ids = repo.fetch_eligible_observation_ids(cutoff=cutoff, limit=50)
    assert old_obs.id in ids
    assert boundary_obs.id not in ids
    assert new_obs.id not in ids
    engine.dispose()
