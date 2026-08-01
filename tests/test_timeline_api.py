"""HTTP tests for the activity timeline API (v0.4.2)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app
from services.entity_query_service import EntityQueryService, QueryLimits
from services.timeline_service import TimelineLimits, TimelineService
from storage.entity_orm import EntityStatus
from storage.entity_records import EntityCreate, ObservationCreate
from storage.entity_repository import EntityRepository
from storage.observation_repository import ObservationRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)
from storage.timeline_repository import TimelineRepository


def _build():
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    entities = EntityRepository(factory)
    observations = ObservationRepository(factory)
    query = EntityQueryService(
        entities,
        observations,
        limits=QueryLimits(),
    )
    timeline = TimelineService(
        TimelineRepository(factory),
        entities,
        limits=TimelineLimits(),
    )
    app = create_app(query_service=query, timeline_service=timeline)
    return TestClient(app), entities, observations


def _seed(
    entities: EntityRepository,
    observations: ObservationRepository,
    *,
    track_id: int = 1,
    closed: bool = False,
    with_obs: bool = True,
):
    seen = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    entity = entities.create(
        EntityCreate(
            identity_key=f"camera:front-door:tracker:{track_id}",
            identity_strategy="tracker_id",
            label="person",
            track_id=track_id,
            camera_id="front-door",
            first_seen=seen,
            last_seen=seen,
            confidence=0.9,
        )
    )
    if with_obs:
        observations.append(
            ObservationCreate(
                entity_id=entity.id,
                observed_at=seen,
                camera_id="front-door",
                confidence=0.9,
                label="person",
                source_event_type="vision.object_entered",
                source_event_id=str(uuid4()),
                frame_number=1,
                track_id=track_id,
            )
        )
    if closed:
        entity = entities.close(entity.id, last_seen=seen)
    return entity


def test_default_timeline_lifecycle_only() -> None:
    client, entities, observations = _build()
    _seed(entities, observations, closed=True, with_obs=True)
    response = client.get("/api/v1/timeline")
    assert response.status_code == 200
    body = response.json()
    assert "total" not in body
    assert body["limit"] == 50
    types = {item["event_type"] for item in body["items"]}
    assert "entity_created" in types
    assert "entity_closed" in types
    assert "observation_recorded" not in types
    assert body["items"][0]["occurred_at"].endswith("+00:00") or body[
        "items"
    ][0]["occurred_at"].endswith("Z")


def test_observation_filter_and_get_event() -> None:
    client, entities, observations = _build()
    entity = _seed(entities, observations, closed=False, with_obs=True)
    listing = client.get(
        "/api/v1/timeline",
        params=[
            ("event_type", "entity_created"),
            ("event_type", "observation_recorded"),
        ],
    )
    assert listing.status_code == 200
    types = {item["event_type"] for item in listing.json()["items"]}
    assert "observation_recorded" in types

    event_id = f"entity-created:{entity.id}"
    single = client.get(f"/api/v1/timeline/{event_id}")
    assert single.status_code == 200
    assert single.json()["id"] == event_id


def test_entity_scoped_timeline_and_404() -> None:
    client, entities, observations = _build()
    entity = _seed(entities, observations, closed=True)
    ok = client.get(f"/api/v1/entities/{entity.id}/timeline")
    assert ok.status_code == 200
    assert ok.json()["items"]

    missing = client.get(f"/api/v1/entities/{uuid4()}/timeline")
    assert missing.status_code == 404


def test_unknown_event_404() -> None:
    client, *_ = _build()
    response = client.get(f"/api/v1/timeline/entity-created:{uuid4()}")
    assert response.status_code == 404


def test_cursor_pagination_http() -> None:
    client, entities, observations = _build()
    for track_id in range(1, 8):
        _seed(entities, observations, track_id=track_id, closed=True, with_obs=False)

    first = client.get("/api/v1/timeline", params={"limit": 5, "sort": "asc"})
    assert first.status_code == 200
    body = first.json()
    assert len(body["items"]) == 5
    assert body["next_cursor"]

    second = client.get(
        "/api/v1/timeline",
        params={"limit": 5, "sort": "asc", "cursor": body["next_cursor"]},
    )
    assert second.status_code == 200
    first_ids = {item["id"] for item in body["items"]}
    second_ids = {item["id"] for item in second.json()["items"]}
    assert first_ids.isdisjoint(second_ids)


def test_malformed_cursor_and_inverted_range() -> None:
    client, *_ = _build()
    bad_cursor = client.get(
        "/api/v1/timeline",
        params={"cursor": "%%%bad%%%"},
    )
    assert bad_cursor.status_code == 422

    bad_range = client.get(
        "/api/v1/timeline",
        params={
            "occurred_after": "2026-07-28T13:00:00+00:00",
            "occurred_before": "2026-07-28T12:00:00+00:00",
        },
    )
    assert bad_range.status_code == 422


def test_max_limit_enforcement() -> None:
    client, *_ = _build()
    response = client.get("/api/v1/timeline", params={"limit": 201})
    assert response.status_code == 422


def test_database_errors_sanitized() -> None:
    client, entities, observations = _build()
    # Reach into app state timeline repository.
    from fastapi.testclient import TestClient as _

    _ = _
    # Rebuild with a broken timeline service wrapper via monkeypatch on list.
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    entities = EntityRepository(factory)
    observations = ObservationRepository(factory)
    query = EntityQueryService(entities, observations, limits=QueryLimits())
    timeline_repo = TimelineRepository(factory)
    timeline = TimelineService(timeline_repo, entities, limits=TimelineLimits())

    original = timeline_repo.list_events

    def boom(*args, **kwargs):
        raise RuntimeError("SELECT * FROM secrets WHERE password='x'")

    timeline_repo.list_events = boom  # type: ignore[method-assign]
    app = create_app(query_service=query, timeline_service=timeline)
    try:
        response = TestClient(app).get("/api/v1/timeline")
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "password" not in detail
        assert "SELECT" not in detail
        assert "unavailable" in detail.lower()
    finally:
        timeline_repo.list_events = original  # type: ignore[method-assign]


def test_api_starts_without_hardware() -> None:
    # Constructing the app and hitting /health requires no camera/Hailo.
    client, *_ = _build()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


if __name__ == "__main__":
    test_default_timeline_lifecycle_only()
    test_observation_filter_and_get_event()
    test_entity_scoped_timeline_and_404()
    test_unknown_event_404()
    test_cursor_pagination_http()
    test_malformed_cursor_and_inverted_range()
    test_max_limit_enforcement()
    test_database_errors_sanitized()
    test_api_starts_without_hardware()
    print("Timeline API tests passed.")
