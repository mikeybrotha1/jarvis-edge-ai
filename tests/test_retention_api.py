"""Retention control API tests (v0.10.0 phase 5)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app
from config.models import (
    ObservationsRetentionPolicy,
    OpsConfig,
    RetentionConfig,
)
from storage.entity_records import EntityCreate, ObservationCreate
from storage.entity_repository import EntityRepository
from storage.observation_repository import ObservationRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)


def _factory():
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    return create_session_factory(engine), engine


def _ops(
    *,
    enabled: bool = True,
    dry_run: bool = True,
    allow_manual: bool = False,
    observations: bool = True,
    keep_days: int = 1,
    batch_size: int = 50,
    max_batches: int = 4,
) -> OpsConfig:
    return OpsConfig(
        retention=RetentionConfig(
            enabled=enabled,
            dry_run=dry_run,
            interval_seconds=3600,
            batch_size=batch_size,
            max_batches_per_run=max_batches,
            allow_manual_destructive_run=allow_manual,
            observations=ObservationsRetentionPolicy(
                enabled=observations, keep_days=keep_days
            ),
        )
    )


def _seed_old_observations(factory, n: int = 5) -> None:
    entities = EntityRepository(factory)
    observations = ObservationRepository(factory)
    old = datetime.now(timezone.utc) - timedelta(days=10)
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
    for i in range(n):
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


def test_get_retention_contract_disabled() -> None:
    factory, engine = _factory()
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        ops_config=_ops(enabled=False),
    )
    with TestClient(app) as client:
        r = client.get("/api/v1/ops/retention")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is False
        assert body["dry_run"] is True
        assert body["allow_manual_destructive_run"] is False
        assert body["destructive_permitted"] is False
        assert "worker" in body
        assert "domains" in body
        raw = r.text.lower()
        assert "password" not in raw
        assert "postgresql://" not in raw
        assert "traceback" not in raw
    engine.dispose()


def test_dry_run_success_and_zero_deletes() -> None:
    factory, engine = _factory()
    _seed_old_observations(factory, 4)
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        ops_config=_ops(enabled=True, dry_run=False, observations=True),
    )
    with TestClient(app) as client:
        r = client.post("/api/v1/ops/retention/dry-run")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["trigger"] == "dry_run"
        summary = body["summary"]
        assert summary["dry_run"] is True
        assert summary["rows_deleted"] == 0
        assert summary["rows_examined"] >= 1
        assert any(d["domain"] == "observations" for d in summary["domains"])
        # Config still dry_run=false, but force dry-run left rows
        r2 = client.get("/api/v1/ops/status")
        assert "manual_retention_dry_runs_total" in str(
            r2.json().get("metrics", {})
        ) or r2.json()["metrics"]["counters"].get(
            "manual_retention_dry_runs_total", 0
        ) >= 1
    engine.dispose()


def test_dry_run_rejected_when_disabled() -> None:
    factory, engine = _factory()
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        ops_config=_ops(enabled=False),
    )
    with TestClient(app) as client:
        r = client.post("/api/v1/ops/retention/dry-run")
        assert r.status_code == 409
        assert "disabled" in r.json()["detail"].lower()
    engine.dispose()


def test_run_rejected_when_dry_run_true() -> None:
    factory, engine = _factory()
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        ops_config=_ops(
            enabled=True, dry_run=True, allow_manual=True, observations=True
        ),
    )
    with TestClient(app) as client:
        r = client.post("/api/v1/ops/retention/run")
        assert r.status_code == 409
        assert "dry_run" in r.json()["detail"].lower()
    engine.dispose()


def test_run_rejected_without_manual_guard() -> None:
    factory, engine = _factory()
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        ops_config=_ops(
            enabled=True, dry_run=False, allow_manual=False, observations=True
        ),
    )
    with TestClient(app) as client:
        r = client.post("/api/v1/ops/retention/run")
        assert r.status_code == 403
        assert "not permitted" in r.json()["detail"].lower()
    engine.dispose()


def test_destructive_run_success() -> None:
    factory, engine = _factory()
    _seed_old_observations(factory, 3)
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        ops_config=_ops(
            enabled=True,
            dry_run=False,
            allow_manual=True,
            observations=True,
            keep_days=1,
        ),
    )
    # Bypass cooldown for sequential tests by clearing last trigger
    app.state.retention_worker.last_manual_trigger_at = None
    with TestClient(app) as client:
        r = client.post("/api/v1/ops/retention/run")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["trigger"] == "run"
        assert body["summary"]["dry_run"] is False
        assert body["summary"]["rows_deleted"] == 3
        assert (
            client.app.state.ops_metrics.snapshot()["counters"].get(
                "manual_retention_runs_total", 0
            )
            >= 1
        )
    engine.dispose()


def test_cooldown_rate_limit() -> None:
    factory, engine = _factory()
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        ops_config=_ops(enabled=True, dry_run=True, observations=True),
    )
    with TestClient(app) as client:
        r1 = client.post("/api/v1/ops/retention/dry-run")
        assert r1.status_code == 200
        r2 = client.post("/api/v1/ops/retention/dry-run")
        assert r2.status_code == 429
        assert "rate" in r2.json()["detail"].lower()
        assert (
            client.app.state.ops_metrics.snapshot()["counters"].get(
                "manual_retention_rejected_total", 0
            )
            >= 1
        )
    engine.dispose()


def test_overlap_rejection() -> None:
    factory, engine = _factory()
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        ops_config=_ops(enabled=True, dry_run=True, observations=True),
    )
    worker = app.state.retention_worker
    # Simulate active cycle
    import asyncio

    async def hold():
        async with worker._cycle_lock:
            await asyncio.sleep(0.01)

    # Hold lock synchronously for try_run_cycle path
    worker._cycle_lock = type(worker._cycle_lock)()  # fresh lock
    # Mark locked by acquiring without release via monkeypatch locked()
    class _Locked:
        def locked(self):
            return True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    worker._cycle_lock = _Locked()  # type: ignore[assignment]
    worker.last_manual_trigger_at = None
    with TestClient(app) as client:
        r = client.post("/api/v1/ops/retention/dry-run")
        assert r.status_code == 409
        assert "already active" in r.json()["detail"].lower()
    engine.dispose()


def test_no_request_body_overrides() -> None:
    factory, engine = _factory()
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        ops_config=_ops(enabled=True, dry_run=True, observations=True),
    )
    with TestClient(app) as client:
        # Extra body is ignored / not used for policy (endpoint accepts none)
        r = client.post(
            "/api/v1/ops/retention/dry-run",
            json={
                "batch_size": 9999,
                "domains": ["everything"],
                "sql": "DELETE FROM entities",
            },
        )
        # Still succeeds; body not applied (FastAPI ignores unexpected body by default for no-param endpoints)
        assert r.status_code in (200, 422)
        if r.status_code == 200:
            assert r.json()["summary"]["rows_deleted"] == 0
    engine.dispose()


def test_batch_bounds_preserved_on_manual_run() -> None:
    factory, engine = _factory()
    _seed_old_observations(factory, 10)
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        ops_config=_ops(
            enabled=True,
            dry_run=False,
            allow_manual=True,
            observations=True,
            batch_size=2,
            max_batches=2,
            keep_days=1,
        ),
    )
    app.state.retention_worker.last_manual_trigger_at = None
    with TestClient(app) as client:
        r = client.post("/api/v1/ops/retention/run")
        assert r.status_code == 200
        # 2 batches * 2 = 4 deleted max
        assert r.json()["summary"]["rows_deleted"] == 4
        obs = next(
            d
            for d in r.json()["summary"]["domains"]
            if d["domain"] == "observations"
        )
        assert obs["batches"] == 2
    engine.dispose()


def test_startup_with_retention_disabled() -> None:
    factory, engine = _factory()
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        ops_config=_ops(enabled=False),
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/ops/retention").status_code == 200
        assert client.post("/api/v1/ops/retention/run").status_code == 409
    engine.dispose()
