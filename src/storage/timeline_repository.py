"""Timeline repository facade over the v0.7.0 provider architecture.

Production listing and ID resolution go through :class:`TimelineComposer`.
This module keeps the historical import path and re-exports the typed
projection contract so existing tests and wiring continue to work.

Domain SQL lives in:

- ``timeline.providers.entity_lifecycle``
- ``timeline.providers.spatial``
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import literal, null, select, union_all
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import Select

from storage.timeline_models import (
    TimelineEvent,
    TimelineEventType,
    TimelineListFilter,
    TimelinePage,
)
from timeline.composer import TimelineComposer
from timeline.contracts import (
    TIMELINE_UNION_COLUMN_NAMES,
    _null_projection_defaults,
    _projection,
    null_projection_defaults,
    projection,
    row_to_timeline_event,
)
from timeline.factory import build_default_timeline_composer
from timeline.provider import TimelineQueryContext
from timeline.providers.entity_lifecycle import EntityLifecycleTimelineProvider
from timeline.providers.spatial import SpatialTimelineProvider

# Re-export contract symbols for tests that import from this module.
__all__ = [
    "TIMELINE_UNION_COLUMN_NAMES",
    "TimelineRepository",
    "_null_projection_defaults",
    "_projection",
    "null_projection_defaults",
    "projection",
    "row_to_timeline_event",
]


class TimelineRepository:
    """Backward-compatible facade over TimelineComposer + domain providers.

    Prefer constructing :class:`timeline.composer.TimelineComposer` via
    :func:`timeline.factory.build_default_timeline_composer` for new code.
    ``TimelineService`` accepts either this facade or a composer.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        composer: TimelineComposer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._composer = composer or build_default_timeline_composer(
            session_factory
        )
        # Direct provider handles for tests / SQL compilation helpers.
        self._lifecycle = EntityLifecycleTimelineProvider(session_factory)
        self._spatial = SpatialTimelineProvider(session_factory)

    @property
    def composer(self) -> TimelineComposer:
        return self._composer

    def list_events(self, filters: TimelineListFilter) -> TimelinePage:
        """Return one cursor page of timeline events (provider composition)."""

        return self._composer.list_events(filters)

    def get_event_by_id(self, event_id: str) -> TimelineEvent | None:
        """Resolve one stable namespaced timeline event id."""

        return self._composer.get_event_by_id(event_id)

    def _build_list_statement(self, filters: TimelineListFilter) -> Select[Any]:
        """Compile a single UNION of provider branches for type regression tests.

        Production queries use per-provider SELECTs merged in the composer.
        This helper preserves PostgreSQL typed-null union coverage by combining
        the same branch selectables both providers emit.
        """

        context = TimelineQueryContext.from_list_filter(
            filters,
            provider_limit=max(filters.limit + 1, 1),
        )
        branches: list[Select[Any]] = []

        for event_type in filters.event_types:
            if event_type is TimelineEventType.ENTITY_CREATED:
                if filters.zone_id is None:
                    branches.append(self._lifecycle._created_select(context))
            elif event_type is TimelineEventType.ENTITY_CLOSED:
                if filters.zone_id is None:
                    branches.append(self._lifecycle._closed_select(context))
            elif event_type is TimelineEventType.OBSERVATION_RECORDED:
                if filters.zone_id is None:
                    branches.append(
                        self._lifecycle._observation_select(context)
                    )
            elif event_type is TimelineEventType.ZONE_ENTERED:
                branches.append(self._spatial._zone_entered_select(context))
            elif event_type is TimelineEventType.ZONE_EXITED:
                branches.append(self._spatial._zone_exited_select(context))
            elif event_type is TimelineEventType.ZONE_OCCUPANCY_CHANGED:
                branches.append(
                    self._spatial._zone_occupancy_entered_select(context)
                )
                branches.append(
                    self._spatial._zone_occupancy_exited_select(context)
                )

        if not branches:
            empty = select(
                *projection(
                    event_id=literal(""),
                    event_type=literal(""),
                    occurred_at=null(),
                    source=literal(""),
                    entity_id=literal(""),
                    camera_id=null(),
                    entity_type=literal(""),
                    **null_projection_defaults(),
                )
            ).where(literal(False))
            return empty.limit(0)

        combined = union_all(*branches).subquery("timeline_events")
        occurred_at = combined.c.occurred_at
        event_id = combined.c.event_id

        statement = select(
            combined.c.event_id,
            combined.c.event_type,
            combined.c.occurred_at,
            combined.c.source,
            combined.c.entity_id,
            combined.c.camera_id,
            combined.c.entity_type,
            combined.c.identity_key,
            combined.c.track_id,
            combined.c.status,
            combined.c.confidence,
            combined.c.frame_number,
            combined.c.source_event_type,
            combined.c.zone_id,
            combined.c.zone_name,
            combined.c.session_id,
            combined.c.occupancy,
        )

        if filters.cursor is not None:
            from sqlalchemy import and_, or_

            cursor_at = filters.cursor.occurred_at
            cursor_id = filters.cursor.event_id
            if filters.sort == "asc":
                statement = statement.where(
                    or_(
                        occurred_at > cursor_at,
                        and_(
                            occurred_at == cursor_at,
                            event_id > cursor_id,
                        ),
                    )
                )
            else:
                statement = statement.where(
                    or_(
                        occurred_at < cursor_at,
                        and_(
                            occurred_at == cursor_at,
                            event_id < cursor_id,
                        ),
                    )
                )

        if filters.sort == "asc":
            statement = statement.order_by(occurred_at.asc(), event_id.asc())
        else:
            statement = statement.order_by(
                occurred_at.desc(),
                event_id.desc(),
            )

        return statement.limit(filters.limit + 1)

    # --- Test helpers mirroring pre-v0.7 select method names ---

    def _created_select(self, filters: TimelineListFilter) -> Select[Any]:
        ctx = TimelineQueryContext.from_list_filter(filters)
        return self._lifecycle._created_select(ctx)

    def _closed_select(self, filters: TimelineListFilter) -> Select[Any]:
        ctx = TimelineQueryContext.from_list_filter(filters)
        return self._lifecycle._closed_select(ctx)

    def _observation_select(self, filters: TimelineListFilter) -> Select[Any]:
        ctx = TimelineQueryContext.from_list_filter(filters)
        return self._lifecycle._observation_select(ctx)

    def _zone_entered_select(self, filters: TimelineListFilter) -> Select[Any]:
        ctx = TimelineQueryContext.from_list_filter(filters)
        return self._spatial._zone_entered_select(ctx)

    def _zone_exited_select(self, filters: TimelineListFilter) -> Select[Any]:
        ctx = TimelineQueryContext.from_list_filter(filters)
        return self._spatial._zone_exited_select(ctx)

    def _zone_occupancy_entered_select(
        self,
        filters: TimelineListFilter,
    ) -> Select[Any]:
        ctx = TimelineQueryContext.from_list_filter(filters)
        return self._spatial._zone_occupancy_entered_select(ctx)

    def _zone_occupancy_exited_select(
        self,
        filters: TimelineListFilter,
    ) -> Select[Any]:
        ctx = TimelineQueryContext.from_list_filter(filters)
        return self._spatial._zone_occupancy_exited_select(ctx)
