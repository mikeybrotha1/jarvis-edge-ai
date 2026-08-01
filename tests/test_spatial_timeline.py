"""Timeline projection tests for spatial events (v0.6.0)."""

from __future__ import annotations

from datetime import datetime, timezone

from services.timeline_service import TimelineService
from storage.entity_records import EntityCreate
from storage.entity_repository import EntityRepository
from storage.entity_zone_session_repository import EntityZoneSessionRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)
from storage.timeline_models import TimelineEventType
from storage.timeline_repository import TimelineRepository
from storage.zone_records import ZoneCreate
from storage.zone_repository import ZoneRepository


def _setup():
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    entities = EntityRepository(factory)
    zones = ZoneRepository(factory)
    sessions = EntityZoneSessionRepository(factory)
    timeline = TimelineService(
        TimelineRepository(factory),
        entities,
    )
    return entities, zones, sessions, timeline


def test_spatial_events_in_default_timeline_and_stable_ids() -> None:
    entities, zones, sessions, timeline = _setup()
    now = datetime.now(timezone.utc)
    entity = entities.create(
        EntityCreate(
            identity_key="cam1:1",
            identity_strategy="tracker_id",
            label="person",
            track_id=1,
            camera_id="cam1",
            first_seen=now,
            last_seen=now,
            confidence=0.9,
        )
    )
    zone = zones.create(
        ZoneCreate(
            name="Lobby",
            camera_id="cam1",
            vertices=[
                {"x": 0.1, "y": 0.1},
                {"x": 0.9, "y": 0.1},
                {"x": 0.9, "y": 0.9},
                {"x": 0.1, "y": 0.9},
            ],
        )
    )
    opened = sessions.open_session(
        zone_id=zone.id,
        entity_id=entity.id,
        camera_id="cam1",
        entered_at=now,
        occupancy_after_enter=1,
    )
    closed = sessions.close_session(
        opened.id,
        exited_at=now,
        occupancy_after_exit=0,
    )

    page = timeline.list_timeline(limit=50)
    types = {item.event_type for item in page.items}
    assert TimelineEventType.ENTITY_CREATED in types
    assert TimelineEventType.ZONE_ENTERED in types
    assert TimelineEventType.ZONE_EXITED in types
    assert TimelineEventType.ZONE_OCCUPANCY_CHANGED in types
    # observation excluded by default
    assert TimelineEventType.OBSERVATION_RECORDED not in types

    entered = timeline.get_event(f"zone-entered:{opened.id}")
    assert entered.event_type is TimelineEventType.ZONE_ENTERED
    assert entered.payload["zone_name"] == "Lobby"
    assert entered.source == "spatial"

    exited = timeline.get_event(f"zone-exited:{closed.id}")
    assert exited.event_type is TimelineEventType.ZONE_EXITED

    occ_in = timeline.get_event(f"zone-occupancy:{opened.id}:entered")
    assert occ_in.event_type is TimelineEventType.ZONE_OCCUPANCY_CHANGED
    assert occ_in.payload["occupancy"] == 1

    filtered = timeline.list_timeline(zone_id=zone.id, limit=50)
    assert all(
        item.payload.get("zone_id") == str(zone.id)
        or item.event_type
        in {
            TimelineEventType.ZONE_ENTERED,
            TimelineEventType.ZONE_EXITED,
            TimelineEventType.ZONE_OCCUPANCY_CHANGED,
        }
        for item in filtered.items
    )
    assert all(
        item.event_type
        in {
            TimelineEventType.ZONE_ENTERED,
            TimelineEventType.ZONE_EXITED,
            TimelineEventType.ZONE_OCCUPANCY_CHANGED,
        }
        for item in filtered.items
    )
