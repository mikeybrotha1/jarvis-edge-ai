"""Repository-level tests for entity memory persistence."""

from __future__ import annotations

from datetime import datetime, timezone

from storage.entity_orm import EntityStatus
from storage.entity_records import (
    EntityCreate,
    EntityUpdate,
    ObservationCreate,
)
from storage.entity_repository import EntityRepository
from storage.observation_repository import ObservationRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
    session_scope,
)


def _repos():
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    return EntityRepository(factory), ObservationRepository(factory), factory


def test_create_and_apply_observation_running_average() -> None:
    entities, observations, _ = _repos()
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)

    entity = entities.create(
        EntityCreate(
            identity_key="camera:cam-a:tracker:9",
            identity_strategy="tracker_id",
            label="person",
            track_id=9,
            camera_id="cam-a",
            first_seen=now,
            last_seen=now,
            confidence=0.5,
            bounding_box={"x1": 0, "y1": 0, "x2": 1, "y2": 1},
        )
    )
    assert entity.times_seen == 1

    later = datetime(2026, 7, 27, 12, 0, 1, tzinfo=timezone.utc)
    entity = entities.apply_observation(
        entity.id,
        EntityUpdate(
            last_seen=later,
            confidence=1.0,
            label="person",
            track_id=9,
            camera_id="cam-a",
        ),
    )
    assert entity.times_seen == 2
    assert entity.average_confidence == 0.75

    observations.append(
        ObservationCreate(
            entity_id=entity.id,
            observed_at=later,
            camera_id="cam-a",
            confidence=1.0,
            label="person",
            source_event_type="vision.object_updated",
            source_event_id="evt-1",
            frame_number=2,
            track_id=9,
        )
    )
    assert observations.count_for_entity(entity.id) == 1

    _, created = observations.append(
        ObservationCreate(
            entity_id=entity.id,
            observed_at=later,
            camera_id="cam-a",
            confidence=1.0,
            label="person",
            source_event_type="vision.object_updated",
            source_event_id="evt-1",
            frame_number=2,
            track_id=9,
        )
    )
    assert created is False
    assert observations.count_for_entity(entity.id) == 1


def test_shared_session_rolls_back_on_failure() -> None:
    entities, observations, factory = _repos()
    now = datetime(2026, 7, 27, 15, 0, tzinfo=timezone.utc)

    try:
        with session_scope(factory) as session:
            entity = entities.create(
                EntityCreate(
                    identity_key="camera:cam:tracker:7",
                    identity_strategy="tracker_id",
                    label="bag",
                    track_id=7,
                    camera_id="cam",
                    first_seen=now,
                    last_seen=now,
                    confidence=0.4,
                ),
                session=session,
            )
            observations.append(
                ObservationCreate(
                    entity_id=entity.id,
                    observed_at=now,
                    camera_id="cam",
                    confidence=0.4,
                    label="bag",
                    source_event_type="vision.object_entered",
                    source_event_id="evt-roll",
                ),
                session=session,
            )
            raise RuntimeError("force rollback")
    except RuntimeError:
        pass

    assert entities.get_latest_by_identity_key(
        "camera:cam:tracker:7"
    ) is None
    assert observations.has_source_event("evt-roll") is False


def test_close_entity_status() -> None:
    entities, _, _ = _repos()
    now = datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc)
    entity = entities.create(
        EntityCreate(
            identity_key="camera:cam:tracker:3",
            identity_strategy="tracker_id",
            label="person",
            track_id=3,
            camera_id="cam",
            first_seen=now,
            last_seen=now,
            confidence=0.9,
        )
    )
    closed = entities.close(entity.id, last_seen=now)
    assert closed.status is EntityStatus.CLOSED
    assert entities.get_active_by_identity_key(
        "camera:cam:tracker:3"
    ) is None


if __name__ == "__main__":
    test_create_and_apply_observation_running_average()
    test_shared_session_rolls_back_on_failure()
    test_close_entity_status()
    print("Entity repository tests passed.")
