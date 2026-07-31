"""Tests for EntityQueryService (v0.4.1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from services.entity_query_service import (
    EntityNotFoundError,
    EntityQueryService,
    QueryLimits,
    QueryValidationError,
)
from storage.entity_orm import EntityStatus
from storage.entity_records import EntityCreate, ObservationCreate
from storage.entity_repository import EntityRepository
from storage.observation_repository import ObservationRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)


def _stack():
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
    return service, entities, observations


def _ts(minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 7, 27, 12, minute, second, tzinfo=timezone.utc)


def _seed_entity(
    entities: EntityRepository,
    observations: ObservationRepository,
    *,
    label: str = "person",
    camera_id: str = "cam_a",
    track_id: int = 1,
    confidence: float = 0.9,
    first_seen: datetime | None = None,
    last_seen: datetime | None = None,
    status: EntityStatus = EntityStatus.ACTIVE,
    obs_count: int = 1,
) -> object:
    first = first_seen or _ts()
    last = last_seen or first
    entity = entities.create(
        EntityCreate(
            identity_key=f"camera:{camera_id}:tracker:{track_id}",
            identity_strategy="tracker_id",
            label=label,
            track_id=track_id,
            camera_id=camera_id,
            first_seen=first,
            last_seen=last,
            confidence=confidence,
            bounding_box={"x1": 1, "y1": 2, "x2": 3, "y2": 4},
        )
    )
    for index in range(obs_count):
        observations.append(
            ObservationCreate(
                entity_id=entity.id,
                observed_at=first + timedelta(seconds=index),
                camera_id=camera_id,
                confidence=confidence,
                label=label,
                source_event_type="vision.object_updated",
                source_event_id=f"{entity.id}-{index}",
                frame_number=index + 1,
                track_id=track_id,
                bounding_box={"x1": 1, "y1": 2, "x2": 3, "y2": 4},
            )
        )
    if status is EntityStatus.CLOSED:
        entity = entities.close(entity.id, last_seen=last)
    return entity


def test_retrieve_existing_entity() -> None:
    service, entities, observations = _stack()
    created = _seed_entity(entities, observations)
    loaded = service.get_entity(created.id)
    assert loaded.id == created.id
    assert loaded.label == "person"


def test_missing_entity_raises() -> None:
    service, *_ = _stack()
    try:
        service.get_entity(uuid4())
        raise AssertionError("expected EntityNotFoundError")
    except EntityNotFoundError:
        pass


def test_status_filtering() -> None:
    service, entities, observations = _stack()
    _seed_entity(entities, observations, track_id=1, status=EntityStatus.ACTIVE)
    _seed_entity(
        entities,
        observations,
        track_id=2,
        status=EntityStatus.CLOSED,
        last_seen=_ts(minute=1),
    )
    active = service.list_entities(status="active")
    closed = service.list_entities(status="closed")
    assert active.total == 1
    assert closed.total == 1
    assert active.items[0].status is EntityStatus.ACTIVE
    assert closed.items[0].status is EntityStatus.CLOSED


def test_camera_filtering() -> None:
    service, entities, observations = _stack()
    _seed_entity(entities, observations, camera_id="cam_a", track_id=1)
    _seed_entity(entities, observations, camera_id="cam_b", track_id=2)
    page = service.list_entities(camera_id="cam_b")
    assert page.total == 1
    assert page.items[0].camera_id == "cam_b"


def test_entity_type_filtering() -> None:
    service, entities, observations = _stack()
    _seed_entity(entities, observations, label="person", track_id=1)
    _seed_entity(entities, observations, label="car", track_id=2)
    page = service.list_entities(entity_type="car")
    assert page.total == 1
    assert page.items[0].label == "car"


def test_time_range_filtering() -> None:
    service, entities, observations = _stack()
    _seed_entity(
        entities,
        observations,
        track_id=1,
        last_seen=_ts(minute=0),
    )
    _seed_entity(
        entities,
        observations,
        track_id=2,
        last_seen=_ts(minute=10),
    )
    page = service.list_entities(
        seen_after=_ts(minute=5),
        seen_before=_ts(minute=15),
    )
    assert page.total == 1
    assert page.items[0].track_id == 2


def test_pagination_and_total_count() -> None:
    service, entities, observations = _stack()
    for track_id in range(1, 6):
        _seed_entity(
            entities,
            observations,
            track_id=track_id,
            last_seen=_ts(minute=track_id),
        )
    page = service.list_entities(limit=2, offset=1, sort="asc")
    assert page.total == 5
    assert page.limit == 2
    assert page.offset == 1
    assert len(page.items) == 2
    assert page.items[0].track_id == 2
    assert page.items[1].track_id == 3


def test_sort_direction() -> None:
    service, entities, observations = _stack()
    _seed_entity(entities, observations, track_id=1, last_seen=_ts(minute=1))
    _seed_entity(entities, observations, track_id=2, last_seen=_ts(minute=2))
    desc = service.list_entities(sort="desc")
    asc = service.list_entities(sort="asc")
    assert desc.items[0].track_id == 2
    assert asc.items[0].track_id == 1


def test_active_and_recent_helpers() -> None:
    service, entities, observations = _stack()
    _seed_entity(
        entities,
        observations,
        track_id=1,
        status=EntityStatus.ACTIVE,
        last_seen=_ts(minute=1),
    )
    _seed_entity(
        entities,
        observations,
        track_id=2,
        status=EntityStatus.CLOSED,
        last_seen=_ts(minute=5),
    )
    active = service.list_active_entities()
    recent = service.list_recent_entities(limit=10)
    assert active.total == 1
    assert active.items[0].status is EntityStatus.ACTIVE
    assert recent.total == 2
    assert recent.items[0].track_id == 2


def test_observation_retrieval_and_entity_scope() -> None:
    service, entities, observations = _stack()
    first = _seed_entity(entities, observations, track_id=1, obs_count=3)
    second = _seed_entity(entities, observations, track_id=2, obs_count=2)
    page = service.list_observations(first.id, sort="asc", limit=10)
    assert page.total == 3
    assert all(item.entity_id == first.id for item in page.items)
    assert page.items[0].frame_number == 1
    other = service.list_observations(second.id)
    assert other.total == 2


def test_invalid_date_range() -> None:
    service, *_ = _stack()
    try:
        service.list_entities(
            seen_after=_ts(minute=10),
            seen_before=_ts(minute=1),
        )
        raise AssertionError("expected QueryValidationError")
    except QueryValidationError as error:
        assert "seen_after" in str(error)


def test_invalid_limit_and_sort() -> None:
    service, *_ = _stack()
    try:
        service.list_entities(limit=999)
        raise AssertionError("expected QueryValidationError")
    except QueryValidationError:
        pass
    try:
        service.list_entities(sort="sideways")
        raise AssertionError("expected QueryValidationError")
    except QueryValidationError:
        pass
    try:
        service.list_entities(offset=-1)
        raise AssertionError("expected QueryValidationError")
    except QueryValidationError:
        pass


def test_safe_database_failure_behavior() -> None:
    service, entities, observations = _stack()
    original = entities.get_by_id

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    entities.get_by_id = boom  # type: ignore[method-assign]
    try:
        service.get_entity(uuid4())
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass
    finally:
        entities.get_by_id = original  # type: ignore[method-assign]


if __name__ == "__main__":
    test_retrieve_existing_entity()
    test_missing_entity_raises()
    test_status_filtering()
    test_camera_filtering()
    test_entity_type_filtering()
    test_time_range_filtering()
    test_pagination_and_total_count()
    test_sort_direction()
    test_active_and_recent_helpers()
    test_observation_retrieval_and_entity_scope()
    test_invalid_date_range()
    test_invalid_limit_and_sort()
    test_safe_database_failure_behavior()
    print("Entity query service tests passed.")
