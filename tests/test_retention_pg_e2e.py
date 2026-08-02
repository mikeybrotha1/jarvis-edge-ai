"""Live PostgreSQL retention validation (v0.10.0 Phase 7).

Requires a dedicated temporary database — never the operator production
database (e.g. jarvis_vision).

Enable with::

    export JARVIS_RETENTION_PG_E2E_URL=postgresql://jarvis_app:...@127.0.0.1:5432/jarvis_retention_e2e_tmp

Or run ``scripts/retention_pg_e2e_demo.py`` which creates/drops the temp DB.

Skipped when the env var is unset so default CI/local SQLite suites stay offline.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
E2E_URL = os.environ.get("JARVIS_RETENTION_PG_E2E_URL", "").strip()
FORBIDDEN_DB_NAMES = {"jarvis_vision", "teslamate", "postgres"}

pytestmark = pytest.mark.skipif(
    not E2E_URL,
    reason="JARVIS_RETENTION_PG_E2E_URL not set (live PostgreSQL retention e2e)",
)


def _assert_safe_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    db = (parsed.path or "").lstrip("/").split("?")[0]
    if not db or db in FORBIDDEN_DB_NAMES:
        raise RuntimeError(
            f"Refusing retention e2e against database name {db!r}; "
            "use a dedicated temporary database."
        )
    if "retention" not in db and "e2e" not in db and "tmp" not in db:
        raise RuntimeError(
            f"Temp database name {db!r} must contain 'retention', 'e2e', or 'tmp'."
        )
    return url


def _migrate(url: str) -> None:
    env = os.environ.copy()
    env["JARVIS_DATABASE_URL"] = url
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "alembic upgrade failed:\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )


@pytest.fixture(scope="module")
def pg_factory():
    url = _assert_safe_url(E2E_URL)
    _migrate(url)

    from storage.sqlalchemy_db import (
        create_entity_engine,
        create_session_factory,
    )

    engine = create_entity_engine(url)
    factory = create_session_factory(engine)
    yield factory, engine, url
    engine.dispose()


def test_pg_retention_dry_run_then_destructive_guards(pg_factory) -> None:
    """Seed all domains → dry-run (0 deletes) → destructive → verify protected."""

    from config.models import (
        AlertsRetentionPolicy,
        EntitiesRetentionPolicy,
        EvaluatorStateRetentionPolicy,
        NotificationDeliveriesRetentionPolicy,
        ObservationsRetentionPolicy,
        RetentionConfig,
        ZoneSessionsRetentionPolicy,
    )
    from services.ops.retention_worker import RetentionWorker
    from storage.alert_orm import (
        AlertEvaluatorCheckpoint,
        AlertEvaluatorState,
        AlertSeverity,
        AlertStatus,
        EvaluatorStateKind,
    )
    from storage.alert_records import AlertRuleCreate
    from storage.alert_repositories import (
        AlertCheckpointRepository,
        AlertEvaluatorStateRepository,
        AlertRepository,
        AlertRuleRepository,
    )
    from storage.alert_orm import AlertRuleType
    from storage.entity_orm import EntityStatus
    from storage.entity_records import EntityCreate, ObservationCreate
    from storage.entity_repository import EntityRepository
    from storage.entity_zone_session_repository import EntityZoneSessionRepository
    from storage.notification_orm import DeliveryStatus, NotificationDelivery
    from storage.notification_records import NotificationTargetCreate
    from storage.notification_repositories import (
        NotificationDeliveryRepository,
        NotificationTargetRepository,
    )
    from storage.observation_repository import ObservationRepository
    from storage.retention_repository import RetentionRepository
    from storage.sqlalchemy_db import session_scope
    from storage.zone_orm import ZoneSessionStatus
    from storage.zone_records import ZoneCreate
    from storage.zone_repository import ZoneRepository
    from sqlalchemy import func, select, text

    factory, engine, _url = pg_factory
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=100)
    recent = now - timedelta(hours=1)

    entities = EntityRepository(factory)
    observations = ObservationRepository(factory)
    zones = ZoneRepository(factory)
    sessions = EntityZoneSessionRepository(factory)
    rules = AlertRuleRepository(factory)
    alerts = AlertRepository(factory)
    states = AlertEvaluatorStateRepository(factory)
    targets = NotificationTargetRepository(factory)
    deliveries = NotificationDeliveryRepository(factory)
    checkpoints = AlertCheckpointRepository(factory)

    # --- seed eligible (old) + protected (active / recent) ---
    closed_entity = entities.create(
        EntityCreate(
            identity_key=f"closed:{uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label="person",
            track_id=1,
            camera_id="cam-e2e",
            first_seen=old,
            last_seen=old,
            confidence=0.9,
        )
    )
    entities.close(closed_entity.id, last_seen=old)

    active_entity = entities.create(
        EntityCreate(
            identity_key=f"active:{uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label="person",
            track_id=2,
            camera_id="cam-e2e",
            first_seen=recent,
            last_seen=recent,
            confidence=0.9,
        )
    )

    # Orphan-safe closed entity with no alerts/evaluator (entity domain eligible)
    orphan_closed = entities.create(
        EntityCreate(
            identity_key=f"orphan:{uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label="person",
            track_id=3,
            camera_id="cam-e2e",
            first_seen=old,
            last_seen=old,
            confidence=0.9,
        )
    )
    entities.close(orphan_closed.id, last_seen=old)

    for i in range(3):
        observations.append(
            ObservationCreate(
                entity_id=closed_entity.id,
                observed_at=old + timedelta(seconds=i),
                camera_id="cam-e2e",
                confidence=0.9,
                label="person",
                source_event_type="object_entered",
                source_event_id=f"obs-old-{uuid4().hex}",
            )
        )
    recent_obs, _ = observations.append(
        ObservationCreate(
            entity_id=active_entity.id,
            observed_at=recent,
            camera_id="cam-e2e",
            confidence=0.9,
            label="person",
            source_event_type="object_entered",
            source_event_id=f"obs-new-{uuid4().hex}",
        )
    )

    zone = zones.create(
        ZoneCreate(
            name=f"z-{uuid4().hex[:6]}",
            camera_id="cam-e2e",
            vertices=[
                {"x": 0.0, "y": 0.0},
                {"x": 1.0, "y": 0.0},
                {"x": 1.0, "y": 1.0},
                {"x": 0.0, "y": 1.0},
            ],
        )
    )
    closed_sess = sessions.open_session(
        zone_id=zone.id,
        entity_id=closed_entity.id,
        camera_id="cam-e2e",
        entered_at=old,
        occupancy_after_enter=1,
    )
    sessions.close_session(
        closed_sess.id,
        exited_at=old + timedelta(minutes=5),
        occupancy_after_exit=0,
    )
    open_sess = sessions.open_session(
        zone_id=zone.id,
        entity_id=active_entity.id,
        camera_id="cam-e2e",
        entered_at=recent,
        occupancy_after_enter=1,
    )
    assert open_sess.status == ZoneSessionStatus.OPEN

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
        entity_id=active_entity.id,
        zone_id=zone.id,
        camera_id="cam-e2e",
        source_event_id="a-open",
        subject_key=f"e:{active_entity.id}:open",
        idempotency_key=f"i-open-{uuid4().hex}",
        triggered_at=recent,
        summary="open",
        payload={},
    )
    resolved_alert = alerts.create(
        rule_id=rule.id,
        severity=AlertSeverity.WARNING,
        entity_id=closed_entity.id,
        zone_id=zone.id,
        camera_id="cam-e2e",
        source_event_id="a-res",
        subject_key=f"e:{closed_entity.id}:res",
        idempotency_key=f"i-res-{uuid4().hex}",
        triggered_at=old,
        summary="resolved",
        payload={},
    )
    alerts.resolve(resolved_alert.id, at=old)
    acked = alerts.create(
        rule_id=rule.id,
        severity=AlertSeverity.WARNING,
        entity_id=active_entity.id,
        zone_id=None,
        camera_id="cam-e2e",
        source_event_id="a-ack",
        subject_key=f"e:{active_entity.id}:ack",
        idempotency_key=f"i-ack-{uuid4().hex}",
        triggered_at=recent,
        summary="acked",
        payload={},
    )
    alerts.acknowledge(acked.id, at=recent)

    states.upsert_pending(
        rule_id=rule.id,
        subject_key=f"pending:{active_entity.id}",
        entity_id=active_entity.id,
        zone_id=zone.id,
        source_event_id="eval-pending",
        condition_started_at=recent,
        due_at=recent + timedelta(hours=1),
    )
    with session_scope(factory) as session:
        cleared = AlertEvaluatorState(
            id=uuid4(),
            rule_id=rule.id,
            subject_key=f"cleared:{closed_entity.id}",
            entity_id=closed_entity.id,
            zone_id=zone.id,
            source_event_id="eval-cleared",
            condition_started_at=old,
            due_at=old,
            state=EvaluatorStateKind.CLEARED,
        )
        session.add(cleared)
        session.flush()
        cleared.updated_at = old
        cleared_id = cleared.id

    target = targets.create(
        NotificationTargetCreate(
            name=f"t-{uuid4().hex[:6]}",
            url="https://hooks.example.com/e2e",
            is_global=True,
        )
    )
    pending_d = deliveries.create_if_absent(
        alert_id=open_alert.id,
        target_id=target.id,
        event_type="alert_triggered",
        idempotency_key=f"{open_alert.id}:{target.id}:alert_triggered",
        payload={},
        next_attempt_at=recent,
    )
    terminal_d = deliveries.create_if_absent(
        alert_id=resolved_alert.id,
        target_id=target.id,
        event_type="alert_triggered",
        idempotency_key=f"{resolved_alert.id}:{target.id}:alert_triggered",
        payload={},
        next_attempt_at=old,
    )
    assert terminal_d is not None
    with session_scope(factory) as session:
        row = session.get(NotificationDelivery, terminal_d.id)
        assert row is not None
        row.status = DeliveryStatus.DELIVERED
        row.delivered_at = old
        row.updated_at = old

    checkpoints.save(
        "alert-consumer-e2e",
        last_occurred_at=old,
        last_event_id="checkpoint-evt-1",
    )

    cfg = RetentionConfig(
        enabled=True,
        dry_run=True,
        interval_seconds=3600,
        batch_size=50,
        max_batches_per_run=20,
        observations=ObservationsRetentionPolicy(enabled=True, keep_days=1),
        entities=EntitiesRetentionPolicy(enabled=True, keep_closed_days=1),
        zone_sessions=ZoneSessionsRetentionPolicy(enabled=True, keep_closed_days=1),
        alerts=AlertsRetentionPolicy(enabled=True, keep_resolved_days=1),
        evaluator_state=EvaluatorStateRetentionPolicy(
            enabled=True, keep_inactive_days=1
        ),
        notification_deliveries=NotificationDeliveriesRetentionPolicy(
            enabled=True, keep_terminal_days=1
        ),
    )
    repo = RetentionRepository(factory)
    worker = RetentionWorker(factory, cfg, repository=repo)

    # --- dry-run: zero deletions ---
    dry = worker.run_cycle_sync()
    assert dry.dry_run is True
    assert dry.rows_deleted == 0
    assert dry.status in ("ok", "degraded")
    # Eligibility should be non-zero for at least observations
    assert any(d.eligible_total > 0 for d in dry.domains)

    # Counts unchanged after dry-run
    with session_scope(factory) as session:
        n_obs = session.scalar(select(func.count()).select_from(
            __import__("storage.entity_orm", fromlist=["EntityObservation"]).EntityObservation
        ))
        n_cp = session.scalar(
            select(func.count()).select_from(AlertEvaluatorCheckpoint)
        )
    assert n_obs >= 4
    assert n_cp >= 1
    assert entities.get_by_id(active_entity.id) is not None
    assert entities.get_by_id(orphan_closed.id) is not None
    assert alerts.get_by_id(open_alert.id) is not None
    assert alerts.get_by_id(acked.id) is not None
    assert pending_d is None or deliveries.get_by_id(pending_d.id) is not None

    # --- destructive with all guards (dry_run=False via fresh config) ---
    cfg_destructive = RetentionConfig(
        enabled=True,
        dry_run=False,
        interval_seconds=3600,
        batch_size=50,
        max_batches_per_run=20,
        allow_manual_destructive_run=True,
        observations=ObservationsRetentionPolicy(enabled=True, keep_days=1),
        entities=EntitiesRetentionPolicy(enabled=True, keep_closed_days=1),
        zone_sessions=ZoneSessionsRetentionPolicy(enabled=True, keep_closed_days=1),
        alerts=AlertsRetentionPolicy(enabled=True, keep_resolved_days=1),
        evaluator_state=EvaluatorStateRetentionPolicy(
            enabled=True, keep_inactive_days=1
        ),
        notification_deliveries=NotificationDeliveriesRetentionPolicy(
            enabled=True, keep_terminal_days=1
        ),
    )
    worker2 = RetentionWorker(factory, cfg_destructive, repository=repo)
    destructive = worker2.run_cycle_sync()
    assert destructive.dry_run is False
    assert destructive.rows_deleted > 0

    # Eligible removed
    assert repo.count_eligible_observations(cutoff=now - timedelta(days=1)) == 0
    assert repo.count_eligible_alerts(cutoff=now - timedelta(days=1)) == 0
    assert repo.count_eligible_evaluator_states(cutoff=now - timedelta(days=1)) == 0
    assert (
        repo.count_eligible_notification_deliveries(cutoff=now - timedelta(days=1))
        == 0
    )
    assert repo.count_eligible_zone_sessions(cutoff=now - timedelta(days=1)) == 0

    # Protected remain
    assert entities.get_by_id(active_entity.id) is not None
    assert entities.get_by_id(active_entity.id).status == EntityStatus.ACTIVE
    assert alerts.get_by_id(open_alert.id) is not None
    assert alerts.get_by_id(open_alert.id).status == AlertStatus.OPEN
    assert alerts.get_by_id(acked.id) is not None
    assert alerts.get_by_id(acked.id).status == AlertStatus.ACKNOWLEDGED
    assert alerts.get_by_id(resolved_alert.id) is None
    assert sessions.get_by_id(open_sess.id) is not None
    assert sessions.get_by_id(open_sess.id).status == ZoneSessionStatus.OPEN
    pending_state = states.get(rule.id, f"pending:{active_entity.id}")
    assert pending_state is not None
    assert pending_state.state == EvaluatorStateKind.PENDING
    with session_scope(factory) as session:
        assert session.get(AlertEvaluatorState, cleared_id) is None
    if pending_d is not None:
        pd = deliveries.get_by_id(pending_d.id)
        assert pd is not None
        assert pd.status == DeliveryStatus.PENDING
    if terminal_d is not None:
        assert deliveries.get_by_id(terminal_d.id) is None
    # Recent observation survives
    from storage.entity_orm import EntityObservation

    with session_scope(factory) as session:
        assert session.get(EntityObservation, recent_obs.id) is not None
    # Checkpoint never deleted
    cp = checkpoints.get("alert-consumer-e2e")
    assert cp is not None
    assert cp.last_event_id == "checkpoint-evt-1"

    # Orphan closed entity (no dependents) deleted by entities domain
    assert entities.get_by_id(orphan_closed.id) is None

    # closed_entity may remain if residual closed sessions/alerts already
    # pruned — entity cascade-safe only when no alert/eval rows remain.
    # After alerts+eval prune it should be cascade-safe for a second pass.
    worker2.run_cycle_sync()
    remaining_closed = entities.get_by_id(closed_entity.id)
    # Either deleted or blocked only by residual non-eligible children.
    if remaining_closed is not None:
        # Must still be closed; never reopened.
        assert remaining_closed.status == EntityStatus.CLOSED

    # Sanity: app can SELECT 1 on the temp DB
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
