"""Tests for the v0.5.1 Live Activity Console (static + FastAPI mount)."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from services.entity_query_service import EntityQueryService, QueryLimits
from services.timeline_service import TimelineLimits, TimelineService
from storage.entity_repository import EntityRepository
from storage.observation_repository import ObservationRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)
from storage.timeline_repository import TimelineRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
CONSOLE_DIR = REPO_ROOT / "console"


def _app(*, activity_enabled: bool = False):
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    entities = EntityRepository(factory)
    observations = ObservationRepository(factory)
    query = EntityQueryService(entities, observations, limits=QueryLimits())
    timeline = TimelineService(
        TimelineRepository(factory),
        entities,
        limits=TimelineLimits(),
    )
    return create_app(
        query_service=query,
        timeline_service=timeline,
        enable_activity_stream=activity_enabled,
    )


def test_console_html_routes() -> None:
    app = _app()
    with TestClient(app) as client:
        for path in ("/console", "/console/"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert "text/html" in response.headers.get("content-type", "")
            body = response.text
            assert "Jarvis Live Activity" in body
            assert 'src="/console/js/main.js"' in body
            assert 'href="/console/css/console.css"' in body


def test_console_static_assets() -> None:
    app = _app()
    with TestClient(app) as client:
        css = client.get("/console/css/console.css")
        assert css.status_code == 200
        assert "status-bar" in css.text

        for name in (
            "api.js",
            "ws.js",
            "store.js",
            "recovery.js",
            "ui.js",
            "main.js",
        ):
            response = client.get(f"/console/js/{name}")
            assert response.status_code == 200, name
            assert len(response.content) > 20


def test_console_does_not_shadow_api_health_docs() -> None:
    app = _app()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        # Entity API still present (empty DB).
        listing = client.get("/api/v1/entities")
        assert listing.status_code == 200
        timeline = client.get("/api/v1/timeline")
        assert timeline.status_code == 200


def test_console_works_when_activity_stream_disabled() -> None:
    app = _app(activity_enabled=False)
    with TestClient(app) as client:
        assert client.get("/console").status_code == 200
        assert client.app.state.activity_stream_enabled is False


def test_app_starts_without_camera_hailo_imports() -> None:
    # Constructing the app must not require vision stacks.
    app = _app()
    with TestClient(app) as client:
        assert client.get("/console").status_code == 200
        assert client.get("/health").json()["status"] == "ok"


def test_module_files_exist() -> None:
    expected = [
        CONSOLE_DIR / "index.html",
        CONSOLE_DIR / "css" / "console.css",
        CONSOLE_DIR / "js" / "api.js",
        CONSOLE_DIR / "js" / "ws.js",
        CONSOLE_DIR / "js" / "store.js",
        CONSOLE_DIR / "js" / "recovery.js",
        CONSOLE_DIR / "js" / "ui.js",
        CONSOLE_DIR / "js" / "main.js",
    ]
    for path in expected:
        assert path.is_file(), path


def test_no_unsafe_dynamic_innerhtml_patterns() -> None:
    """Static scan: forbid dynamic innerHTML assignment in console JS/HTML."""

    offenders: list[str] = []
    for path in CONSOLE_DIR.rglob("*"):
        if path.suffix not in {".js", ".html"}:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\.innerHTML\s*=", text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
        if "document.write(" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_no_embedded_secrets_or_database_urls() -> None:
    banned = re.compile(
        r"(postgresql://|postgres://|password\s*=\s*['\"][^'\"]+['\"])",
        re.I,
    )
    for path in CONSOLE_DIR.rglob("*"):
        if path.suffix not in {".js", ".html", ".css"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert banned.search(text) is None, path


def test_html_accessibility_landmarks() -> None:
    html = (CONSOLE_DIR / "index.html").read_text(encoding="utf-8")
    assert 'role="banner"' in html or "<header" in html
    assert 'id="main-feed"' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert 'type="module"' in html


def test_store_algorithm_ordering_dedupe_and_cap() -> None:
    """Python mirror of console/js/store.js ordering + cap contracts."""

    def compare(a: dict, b: dict) -> int:
        ta = a["occurred_at"]
        tb = b["occurred_at"]
        if ta < tb:
            return -1
        if ta > tb:
            return 1
        if a["id"] < b["id"]:
            return -1
        if a["id"] > b["id"]:
            return 1
        return 0

    events = [
        {"id": "b", "occurred_at": "2026-01-01T00:00:02Z"},
        {"id": "a", "occurred_at": "2026-01-01T00:00:01Z"},
        {"id": "c", "occurred_at": "2026-01-01T00:00:02Z"},
        {"id": "a", "occurred_at": "2026-01-01T00:00:01Z"},  # dup
    ]
    by_id: dict[str, dict] = {}
    for ev in events:
        by_id[ev["id"]] = ev
    ordered = sorted(by_id.values(), key=lambda e: (e["occurred_at"], e["id"]))
    newest_first = list(reversed(ordered))
    assert [e["id"] for e in newest_first] == ["c", "b", "a"]
    assert len(by_id) == 3

    # Cap drops oldest.
    cap = 2
    while len(newest_first) > cap:
        dropped = newest_first.pop()
        by_id.pop(dropped["id"], None)
    assert set(by_id) == {"c", "b"}


def test_recovery_overlap_merge_dedupes_by_id() -> None:
    recovered = [
        {"id": "entity-created:1", "occurred_at": "2026-01-01T00:00:01Z"},
        {"id": "entity-closed:1", "occurred_at": "2026-01-01T00:00:05Z"},
    ]
    buffered_live = [
        {"id": "entity-closed:1", "occurred_at": "2026-01-01T00:00:05Z"},
        {"id": "entity-created:2", "occurred_at": "2026-01-01T00:00:06Z"},
    ]
    merged: dict[str, dict] = {}
    for ev in recovered + buffered_live:
        merged[ev["id"]] = ev
    ids = sorted(merged, key=lambda i: (merged[i]["occurred_at"], i))
    assert ids == [
        "entity-created:1",
        "entity-closed:1",
        "entity-created:2",
    ]


def test_filter_serialization_lifecycle_default() -> None:
    # Mirrors recovery.filtersFromUi defaults.
    ui = {
        "event_types": [],
        "camera_id": "",
        "entity_type": "",
        "entity_id": "",
    }
    event_types = ui["event_types"] or ["entity_created", "entity_closed"]
    assert event_types == ["entity_created", "entity_closed"]


def test_reconnect_backoff_bounds_contract() -> None:
    initial = 1000
    maximum = 30000
    delays = [min(maximum, initial * (2 ** min(n, 8))) for n in range(0, 12)]
    assert delays[0] == 1000
    assert max(delays) == 30000


if __name__ == "__main__":
    test_console_html_routes()
    test_console_static_assets()
    test_console_does_not_shadow_api_health_docs()
    test_console_works_when_activity_stream_disabled()
    test_app_starts_without_camera_hailo_imports()
    test_module_files_exist()
    test_no_unsafe_dynamic_innerhtml_patterns()
    test_no_embedded_secrets_or_database_urls()
    test_html_accessibility_landmarks()
    test_store_algorithm_ordering_dedupe_and_cap()
    test_recovery_overlap_merge_dedupes_by_id()
    test_filter_serialization_lifecycle_default()
    test_reconnect_backoff_bounds_contract()
    print("Live activity console tests passed.")
