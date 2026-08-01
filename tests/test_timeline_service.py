"""Service and repository tests for activity timeline (v0.4.2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from services.entity_query_service import EntityNotFoundError
from services.timeline_service import (
    TimelineLimits,
    TimelineNotFoundError,
    TimelineService,
    TimelineValidationError,
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
from storage.timeline_cursor import decode_cursor, encode_cursor
from storage.timeline_models import TimelineEventType
from storage.timeline_repository import TimelineRepository


def _stack():
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    entities = EntityRepository(factory)
    observations = ObservationRepository(factory)
    timeline_repo = TimelineRepository(factory)
    service = TimelineService(
        timeline_repo,
        entities,
        limits=TimelineLimits(default_limit=50, maximum_limit=200),
    )
    return service, entities, observations, timeline_repo


def _ts(minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 7, 28, 12, minute, second, tzinfo=timezone.utc)


def _seed(
    entities: EntityRepository,
    observations: ObservationRepository,
    *,
    track_id: int = 1,
    label: str = "person",
    camera_id: str = "front-door",
    first_seen: datetime | None = None,
    last_seen: datetime | None = None,
    closed: bool = False,
    obs_times: list[datetime] | None = None,
):
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
            confidence=0.9,
        )
    )
    for index, when in enumerate(obs_times or []):
        observations.append(
            ObservationCreate(
                entity_id=entity.id,
                observed_at=when,
                camera_id=camera_id,
                confidence=0.8,
                label=label,
                source_event_type="vision.object_updated",
                source_event_id=f"{entity.id}-{index}",
                frame_number=index + 1,
                track_id=track_id,
            )
        )
    if closed:
        entity = entities.close(entity.id, last_seen=last)
    return entity


def test_default_timeline_excludes_observations() -> None:
    service, entities, observations, _ = _stack()
    entity = _seed(
        entities,
        observations,
        closed=True,
        last_seen=_ts(minute=5),
        obs_times=[_ts(minute=1), _ts(minute=2)],
    )
    page = service.list_timeline()
    types = {item.event_type for item in page.items}
    assert TimelineEventType.ENTITY_CREATED in types
    assert TimelineEventType.ENTITY_CLOSED in types
    assert TimelineEventType.OBSERVATION_RECORDED not in types
    assert all(item.entity_id == entity.id for item in page.items)


def test_explicit_observation_filter_includes_observations() -> None:
    service, entities, observations, _ = _stack()
    _seed(
        entities,
        observations,
        closed=True,
        last_seen=_ts(minute=5),
        obs_times=[_ts(minute=1)],
    )
    page = service.list_timeline(
        event_type=[
            "entity_created",
            "entity_closed",
            "observation_recorded",
        ]
    )
    types = {item.event_type for item in page.items}
    assert TimelineEventType.OBSERVATION_RECORDED in types
    assert len(page.items) == 3


def test_entity_created_and_closed_projection() -> None:
    service, entities, observations, _ = _stack()
    entity = _seed(
        entities,
        observations,
        closed=True,
        first_seen=_ts(minute=0),
        last_seen=_ts(minute=10),
    )
    page = service.list_timeline(sort="asc")
    assert page.items[0].id == f"entity-created:{entity.id}"
    assert page.items[0].event_type is TimelineEventType.ENTITY_CREATED
    assert page.items[0].summary.startswith("Person appeared on")
    assert page.items[1].id == f"entity-closed:{entity.id}"
    assert page.items[1].event_type is TimelineEventType.ENTITY_CLOSED
    assert "left" in page.items[1].summary


def test_observation_projection_and_stable_ids() -> None:
    service, entities, observations, _ = _stack()
    entity = _seed(
        entities,
        observations,
        obs_times=[_ts(minute=1)],
    )
    obs = observations.list_for_entity(entity.id)[0]
    page = service.list_timeline(event_type=["observation_recorded"])
    assert len(page.items) == 1
    event = page.items[0]
    assert event.id == f"observation:{obs.id}"
    assert event.payload["frame_number"] == 1


def test_deterministic_ordering_when_timestamps_tie() -> None:
    service, entities, observations, _ = _stack()
    # Two entities with identical first_seen; order by event_id.
    _seed(
        entities,
        observations,
        track_id=1,
        first_seen=_ts(),
        last_seen=_ts(),
    )
    _seed(
        entities,
        observations,
        track_id=2,
        first_seen=_ts(),
        last_seen=_ts(),
    )
    asc = service.list_timeline(sort="asc")
    desc = service.list_timeline(sort="desc")
    asc_ids = [item.id for item in asc.items]
    desc_ids = [item.id for item in desc.items]
    assert asc_ids == sorted(asc_ids)
    assert desc_ids == sorted(desc_ids, reverse=True)


def test_cursor_pagination_no_duplicates_or_gaps() -> None:
    service, entities, observations, _ = _stack()
    for track_id in range(1, 6):
        _seed(
            entities,
            observations,
            track_id=track_id,
            first_seen=_ts(minute=track_id),
            last_seen=_ts(minute=track_id),
            closed=True,
        )
    # 5 created + 5 closed = 10 lifecycle events
    first = service.list_timeline(limit=4, sort="asc")
    assert len(first.items) == 4
    assert first.next_cursor is not None
    cursor = decode_cursor(first.next_cursor)
    assert cursor.event_id == first.items[-1].id

    second = service.list_timeline(
        limit=4,
        sort="asc",
        cursor=first.next_cursor,
    )
    third = service.list_timeline(
        limit=4,
        sort="asc",
        cursor=second.next_cursor,
    )
    all_ids = (
        [item.id for item in first.items]
        + [item.id for item in second.items]
        + [item.id for item in third.items]
    )
    assert len(all_ids) == 10
    assert len(set(all_ids)) == 10
    assert third.next_cursor is None


def test_malformed_cursor_rejected() -> None:
    service, *_ = _stack()
    try:
        service.list_timeline(cursor="!!!not-a-cursor!!!")
        raise AssertionError("expected TimelineValidationError")
    except TimelineValidationError as error:
        assert "cursor" in str(error).lower()


def test_camera_entity_and_type_filters() -> None:
    service, entities, observations, _ = _stack()
    _seed(entities, observations, track_id=1, camera_id="cam_a", label="person")
    _seed(entities, observations, track_id=2, camera_id="cam_b", label="car")
    page = service.list_timeline(camera_id="cam_b", entity_type="car")
    assert page.limit == 50
    assert all(item.camera_id == "cam_b" for item in page.items)
    assert all(item.entity_type == "car" for item in page.items)


def test_multiple_event_type_filters() -> None:
    service, entities, observations, _ = _stack()
    _seed(
        entities,
        observations,
        closed=True,
        last_seen=_ts(minute=3),
        obs_times=[_ts(minute=1)],
    )
    page = service.list_timeline(
        event_type=["entity_created", "observation_recorded"]
    )
    types = {item.event_type for item in page.items}
    assert TimelineEventType.ENTITY_CREATED in types
    assert TimelineEventType.OBSERVATION_RECORDED in types
    assert TimelineEventType.ENTITY_CLOSED not in types


def test_time_range_and_inverted_range() -> None:
    service, entities, observations, _ = _stack()
    _seed(entities, observations, track_id=1, first_seen=_ts(minute=0))
    _seed(entities, observations, track_id=2, first_seen=_ts(minute=10))
    page = service.list_timeline(
        occurred_after=_ts(minute=5),
        occurred_before=_ts(minute=15),
    )
    assert all(
        _ts(minute=5) <= item.occurred_at <= _ts(minute=15)
        for item in page.items
    )
    try:
        service.list_timeline(
            occurred_after=_ts(minute=10),
            occurred_before=_ts(minute=1),
        )
        raise AssertionError("expected TimelineValidationError")
    except TimelineValidationError:
        pass


def test_unknown_entity_and_event() -> None:
    service, *_ = _stack()
    try:
        service.list_entity_timeline(uuid4())
        raise AssertionError("expected EntityNotFoundError")
    except EntityNotFoundError:
        pass
    try:
        service.get_event(f"entity-created:{uuid4()}")
        raise AssertionError("expected TimelineNotFoundError")
    except TimelineNotFoundError:
        pass


def test_late_observation_ordered_by_occurred_at() -> None:
    service, entities, observations, _ = _stack()
    entity = _seed(
        entities,
        observations,
        first_seen=_ts(minute=0),
        last_seen=_ts(minute=1),
        closed=True,
        obs_times=[],
    )
    # Late observation after close time.
    late = _ts(minute=30)
    observations.append(
        ObservationCreate(
            entity_id=entity.id,
            observed_at=late,
            camera_id="front-door",
            confidence=0.5,
            label="person",
            source_event_type="vision.object_updated",
            source_event_id="late-1",
            frame_number=99,
            track_id=1,
        )
    )
    page = service.list_timeline(
        event_type=[
            "entity_created",
            "entity_closed",
            "observation_recorded",
        ],
        sort="asc",
    )
    assert page.items[-1].event_type is TimelineEventType.OBSERVATION_RECORDED
    assert page.items[-1].occurred_at == late


def test_encode_decode_cursor_roundtrip() -> None:
    token = encode_cursor(_ts(minute=3), "entity-created:abc")
    decoded = decode_cursor(token)
    assert decoded.event_id == "entity-created:abc"
    assert decoded.occurred_at == _ts(minute=3)


def test_safe_database_failure() -> None:
    service, entities, observations, timeline_repo = _stack()
    original = timeline_repo.list_events

    def boom(*args, **kwargs):
        raise RuntimeError("SELECT password=secret")

    timeline_repo.list_events = boom  # type: ignore[method-assign]
    try:
        service.list_timeline()
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass
    finally:
        timeline_repo.list_events = original  # type: ignore[method-assign]


if __name__ == "__main__":
    test_default_timeline_excludes_observations()
    test_explicit_observation_filter_includes_observations()
    test_entity_created_and_closed_projection()
    test_observation_projection_and_stable_ids()
    test_deterministic_ordering_when_timestamps_tie()
    test_cursor_pagination_no_duplicates_or_gaps()
    test_malformed_cursor_rejected()
    test_camera_entity_and_type_filters()
    test_multiple_event_type_filters()
    test_time_range_and_inverted_range()
    test_unknown_entity_and_event()
    test_late_observation_ordered_by_occurred_at()
    test_encode_decode_cursor_roundtrip()
    test_safe_database_failure()
    print("Timeline service tests passed.")
