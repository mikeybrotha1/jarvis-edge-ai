"""REST API tests for spatial zones (v0.6.0)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi.testclient import TestClient

from api.app import create_app
from storage.entity_records import EntityCreate
from storage.entity_repository import EntityRepository
from storage.entity_zone_session_repository import EntityZoneSessionRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)
from storage.zone_records import ZoneCreate
from storage.zone_repository import ZoneRepository


def _client():
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    app = create_app(
        session_factory=factory,
        create_schema=False,
        enable_activity_stream=False,
    )
    return TestClient(app), factory


def test_zone_crud_occupancy_and_sessions() -> None:
    client, factory = _client()

    created = client.post(
        "/api/v1/zones",
        json={
            "name": "Entrance",
            "camera_id": "cam1",
            "x_min": 0.1,
            "y_min": 0.1,
            "x_max": 0.5,
            "y_max": 0.5,
            "enabled": True,
        },
    )
    assert created.status_code == 201, created.text
    zone = created.json()
    zone_id = zone["id"]
    assert zone["geometry_type"] == "rectangle"
    assert len(zone["vertices"]) == 4

    # Duplicate name -> 409
    dup = client.post(
        "/api/v1/zones",
        json={
            "name": "Entrance",
            "camera_id": "cam1",
            "x_min": 0.2,
            "y_min": 0.2,
            "x_max": 0.6,
            "y_max": 0.6,
        },
    )
    assert dup.status_code == 409

    # Invalid coords -> 422
    bad = client.post(
        "/api/v1/zones",
        json={
            "name": "Bad",
            "camera_id": "cam1",
            "x_min": 0.8,
            "y_min": 0.1,
            "x_max": 0.2,
            "y_max": 0.5,
        },
    )
    assert bad.status_code == 422

    listed = client.get("/api/v1/zones")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    got = client.get(f"/api/v1/zones/{zone_id}")
    assert got.status_code == 200
    assert got.json()["name"] == "Entrance"

    patched = client.patch(
        f"/api/v1/zones/{zone_id}",
        json={"enabled": False, "name": "Entrance-disabled"},
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False

    # Re-enable for occupancy test
    client.patch(f"/api/v1/zones/{zone_id}", json={"enabled": True})

    entities = EntityRepository(factory)
    entity = entities.create(
        EntityCreate(
            identity_key="cam1:1",
            identity_strategy="tracker_id",
            label="person",
            track_id=1,
            camera_id="cam1",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc),
            confidence=0.9,
        )
    )
    sessions = EntityZoneSessionRepository(factory)
    sessions.open_session(
        zone_id=UUID(zone_id),
        entity_id=entity.id,
        camera_id="cam1",
        entered_at=datetime.now(timezone.utc),
        occupancy_after_enter=1,
    )

    occ = client.get(f"/api/v1/zones/{zone_id}/occupancy")
    assert occ.status_code == 200
    body = occ.json()
    assert body["occupancy"] == 1
    assert len(body["entities"]) == 1

    ents = client.get(f"/api/v1/zones/{zone_id}/entities")
    assert ents.status_code == 200
    assert ents.json()["total"] == 1

    sess = client.get(f"/api/v1/zones/{zone_id}/sessions")
    assert sess.status_code == 200
    assert sess.json()["total"] == 1
    assert "dwell_seconds" in sess.json()["items"][0]

    hist = client.get(f"/api/v1/entities/{entity.id}/zones")
    assert hist.status_code == 200
    assert hist.json()["total"] == 1

    missing = client.get(
        "/api/v1/zones/00000000-0000-0000-0000-000000000099"
    )
    assert missing.status_code == 404


def test_zone_not_found_and_list_filter() -> None:
    client, factory = _client()
    zones = ZoneRepository(factory)
    zones.create(
        ZoneCreate(
            name="A",
            camera_id="camA",
            vertices=[
                {"x": 0.0, "y": 0.0},
                {"x": 0.5, "y": 0.0},
                {"x": 0.5, "y": 0.5},
                {"x": 0.0, "y": 0.5},
            ],
        )
    )
    zones.create(
        ZoneCreate(
            name="B",
            camera_id="camB",
            vertices=[
                {"x": 0.0, "y": 0.0},
                {"x": 0.5, "y": 0.0},
                {"x": 0.5, "y": 0.5},
                {"x": 0.0, "y": 0.5},
            ],
        )
    )
    resp = client.get("/api/v1/zones", params={"camera_id": "camA"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["name"] == "A"
