#!/usr/bin/env python3
"""Lightweight ops endpoint latency micro-benchmark (no heavy deps).

Starts an in-process TestClient against create_app (SQLite memory) by default,
or hits a live base URL when JARVIS_BENCH_BASE_URL is set.

Reports p50/p95/max for:
  GET /health
  GET /ready
  GET /api/v1/ops/status
  GET /api/v1/ops/retention

Also times one retention dry-run and one destructive cycle against in-memory
seeded data when running local mode.

Usage::

    PYTHONPATH=src python scripts/ops_latency_bench.py
    JARVIS_BENCH_BASE_URL=http://127.0.0.1:8000 PYTHONPATH=src \\
        python scripts/ops_latency_bench.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

ITERATIONS = int(os.environ.get("JARVIS_BENCH_ITERS", "30"))


def _pct(samples: list[float], p: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def _summarize(name: str, samples_ms: list[float]) -> dict:
    return {
        "endpoint": name,
        "n": len(samples_ms),
        "p50_ms": round(_pct(samples_ms, 50), 2),
        "p95_ms": round(_pct(samples_ms, 95), 2),
        "max_ms": round(max(samples_ms) if samples_ms else 0.0, 2),
        "mean_ms": round(statistics.fmean(samples_ms) if samples_ms else 0.0, 2),
    }


def _bench_http(get_fn, path: str, n: int) -> list[float]:
    # Warmup
    for _ in range(3):
        get_fn(path)
    samples: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        resp = get_fn(path)
        dt = (time.perf_counter() - t0) * 1000.0
        if getattr(resp, "status_code", 200) >= 500:
            raise RuntimeError(f"{path} returned {resp.status_code}")
        samples.append(dt)
    return samples


def _local_app():
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

    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    cfg = OpsConfig(
        retention=RetentionConfig(
            enabled=True,
            dry_run=True,
            interval_seconds=3600,
            batch_size=50,
            max_batches_per_run=4,
            observations=ObservationsRetentionPolicy(enabled=True, keep_days=1),
        )
    )
    app = create_app(
        session_factory=factory,
        enable_activity_stream=False,
        ops_config=cfg,
    )
    # Seed a few observations for retention timing.
    entities = EntityRepository(factory)
    observations = ObservationRepository(factory)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=10)
    ent = entities.create(
        EntityCreate(
            identity_key=f"bench:{uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label="person",
            track_id=1,
            camera_id="cam",
            first_seen=old,
            last_seen=old,
            confidence=0.9,
        )
    )
    for i in range(100):
        observations.append(
            ObservationCreate(
                entity_id=ent.id,
                observed_at=old + timedelta(seconds=i),
                camera_id="cam",
                confidence=0.9,
                label="person",
                source_event_type="object_entered",
                source_event_id=f"bench-{uuid4().hex}",
            )
        )
    client = TestClient(app)
    return client, app, engine, factory


def main() -> int:
    base = os.environ.get("JARVIS_BENCH_BASE_URL", "").strip().rstrip("/")
    results: dict = {"mode": "live" if base else "local", "iterations": ITERATIONS}

    if base:
        import urllib.request

        def get(path: str):
            class R:
                def __init__(self, code: int, body: bytes):
                    self.status_code = code
                    self._body = body

                def json(self):
                    return json.loads(self._body.decode())

            req = urllib.request.Request(base + path, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                return R(resp.status, resp.read())

        client_get = get
        app = None
        engine = None
    else:
        client, app, engine, _factory = _local_app()
        client_get = client.get

    endpoints = [
        "/health",
        "/ready",
        "/api/v1/ops/status",
        "/api/v1/ops/retention",
    ]
    summaries = []
    for path in endpoints:
        samples = _bench_http(client_get, path, ITERATIONS)
        summaries.append(_summarize(path, samples))
    results["endpoints"] = summaries

    if not base and app is not None:
        worker = app.state.retention_worker
        assert worker is not None
        t0 = time.perf_counter()
        dry = worker.run_cycle_sync()
        dry_ms = (time.perf_counter() - t0) * 1000.0
        # Destructive on remaining
        from config.models import ObservationsRetentionPolicy, RetentionConfig

        worker.config = RetentionConfig(  # type: ignore[attr-defined]
            enabled=True,
            dry_run=False,
            interval_seconds=3600,
            batch_size=50,
            max_batches_per_run=10,
            observations=ObservationsRetentionPolicy(enabled=True, keep_days=1),
        )
        # RetentionWorker reads config from self._config — use proper path
        from services.ops.retention_worker import RetentionWorker
        from storage.retention_repository import RetentionRepository
        from storage.sqlalchemy_db import create_session_factory

        factory = create_session_factory(engine)
        # Re-bind worker config by constructing a destructive worker
        # Prefer mutating known attribute if present
        destructive_worker = RetentionWorker(
            factory,
            RetentionConfig(
                enabled=True,
                dry_run=False,
                interval_seconds=3600,
                batch_size=50,
                max_batches_per_run=10,
                observations=ObservationsRetentionPolicy(enabled=True, keep_days=1),
            ),
            repository=RetentionRepository(factory),
        )
        t1 = time.perf_counter()
        dest = destructive_worker.run_cycle_sync()
        dest_ms = (time.perf_counter() - t1) * 1000.0

        # Latency during a retention cycle (sample /health)
        during: list[float] = []
        import threading

        stop = threading.Event()

        def hammer():
            while not stop.is_set():
                t = time.perf_counter()
                client_get("/health")
                during.append((time.perf_counter() - t) * 1000.0)

        th = threading.Thread(target=hammer, daemon=True)
        th.start()
        destructive_worker.run_cycle_sync()
        time.sleep(0.2)
        stop.set()
        th.join(timeout=2)

        results["retention"] = {
            "dry_run_ms": round(dry_ms, 2),
            "dry_run_rows_examined": dry.rows_examined
            if hasattr(dry, "rows_examined")
            else sum(d.rows_examined for d in dry.domains),
            "destructive_ms": round(dest_ms, 2),
            "destructive_rows_deleted": dest.rows_deleted,
            "health_during_cleanup": _summarize(
                "/health@retention", during[:200]
            ),
        }

    print(json.dumps(results, indent=2))
    # Soft acceptance hints
    ok = True
    for row in results["endpoints"]:
        if row["p95_ms"] > 200:
            print(
                f"WARN: {row['endpoint']} p95={row['p95_ms']}ms exceeds 200ms target",
                file=sys.stderr,
            )
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
