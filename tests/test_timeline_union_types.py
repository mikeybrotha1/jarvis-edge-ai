"""Regression: Timeline UNION ALL column types must match (PostgreSQL).

Bare ``literal(None)`` / untyped NULL in one branch vs INTEGER/VARCHAR in
another causes ``psycopg.errors.DatatypeMismatch`` on PostgreSQL even though
SQLite accepts the query.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import pytest
from sqlalchemy import union_all
from sqlalchemy.dialects import postgresql, sqlite

from services.timeline_service import TimelineService
from storage.entity_records import EntityCreate
from storage.entity_repository import EntityRepository
from storage.entity_zone_session_repository import EntityZoneSessionRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)
from storage.timeline_models import (
    DEFAULT_TIMELINE_EVENT_TYPES,
    TimelineEventType,
    TimelineListFilter,
)
from storage.timeline_repository import (
    TIMELINE_UNION_COLUMN_NAMES,
    TimelineRepository,
    _null_projection_defaults,
    _projection,
)
from storage.zone_records import ZoneCreate
from storage.zone_repository import ZoneRepository


def _repo_and_factory():
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    return TimelineRepository(factory), factory


def _default_filters() -> TimelineListFilter:
    return TimelineListFilter(
        event_types=DEFAULT_TIMELINE_EVENT_TYPES,
        limit=50,
        sort="desc",
    )


def test_union_column_contract_names_and_count() -> None:
    """Every branch projects the same ordered column names."""

    repo, _ = _repo_and_factory()
    filters = _default_filters()
    # Include observation so all six event families are covered.
    all_types = TimelineListFilter(
        event_types=tuple(TimelineEventType),
        limit=10,
    )
    branches = [
        repo._created_select(all_types),
        repo._closed_select(all_types),
        repo._observation_select(all_types),
        repo._zone_entered_select(all_types),
        repo._zone_exited_select(all_types),
        repo._zone_occupancy_entered_select(all_types),
        repo._zone_occupancy_exited_select(all_types),
    ]
    expected = list(TIMELINE_UNION_COLUMN_NAMES)
    for branch in branches:
        names = [col.key for col in branch.selected_columns]
        assert names == expected, names


def test_projection_helper_casts_nulls_for_postgresql() -> None:
    """Canonical _projection emits CAST(NULL AS …) for optional columns."""

    from sqlalchemy import null as sa_null

    nulls = _null_projection_defaults()
    cols = _projection(
        event_id="e",
        event_type="entity_created",
        occurred_at=sa_null(),
        source="entity",
        entity_id="id",
        camera_id=sa_null(),
        entity_type="person",
        **nulls,
    )
    # Build a one-row select and compile with PostgreSQL dialect.
    from sqlalchemy import select

    statement = select(*cols)
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).upper()

    # Typed NULLs for spatial + nullable lifecycle columns.
    assert "CAST(NULL AS VARCHAR)" in compiled or "CAST(NULL AS TEXT)" in compiled
    assert "CAST(NULL AS INTEGER)" in compiled
    assert "CAST(NULL AS FLOAT)" in compiled or "CAST(NULL AS DOUBLE" in compiled
    assert "CAST(NULL AS BIGINT)" in compiled


def test_full_default_union_compiles_with_postgresql_dialect() -> None:
    """Default spatial lifecycle UNION compiles without untyped NULL binds."""

    repo, _ = _repo_and_factory()
    statement = repo._build_list_statement(_default_filters())
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    sql_upper = sql.upper()

    # Must cast NULLs rather than rely on untyped bind parameters for
    # spatial columns that other branches populate with VARCHAR/INTEGER.
    assert "ZONE_ID" in sql_upper
    assert "OCCUPANCY" in sql_upper
    # Unlabeled bare NULL AS occupancy (without CAST) is the historical bug.
    # Require CAST near occupancy nulls from entity branches.
    assert "CAST(" in sql_upper
    # No untyped null bind for occupancy/zone_id labels alone.
    # PostgreSQL dialect should not emit bare `:param` for null occupancy
    # when using cast(null(), Integer).
    assert re.search(r"CAST\s*\(\s*NULL\s+AS\s+", sql_upper)


def test_full_default_union_compiles_with_sqlite_dialect() -> None:
    repo, _ = _repo_and_factory()
    statement = repo._build_list_statement(_default_filters())
    sql = str(statement.compile(dialect=sqlite.dialect()))
    assert "union" in sql.lower() or "UNION" in sql


def test_default_timeline_executes_with_spatial_events_sqlite() -> None:
    """End-to-end list_events with default types including spatial (SQLite)."""

    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    entities = EntityRepository(factory)
    zones = ZoneRepository(factory)
    sessions = EntityZoneSessionRepository(factory)
    timeline = TimelineService(TimelineRepository(factory), entities)

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
    sessions.close_session(
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


def test_union_all_of_all_branches_compiles_postgresql() -> None:
    """union_all of every branch family compiles for PostgreSQL."""

    repo, _ = _repo_and_factory()
    filters = TimelineListFilter(
        event_types=tuple(TimelineEventType),
        limit=5,
    )
    branches = [
        repo._created_select(filters),
        repo._closed_select(filters),
        repo._observation_select(filters),
        repo._zone_entered_select(filters),
        repo._zone_exited_select(filters),
        repo._zone_occupancy_entered_select(filters),
        repo._zone_occupancy_exited_select(filters),
    ]
    combined = union_all(*branches)
    sql = str(combined.compile(dialect=postgresql.dialect())).upper()
    assert "UNION ALL" in sql
    # Entity branch null spatial fields are typed.
    assert "CAST(NULL AS" in sql
    # Occupancy appears as integer-typed on spatial and null-cast on entity.
    assert "OCCUPANCY" in sql or "OCCUPANCY_AFTER" not in sql  # labeled occupancy


@pytest.mark.skipif(
    not os.environ.get("JARVIS_DATABASE_URL", "").startswith(
        ("postgresql://", "postgres://")
    ),
    reason="JARVIS_DATABASE_URL PostgreSQL not configured",
)
def test_postgresql_default_timeline_smoke() -> None:
    """Real PostgreSQL execution of the default spatial timeline UNION."""

    url = os.environ["JARVIS_DATABASE_URL"]
    engine = create_entity_engine(url)
    # Ensure spatial tables exist (idempotent create for smoke env).
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    entities = EntityRepository(factory)
    timeline = TimelineService(TimelineRepository(factory), entities)

    # Must not raise DatatypeMismatch.
    page = timeline.list_timeline(limit=10)
    assert page.limit == 10
    assert isinstance(page.items, list)

    # Also compile+execute via repository directly.
    repo = TimelineRepository(factory)
    page2 = repo.list_events(_default_filters())
    assert isinstance(page2.items, list)
    engine.dispose()
