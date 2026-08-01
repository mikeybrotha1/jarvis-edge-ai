"""WebSocket activity stream protocol tests (v0.5.0)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app
from config.models import ActivityStreamConfig
from services.activity_stream import ActivityStreamBroker
from services.entity_query_service import EntityQueryService, QueryLimits
from services.timeline_service import TimelineLimits, TimelineService
from storage.entity_repository import EntityRepository
from storage.observation_repository import ObservationRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)
from storage.timeline_models import TimelineEvent, TimelineEventType
from storage.timeline_repository import TimelineRepository


def _build_app(*, max_connections: int = 5, queue_size: int = 50):
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
    broker = ActivityStreamBroker(
        client_queue_size=queue_size,
        max_connections=max_connections,
    )
    config = ActivityStreamConfig(
        enabled=True,
        client_queue_size=queue_size,
        max_connections=max_connections,
        heartbeat_interval_seconds=30.0,
    )
    app = create_app(
        query_service=query,
        timeline_service=timeline,
        activity_stream_config=config,
        activity_broker=broker,
        activity_listener=None,
        enable_activity_stream=True,
    )
    return app, broker, timeline


def _inject_event(broker: ActivityStreamBroker, event: TimelineEvent) -> None:
    """Inject a timeline.event into the sole registered client queue.

    TestClient runs the ASGI app on a worker thread; put_nowait is used so we
    do not need the portal token. Broker filtering is covered by broker tests.
    """

    from services.activity_stream import _event_to_dict

    clients = list(broker._clients.values())
    assert clients, "expected a registered websocket client"
    clients[0].queue.put_nowait(
        {
            "type": "timeline.event",
            "event": _event_to_dict(event),
        }
    )


def test_connection_ready_and_default_subscription() -> None:
    app, broker, _ = _build_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/v1/activity") as ws:
            ready = ws.receive_json()
            assert ready["type"] == "connection.ready"
            assert ready["protocol_version"] == "1"
            assert ready["stream_version"] == "0.6.0"
            assert ready["subscription"]["event_types"] == [
                "entity_closed",
                "entity_created",
                "zone_entered",
                "zone_exited",
                "zone_occupancy_changed",
            ]


def test_timeline_event_schema_and_subscription_update() -> None:
    app, broker, _ = _build_app()
    entity_id = uuid4()
    event = TimelineEvent(
        id=f"entity-created:{entity_id}",
        event_type=TimelineEventType.ENTITY_CREATED,
        occurred_at=datetime(2026, 7, 28, 17, 0, tzinfo=timezone.utc),
        source="entity",
        entity_id=entity_id,
        camera_id="front-door",
        entity_type="person",
        summary="Person appeared on front-door",
        payload={"status": "active"},
    )

    with TestClient(app) as client:
        with client.websocket_connect("/ws/v1/activity") as ws:
            assert ws.receive_json()["type"] == "connection.ready"

            # Client is registered after connection.ready.
            _inject_event(broker, event)

            msg = ws.receive_json()
            assert msg["type"] == "timeline.event"
            body = msg["event"]
            assert body["id"] == event.id
            assert body["event_type"] == "entity_created"
            assert body["entity_id"] == str(entity_id)
            assert body["summary"] == "Person appeared on front-door"
            assert "occurred_at" in body
            assert set(body.keys()) >= {
                "id",
                "event_type",
                "occurred_at",
                "source",
                "entity_id",
                "camera_id",
                "entity_type",
                "summary",
                "payload",
            }

            ws.send_json(
                {
                    "type": "subscription.update",
                    "filters": {
                        "event_types": [
                            "entity_created",
                            "observation_recorded",
                        ],
                        "camera_ids": ["front-door"],
                    },
                }
            )
            updated = ws.receive_json()
            assert updated["type"] == "subscription.updated"
            assert "observation_recorded" in updated["subscription"][
                "event_types"
            ]


def test_malformed_and_unsupported_messages() -> None:
    app, broker, _ = _build_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/v1/activity") as ws:
            ws.receive_json()
            ws.send_text("not-json")
            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["code"] == "invalid_json"

            ws.send_json({"type": "nope"})
            err2 = ws.receive_json()
            assert err2["type"] == "error"

            ws.send_json(
                {
                    "type": "subscription.update",
                    "filters": {"event_types": ["not_a_type"]},
                }
            )
            err3 = ws.receive_json()
            assert err3["type"] == "error"
            assert err3["code"] == "invalid_subscription"


def test_max_connections() -> None:
    app, broker, _ = _build_app(max_connections=1)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/v1/activity") as ws1:
            ws1.receive_json()
            rejected = False
            try:
                with client.websocket_connect("/ws/v1/activity") as ws2:
                    # If accept sneaks through, the server should close soon.
                    try:
                        ws2.receive_json()
                    except Exception:
                        rejected = True
            except Exception:
                rejected = True
            assert rejected is True


def test_api_starts_without_camera_imports() -> None:
    app, _, _ = _build_app()
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_heartbeat_message_type() -> None:
    app, broker, _ = _build_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/v1/activity") as ws:
            ws.receive_json()

            clients = list(broker._clients.values())
            assert clients
            clients[0].queue.put_nowait(
                {
                    "type": "heartbeat",
                    "sent_at": "2026-07-28T17:00:00Z",
                }
            )
            msg = ws.receive_json()
            assert msg["type"] == "heartbeat"
            ws.send_json({"type": "heartbeat.ack"})


def test_clean_server_shutdown() -> None:
    app, broker, _ = _build_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/v1/activity") as ws:
            ws.receive_json()
    # Exiting TestClient context runs lifespan shutdown without errors.
    assert broker.connection_count == 0


if __name__ == "__main__":
    test_connection_ready_and_default_subscription()
    test_timeline_event_schema_and_subscription_update()
    test_malformed_and_unsupported_messages()
    test_max_connections()
    test_api_starts_without_camera_imports()
    test_heartbeat_message_type()
    test_clean_server_shutdown()
    print("Activity websocket tests passed.")
