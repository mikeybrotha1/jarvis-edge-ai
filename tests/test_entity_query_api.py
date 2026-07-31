"""HTTP tests for the entity query API (v0.4.1)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app
from services.entity_query_service import EntityQueryService, QueryLimits
from storage.entity_orm import EntityStatus
from storage.entity_records import EntityCreate, ObservationCreate
from storage.entity_repository import EntityRepository
from storage.observation_repository import ObservationRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)


def _build_client():
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    entities = EntityRepository(factory)
    observations = ObservationRepository(factory)
    service = EntityQueryService(
        entities,
        observations,
        limits=QueryLimits(
            entity_default_limit=50,
            entity_maximum_limit=200,
            observation_default_limit=100,
            observation_maximum_limit=500,
        ),
    )
    app = create_app(query_service=service)
    client = TestClient(app)
    return client, entities, observations


def _seed(
    entities: EntityRepository,
    observations: ObservationRepository,
    *,
    track_id: int = 1,
    label: str = "person",
    camera_id: str = "azure_kinect",
    last_seen: datetime | None = None,
    status: EntityStatus = EntityStatus.ACTIVE,
):
    seen = last_seen or datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    entity = entities.create(
        EntityCreate(
            identity_key=f"camera:{camera_id}:tracker:{track_id}",
            identity_strategy="tracker_id",
            label=label,
            track_id=track_id,
            camera_id=camera_id,
            first_seen=seen,
            last_seen=seen,
            confidence=0.88,
            bounding_box={"x1": 0, "y1": 0, "x2": 10, "y2": 10},
        )
    )
    observations.append(
        ObservationCreate(
            entity_id=entity.id,
            observed_at=seen,
            camera_id=camera_id,
            confidence=0.88,
            label=label,
            source_event_type="vision.object_entered",
            source_event_id=str(uuid4()),
            frame_number=7,
            track_id=track_id,
            bounding_box={"x1": 0, "y1": 0, "x2": 10, "y2": 10},
        )
    )
    if status is EntityStatus.CLOSED:
        entity = entities.close(entity.id, last_seen=seen)
    return entity


def test_health_endpoint() -> None:
    client, *_ = _build_client()
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "jarvis-entity-query-api"


def test_list_entities_and_retrieve_one() -> None:
    client, entities, observations = _build_client()
    entity = _seed(entities, observations)

    listing = client.get("/api/v1/entities")
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["total"] == 1
    assert payload["limit"] == 50
    assert payload["offset"] == 0
    assert payload["items"][0]["id"] == str(entity.id)
    assert payload["items"][0]["entity_type"] == "person"

    single = client.get(f"/api/v1/entities/{entity.id}")
    assert single.status_code == 200
    body = single.json()
    assert body["id"] == str(entity.id)
    assert body["status"] == "active"
    # timezone-aware ISO 8601
    assert body["first_seen"].endswith("+00:00") or body[
        "first_seen"
    ].endswith("Z")


def test_404_behavior() -> None:
    client, *_ = _build_client()
    missing = uuid4()
    response = client.get(f"/api/v1/entities/{missing}")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "not found" in detail.lower()
    assert "sql" not in detail.lower()
    assert "traceback" not in detail.lower()


def test_observations_endpoint() -> None:
    client, entities, observations = _build_client()
    entity = _seed(entities, observations)
    response = client.get(f"/api/v1/entities/{entity.id}/observations")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["entity_id"] == str(entity.id)
    assert body["items"][0]["frame_number"] == 7
    assert body["items"][0]["observed_at"]


def test_active_and_recent_endpoints() -> None:
    client, entities, observations = _build_client()
    _seed(
        entities,
        observations,
        track_id=1,
        status=EntityStatus.ACTIVE,
        last_seen=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
    )
    _seed(
        entities,
        observations,
        track_id=2,
        status=EntityStatus.CLOSED,
        last_seen=datetime(2026, 7, 27, 13, 0, tzinfo=timezone.utc),
    )

    active = client.get("/api/v1/entities/active")
    assert active.status_code == 200
    assert active.json()["total"] == 1
    assert active.json()["items"][0]["status"] == "active"

    recent = client.get("/api/v1/entities/recent")
    assert recent.status_code == 200
    assert recent.json()["total"] == 2
    assert recent.json()["items"][0]["track_id"] == 2


def test_pagination_validation_and_max_limit() -> None:
    client, *_ = _build_client()

    bad_offset = client.get("/api/v1/entities", params={"offset": -1})
    assert bad_offset.status_code == 422

    too_large = client.get("/api/v1/entities", params={"limit": 201})
    assert too_large.status_code == 422
    assert "200" in too_large.json()["detail"]

    bad_range = client.get(
        "/api/v1/entities",
        params={
            "seen_after": "2026-07-27T13:00:00+00:00",
            "seen_before": "2026-07-27T12:00:00+00:00",
        },
    )
    assert bad_range.status_code == 422


def test_timestamp_serialization() -> None:
    client, entities, observations = _build_client()
    entity = _seed(entities, observations)
    response = client.get(f"/api/v1/entities/{entity.id}")
    body = response.json()
    # pydantic serialises aware datetimes with offset
    assert "+00:00" in body["last_seen"] or body["last_seen"].endswith("Z")


def test_database_errors_do_not_leak_internal_details() -> None:
    client, entities, observations = _build_client()
    original = entities.list_entities

    def boom(*args, **kwargs):
        raise RuntimeError(
            "SELECT * FROM secret; password=hunter2 stacktrace"
        )

    entities.list_entities = boom  # type: ignore[method-assign]
    try:
        response = client.get("/api/v1/entities")
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "hunter2" not in detail
        assert "SELECT" not in detail
        assert "stacktrace" not in detail
        assert "unavailable" in detail.lower()
    finally:
        entities.list_entities = original  # type: ignore[method-assign]


def test_filters_on_list_endpoint() -> None:
    client, entities, observations = _build_client()
    _seed(entities, observations, track_id=1, label="person", camera_id="a")
    _seed(entities, observations, track_id=2, label="car", camera_id="b")
    response = client.get(
        "/api/v1/entities",
        params={"entity_type": "car", "camera_id": "b"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["entity_type"] == "car"


if __name__ == "__main__":
    test_health_endpoint()
    test_list_entities_and_retrieve_one()
    test_404_behavior()
    test_observations_endpoint()
    test_active_and_recent_endpoints()
    test_pagination_validation_and_max_limit()
    test_timestamp_serialization()
    test_database_errors_do_not_leak_internal_details()
    test_filters_on_list_endpoint()
    print("Entity query API tests passed.")
