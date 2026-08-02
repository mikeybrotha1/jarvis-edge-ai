"""Provider isolation and composer tests for timeline architecture (v0.7.0)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from storage.entity_records import EntityCreate, ObservationCreate
from storage.entity_repository import EntityRepository
from storage.entity_zone_session_repository import EntityZoneSessionRepository
from storage.observation_repository import ObservationRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)
from storage.timeline_cursor import encode_cursor
from storage.timeline_models import (
    DEFAULT_TIMELINE_EVENT_TYPES,
    TimelineEventType,
    TimelineListFilter,
)
from storage.timeline_repository import TimelineRepository
from storage.zone_records import ZoneCreate
from storage.zone_repository import ZoneRepository
from timeline.composer import (
    TimelineComposer,
    TimelineProviderRegistrationError,
    merge_ordered_streams,
)
from timeline.contracts import TIMELINE_UNION_COLUMN_NAMES
from timeline.factory import build_default_timeline_composer
from timeline.provider import TimelineQueryContext
from timeline.providers.entity_lifecycle import EntityLifecycleTimelineProvider
from timeline.providers.spatial import SpatialTimelineProvider
from storage.timeline_models import TimelineEvent


def _factory():
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    return create_session_factory(engine)


def _seed_lifecycle(factory):
    entities = EntityRepository(factory)
    observations = ObservationRepository(factory)
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    e1 = entities.create(
        EntityCreate(
            identity_key="cam:1",
            identity_strategy="tracker_id",
            label="person",
            track_id=1,
            camera_id="cam",
            first_seen=t0,
            last_seen=t0 + timedelta(seconds=10),
            confidence=0.9,
        )
    )
    entities.close(e1.id, last_seen=t0 + timedelta(seconds=10))
    observations.append(
        ObservationCreate(
            entity_id=e1.id,
            observed_at=t0 + timedelta(seconds=5),
            camera_id="cam",
            confidence=0.8,
            label="person",
            source_event_type="object_updated",
            track_id=1,
        )
    )
    return entities, e1, t0


def _seed_spatial(factory, entity_id):
    zones = ZoneRepository(factory)
    sessions = EntityZoneSessionRepository(factory)
    t0 = datetime(2026, 8, 1, 12, 0, 5, tzinfo=timezone.utc)
    zone = zones.create(
        ZoneCreate(
            name="Lobby",
            camera_id="cam",
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
        entity_id=entity_id,
        camera_id="cam",
        entered_at=t0,
        occupancy_after_enter=1,
    )
    sessions.close_session(
        opened.id,
        exited_at=t0 + timedelta(seconds=3),
        occupancy_after_exit=0,
    )
    return zone, opened, t0


# --- Entity lifecycle provider ---


def test_lifecycle_provider_owned_types_and_prefixes() -> None:
    factory = _factory()
    provider = EntityLifecycleTimelineProvider(factory)
    assert TimelineEventType.ENTITY_CREATED in provider.owned_event_types
    assert TimelineEventType.ZONE_ENTERED not in provider.owned_event_types
    assert provider.supports_event_id(f"entity-created:{uuid4()}")
    assert not provider.supports_event_id(f"zone-entered:{uuid4()}")


def test_lifecycle_provider_lists_and_get_by_id() -> None:
    factory = _factory()
    _, entity, t0 = _seed_lifecycle(factory)
    provider = EntityLifecycleTimelineProvider(factory)
    ctx = TimelineQueryContext(
        event_types=(
            TimelineEventType.ENTITY_CREATED,
            TimelineEventType.ENTITY_CLOSED,
            TimelineEventType.OBSERVATION_RECORDED,
        ),
        sort="asc",
        limit=50,
    )
    events = provider.list_events(ctx)
    types = {e.event_type for e in events}
    assert TimelineEventType.ENTITY_CREATED in types
    assert TimelineEventType.ENTITY_CLOSED in types
    assert TimelineEventType.OBSERVATION_RECORDED in types
    assert len(events) <= ctx.limit

    created = provider.get_event_by_id(f"entity-created:{entity.id}")
    assert created is not None
    assert created.event_type is TimelineEventType.ENTITY_CREATED

    closed = provider.get_event_by_id(f"entity-closed:{entity.id}")
    assert closed is not None

    assert provider.get_event_by_id("entity-created:not-a-uuid") is None
    assert provider.get_event_by_id(f"zone-entered:{uuid4()}") is None


def test_lifecycle_skips_when_zone_filter() -> None:
    factory = _factory()
    _seed_lifecycle(factory)
    provider = EntityLifecycleTimelineProvider(factory)
    ctx = TimelineQueryContext(
        event_types=tuple(DEFAULT_TIMELINE_EVENT_TYPES),
        zone_id=uuid4(),
        limit=10,
    )
    assert not provider.can_contribute(ctx)
    assert provider.list_events(ctx) == []


def test_lifecycle_provider_typed_nulls_compile_postgresql() -> None:
    factory = _factory()
    provider = EntityLifecycleTimelineProvider(factory)
    ctx = TimelineQueryContext(
        event_types=(TimelineEventType.ENTITY_CREATED,),
        limit=5,
    )
    sql = str(
        provider.build_list_statement(ctx).compile(
            dialect=postgresql.dialect()
        )
    ).upper()
    assert "CAST(NULL AS VARCHAR)" in sql or "CAST(NULL AS TEXT)" in sql
    assert "CAST(NULL AS INTEGER)" in sql


# --- Spatial provider ---


def test_spatial_provider_lists_and_filters() -> None:
    factory = _factory()
    _, entity, _ = _seed_lifecycle(factory)
    zone, session, _ = _seed_spatial(factory, entity.id)
    provider = SpatialTimelineProvider(factory)

    ctx = TimelineQueryContext(
        event_types=tuple(_OWNED_SPATIAL),
        sort="asc",
        limit=50,
    )
    events = provider.list_events(ctx)
    types = {e.event_type for e in events}
    assert TimelineEventType.ZONE_ENTERED in types
    assert TimelineEventType.ZONE_EXITED in types
    assert TimelineEventType.ZONE_OCCUPANCY_CHANGED in types

    filtered = provider.list_events(
        TimelineQueryContext(
            event_types=tuple(_OWNED_SPATIAL),
            zone_id=zone.id,
            limit=50,
        )
    )
    assert filtered
    assert all(e.payload.get("zone_id") == str(zone.id) for e in filtered)

    entered = provider.get_event_by_id(f"zone-entered:{session.id}")
    assert entered is not None
    occ = provider.get_event_by_id(f"zone-occupancy:{session.id}:entered")
    assert occ is not None
    assert occ.payload.get("cause") == "entered"


_OWNED_SPATIAL = (
    TimelineEventType.ZONE_ENTERED,
    TimelineEventType.ZONE_EXITED,
    TimelineEventType.ZONE_OCCUPANCY_CHANGED,
)


# --- Composer ---


def test_composer_rejects_duplicate_event_ownership() -> None:
    factory = _factory()
    a = EntityLifecycleTimelineProvider(factory)
    b = EntityLifecycleTimelineProvider(factory)
    with pytest.raises(TimelineProviderRegistrationError):
        TimelineComposer([a, b])


def test_composer_rejects_duplicate_prefix_ownership() -> None:
    factory = _factory()

    class Clone(EntityLifecycleTimelineProvider):
        @property
        def name(self) -> str:
            return "clone"

        @property
        def owned_event_types(self):
            return frozenset({TimelineEventType.ENTITY_CREATED})

    with pytest.raises(TimelineProviderRegistrationError):
        TimelineComposer(
            [
                EntityLifecycleTimelineProvider(factory),
                Clone(factory),
            ]
        )


def test_composer_mixed_merge_and_pagination() -> None:
    factory = _factory()
    _, entity, _ = _seed_lifecycle(factory)
    _seed_spatial(factory, entity.id)
    composer = build_default_timeline_composer(factory)

    page = composer.list_events(
        TimelineListFilter(
            event_types=DEFAULT_TIMELINE_EVENT_TYPES,
            sort="asc",
            limit=3,
        )
    )
    assert len(page.items) == 3
    assert page.next_cursor is not None
    # Ordering
    for i in range(1, len(page.items)):
        prev, cur = page.items[i - 1], page.items[i]
        assert (prev.occurred_at, prev.id) <= (cur.occurred_at, cur.id)

    page2 = composer.list_events(
        TimelineListFilter(
            event_types=DEFAULT_TIMELINE_EVENT_TYPES,
            sort="asc",
            limit=3,
            cursor=__import__(
                "storage.timeline_cursor", fromlist=["decode_cursor"]
            ).decode_cursor(page.next_cursor),
        )
    )
    # No overlap with first page IDs
    first_ids = {e.id for e in page.items}
    assert first_ids.isdisjoint({e.id for e in page2.items})


def test_composer_limit_one_and_empty() -> None:
    factory = _factory()
    composer = build_default_timeline_composer(factory)
    empty = composer.list_events(
        TimelineListFilter(event_types=DEFAULT_TIMELINE_EVENT_TYPES, limit=10)
    )
    assert empty.items == []
    assert empty.next_cursor is None

    _, entity, _ = _seed_lifecycle(factory)
    _seed_spatial(factory, entity.id)
    page = composer.list_events(
        TimelineListFilter(
            event_types=DEFAULT_TIMELINE_EVENT_TYPES,
            sort="desc",
            limit=1,
        )
    )
    assert len(page.items) == 1
    assert page.next_cursor is not None


def test_composer_provider_skipping() -> None:
    factory = _factory()
    _, entity, _ = _seed_lifecycle(factory)
    zone, _, _ = _seed_spatial(factory, entity.id)
    composer = build_default_timeline_composer(factory)

    only_zone = composer.list_events(
        TimelineListFilter(
            event_types=DEFAULT_TIMELINE_EVENT_TYPES,
            zone_id=zone.id,
            limit=50,
        )
    )
    assert only_zone.items
    assert all(
        e.event_type
        in {
            TimelineEventType.ZONE_ENTERED,
            TimelineEventType.ZONE_EXITED,
            TimelineEventType.ZONE_OCCUPANCY_CHANGED,
        }
        for e in only_zone.items
    )

    only_created = composer.list_events(
        TimelineListFilter(
            event_types=(TimelineEventType.ENTITY_CREATED,),
            limit=50,
        )
    )
    assert all(
        e.event_type is TimelineEventType.ENTITY_CREATED
        for e in only_created.items
    )


def test_composer_get_event_routing() -> None:
    factory = _factory()
    _, entity, _ = _seed_lifecycle(factory)
    _, session, _ = _seed_spatial(factory, entity.id)
    composer = build_default_timeline_composer(factory)

    assert composer.get_event_by_id(f"entity-created:{entity.id}") is not None
    assert composer.get_event_by_id(f"zone-entered:{session.id}") is not None
    assert composer.get_event_by_id("unknown:foo") is None
    assert composer.get_event_by_id("") is None


def test_merge_identical_timestamps_stable_id_tiebreak() -> None:
    t = datetime(2026, 8, 1, tzinfo=timezone.utc)
    a = TimelineEvent(
        id="a",
        event_type=TimelineEventType.ENTITY_CREATED,
        occurred_at=t,
        source="entity",
        entity_id=uuid4(),
        camera_id="c",
        entity_type="person",
        summary="s",
    )
    b = TimelineEvent(
        id="b",
        event_type=TimelineEventType.ZONE_ENTERED,
        occurred_at=t,
        source="spatial",
        entity_id=uuid4(),
        camera_id="c",
        entity_type="person",
        summary="s",
    )
    asc = merge_ordered_streams([[a], [b]], sort="asc", limit=10)
    assert [e.id for e in asc] == ["a", "b"]
    desc = merge_ordered_streams([[a], [b]], sort="desc", limit=10)
    assert [e.id for e in desc] == ["b", "a"]


def test_provider_bound_enforced() -> None:
    factory = _factory()
    _, entity, _ = _seed_lifecycle(factory)
    _seed_spatial(factory, entity.id)
    provider = EntityLifecycleTimelineProvider(factory)
    ctx = TimelineQueryContext(
        event_types=(TimelineEventType.ENTITY_CREATED,),
        limit=1,
    )
    events = provider.list_events(ctx)
    assert len(events) <= 1


def test_facade_repository_matches_composer() -> None:
    factory = _factory()
    _, entity, _ = _seed_lifecycle(factory)
    _seed_spatial(factory, entity.id)
    repo = TimelineRepository(factory)
    composer = build_default_timeline_composer(factory)
    filters = TimelineListFilter(
        event_types=DEFAULT_TIMELINE_EVENT_TYPES,
        sort="desc",
        limit=20,
    )
    a = repo.list_events(filters)
    b = composer.list_events(filters)
    assert [e.id for e in a.items] == [e.id for e in b.items]
    assert a.next_cursor == b.next_cursor


def test_union_branch_column_names_via_facade() -> None:
    factory = _factory()
    repo = TimelineRepository(factory)
    filters = TimelineListFilter(
        event_types=tuple(TimelineEventType),
        limit=5,
    )
    for branch in (
        repo._created_select(filters),
        repo._zone_entered_select(filters),
    ):
        names = [col.key for col in branch.selected_columns]
        assert names == list(TIMELINE_UNION_COLUMN_NAMES)
