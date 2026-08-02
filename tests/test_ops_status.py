"""Operational status and metrics tests (v0.10.0 phases 1–2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app
from config.models import AlertsConfig, NotificationsConfig
from services.ops.metrics import OpsMetricsRegistry
from services.ops.status import ComponentStatus, OpsStatusCollector
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)


def _factory():
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    return create_session_factory(engine), engine


def test_health_preserved() -> None:
    factory, engine = _factory()
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        alerts_config=AlertsConfig(enabled=False),
        notifications_config=NotificationsConfig(enabled=False),
    )
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["service"] == "jarvis-entity-query-api"
    engine.dispose()


def test_ready_healthy_with_database() -> None:
    factory, engine = _factory()
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        alerts_config=AlertsConfig(enabled=False),
        notifications_config=NotificationsConfig(enabled=False),
    )
    with TestClient(app) as client:
        r = client.get("/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["ready"] is True
        assert body["status"] == "ready"
        assert body["checks"]["database"] == "healthy"
    engine.dispose()


def test_ready_unavailable_database() -> None:
    factory, engine = _factory()
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        alerts_config=AlertsConfig(enabled=False),
        notifications_config=NotificationsConfig(enabled=False),
    )
    # Break DB connectivity for the collector only.
    app.state.session_factory = None
    app.state.ops_status_collector = OpsStatusCollector(session_factory=None)
    with TestClient(app) as client:
        r = client.get("/ready")
        assert r.status_code == 503
        body = r.json()
        assert body["ready"] is False
        assert body["status"] == "not_ready"
        assert body["checks"]["database"] == "unavailable"
    engine.dispose()


def test_ops_status_disabled_components() -> None:
    factory, engine = _factory()
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        alerts_config=AlertsConfig(enabled=False),
        notifications_config=NotificationsConfig(enabled=False),
    )
    with TestClient(app) as client:
        r = client.get("/api/v1/ops/status")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] in ("healthy", "degraded")
        assert "components" in body
        assert body["components"]["database"]["status"] == "healthy"
        assert body["components"]["activity_listener"]["status"] == "disabled"
        assert body["components"]["alert_consumer"]["status"] == "disabled"
        assert body["components"]["due_reconciler"]["status"] == "disabled"
        assert body["components"]["notification_worker"]["status"] == "disabled"
        # No secrets / DSNs
        raw = r.text.lower()
        assert "password" not in raw
        assert "postgresql://" not in raw
        assert "traceback" not in raw
        metrics = body["metrics"]
        assert "counters" in metrics
        assert "gauges" in metrics
        assert "bounds" in metrics
    engine.dispose()


def test_ops_status_enabled_components_report_health() -> None:
    factory, engine = _factory()
    app = create_app(
        session_factory=factory,
        enable_activity_stream=True,
        alerts_config=AlertsConfig(enabled=True),
        notifications_config=NotificationsConfig(enabled=True),
    )
    with TestClient(app) as client:
        r = client.get("/api/v1/ops/status")
        assert r.status_code == 200
        body = r.json()
        comps = body["components"]
        assert comps["database"]["status"] == "healthy"
        assert comps["timeline_composition"]["status"] == "healthy"
        # Broker-only SQLite path for activity stream
        assert comps["activity_listener"]["status"] in (
            "healthy",
            "degraded",
            "disabled",
        )
        assert comps["alert_consumer"]["status"] in ("healthy", "degraded")
        assert comps["due_reconciler"]["status"] in ("healthy", "degraded")
        assert comps["notification_worker"]["status"] in ("healthy", "degraded")
    engine.dispose()


def test_disabled_not_unavailable_in_collector() -> None:
    factory, engine = _factory()
    collector = OpsStatusCollector(session_factory=factory)
    consumer = MagicMock()
    consumer.enabled = False
    body = collector.check_alert_consumer(consumer)
    assert body["status"] == ComponentStatus.DISABLED.value

    worker = MagicMock()
    worker.enabled = False
    body = collector.check_notification_worker(worker)
    assert body["status"] == ComponentStatus.DISABLED.value

    body = collector.check_activity_listener(
        stream_enabled=False,
        listener=None,
        broker=None,
        stream_ready=False,
    )
    assert body["status"] == ComponentStatus.DISABLED.value
    engine.dispose()


def test_degraded_alert_consumer() -> None:
    factory, engine = _factory()
    collector = OpsStatusCollector(session_factory=factory)
    consumer = MagicMock()
    consumer.enabled = True
    consumer.is_ready = True
    consumer.is_degraded = True
    consumer.consumer_name = "test"
    consumer.stats.return_value = {
        "queue_depth": 3,
        "dropped": 2,
        "last_success_at": datetime.now(timezone.utc),
    }
    body = collector.check_alert_consumer(consumer)
    assert body["status"] == ComponentStatus.DEGRADED.value
    assert body["queue_depth"] == 3
    engine.dispose()


def test_due_reconciler_historical_error_not_permanent() -> None:
    """A past error followed by success must not keep the component degraded."""

    factory, engine = _factory()
    collector = OpsStatusCollector(session_factory=factory)
    now = datetime.now(timezone.utc)
    reconciler = MagicMock()
    reconciler.enabled = True
    reconciler.is_running = True
    reconciler.stats.return_value = {
        "running": True,
        "error_count": 5,
        "last_error_at": now - timedelta(minutes=10),
        "last_success_at": now - timedelta(seconds=5),
        "iterations": 100,
    }
    body = collector.check_due_reconciler(reconciler)
    assert body["status"] == ComponentStatus.HEALTHY.value
    engine.dispose()


def test_due_reconciler_recent_error_degrades() -> None:
    factory, engine = _factory()
    collector = OpsStatusCollector(session_factory=factory)
    now = datetime.now(timezone.utc)
    reconciler = MagicMock()
    reconciler.enabled = True
    reconciler.is_running = True
    reconciler.stats.return_value = {
        "running": True,
        "error_count": 1,
        "last_error_at": now - timedelta(seconds=2),
        "last_success_at": now - timedelta(minutes=1),
        "iterations": 10,
    }
    body = collector.check_due_reconciler(reconciler)
    assert body["status"] == ComponentStatus.DEGRADED.value
    engine.dispose()


def test_metrics_bounds_and_no_high_cardinality() -> None:
    reg = OpsMetricsRegistry()
    for i in range(100):
        reg.inc(f"counter_{i}")
        reg.set_gauge(f"gauge_{i}", float(i))
        reg.mark_success(f"success_{i}")
        reg.observe_latency_ms(f"lat_{i}", float(i))
    snap = reg.snapshot()
    assert len(snap["counters"]) <= 64
    assert len(snap["gauges"]) <= 64
    assert len(snap["last_success_at"]) <= 64
    assert len(snap["latencies_ms"]) <= 64
    # Counter clamp still works
    reg.inc("counter_0", amount=10**20)
    assert reg.snapshot()["counters"].get("counter_0", 0) <= 2**63 - 1


def test_ops_status_no_camera_hailo_imports() -> None:
    import api.ops_routes as routes
    import services.ops.status as status_mod

    for mod in (routes, status_mod):
        src = open(mod.__file__, encoding="utf-8").read().lower()
        assert "hailo" not in src
        # camera only allowed as camera_id field names in other modules
        assert "from camera" not in src
        assert "import camera" not in src


def test_overall_unavailable_when_database_down() -> None:
    collector = OpsStatusCollector(session_factory=None)
    state = MagicMock()
    state.timeline_service = MagicMock()
    state.timeline_service.list_timeline.return_value = MagicMock(items=[])
    state.activity_stream_enabled = False
    state.activity_listener = None
    state.activity_broker = None
    state.activity_stream_ready = False
    state.alert_consumer = None
    state.alert_reconciler = None
    state.notification_worker = None
    body = collector.collect(state)
    assert body["status"] == "unavailable"
    assert body["components"]["database"]["status"] == "unavailable"


def test_missing_timeline_and_disabled_activity() -> None:
    factory, engine = _factory()
    collector = OpsStatusCollector(session_factory=factory)
    state = MagicMock()
    state.timeline_service = None
    state.activity_stream_enabled = False
    state.activity_listener = None
    state.activity_broker = None
    state.activity_stream_ready = False
    state.alert_consumer = None
    state.alert_reconciler = None
    state.notification_worker = None
    state.retention_config = None
    state.ops_config = None
    state.retention_worker = None
    body = collector.collect(state)
    assert body["components"]["timeline_composition"]["status"] in (
        ComponentStatus.DISABLED.value,
        ComponentStatus.UNAVAILABLE.value,
        ComponentStatus.DEGRADED.value,
    )
    assert body["components"]["activity_listener"]["status"] == ComponentStatus.DISABLED.value
    engine.dispose()


def test_activity_listener_degraded() -> None:
    factory, engine = _factory()
    collector = OpsStatusCollector(session_factory=factory)
    listener = MagicMock()
    listener.is_ready = False
    body = collector.check_activity_listener(
        stream_enabled=True,
        listener=listener,
        broker=None,
        stream_ready=False,
    )
    assert body["status"] == ComponentStatus.DEGRADED.value
    engine.dispose()


def test_notification_worker_unavailable_and_degraded() -> None:
    factory, engine = _factory()
    collector = OpsStatusCollector(session_factory=factory)
    none_body = collector.check_notification_worker(None)
    assert none_body["status"] == ComponentStatus.DISABLED.value

    worker = MagicMock()
    worker.enabled = True
    worker.is_ready = False
    worker.is_degraded = False
    worker.stats.return_value = {"ready": False, "pending": 1}
    not_ready = collector.check_notification_worker(worker)
    assert not_ready["status"] == ComponentStatus.DEGRADED.value

    worker.is_ready = True
    worker.is_degraded = True
    worker.stats.return_value = {"ready": True, "degraded": True, "pending": 2}
    degraded = collector.check_notification_worker(worker)
    assert degraded["status"] == ComponentStatus.DEGRADED.value
    engine.dispose()


def test_retention_worker_states_in_summary() -> None:
    factory, engine = _factory()
    from config.models import OpsConfig, RetentionConfig

    collector = OpsStatusCollector(session_factory=factory)
    for state_name, enabled in (
        ("disabled", False),
        ("idle", True),
        ("failed", True),
    ):
        state = MagicMock()
        state.timeline_service = MagicMock()
        state.timeline_service.list_timeline.return_value = MagicMock(items=[])
        state.activity_stream_enabled = False
        state.activity_listener = None
        state.activity_broker = None
        state.activity_stream_ready = False
        state.alert_consumer = None
        state.alert_reconciler = None
        state.notification_worker = None
        state.ops_config = OpsConfig(
            retention=RetentionConfig(enabled=enabled, dry_run=True)
        )
        state.retention_config = state.ops_config.retention
        worker = MagicMock()
        worker.stats.return_value = {
            "state": state_name if enabled else "disabled",
            "last_error": "cycle_failed" if state_name == "failed" else None,
            "cycles_completed": 0 if state_name != "idle" else 1,
            "last_run": None,
        }
        state.retention_worker = worker if enabled else None
        body = collector.collect(state)
        assert "retention" in body
        assert body["retention"]["enabled"] is enabled
        if enabled:
            assert body["retention"]["worker"]["state"] == state_name
    engine.dispose()
