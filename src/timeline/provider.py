"""Timeline provider protocol and query context (v0.7.0)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from storage.timeline_models import (
    TimelineCursor,
    TimelineEvent,
    TimelineEventType,
    TimelineListFilter,
)


@dataclass(frozen=True, slots=True)
class TimelineQueryContext:
    """Internal filters passed to providers (maps 1:1 from TimelineListFilter).

    ``limit`` is the **provider** fetch bound (composer passes public N + 1).
    Providers must never return more than ``limit`` events and must never
    materialise unbounded histories.
    """

    event_types: tuple[TimelineEventType, ...]
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None
    entity_id: UUID | None = None
    camera_id: str | None = None
    entity_type: str | None = None
    zone_id: UUID | None = None
    cursor: TimelineCursor | None = None
    sort: str = "desc"
    limit: int = 50

    @classmethod
    def from_list_filter(
        cls,
        filters: TimelineListFilter,
        *,
        provider_limit: int | None = None,
    ) -> "TimelineQueryContext":
        """Build context from public list filters.

        ``provider_limit`` defaults to ``filters.limit``; the composer sets
        ``filters.limit + 1`` so each provider stays bounded.
        """

        limit = filters.limit if provider_limit is None else provider_limit
        if limit < 1:
            raise ValueError("provider limit must be >= 1")
        return cls(
            event_types=filters.event_types,
            occurred_after=filters.occurred_after,
            occurred_before=filters.occurred_before,
            entity_id=filters.entity_id,
            camera_id=filters.camera_id,
            entity_type=filters.entity_type,
            zone_id=filters.zone_id,
            cursor=filters.cursor,
            sort=filters.sort,
            limit=limit,
        )

    def relevant_event_types(
        self,
        owned: frozenset[TimelineEventType],
    ) -> tuple[TimelineEventType, ...]:
        """Intersection of request event types with provider ownership."""

        return tuple(t for t in self.event_types if t in owned)


@runtime_checkable
class TimelineProvider(Protocol):
    """Domain timeline projection source.

    Implementations must not expose SQLAlchemy sessions or raw selectables
    through this protocol. SQL stays inside the provider module.
    """

    @property
    def name(self) -> str:
        """Stable provider name for diagnostics and ordering."""

        ...

    @property
    def owned_event_types(self) -> frozenset[TimelineEventType]:
        """Event types exclusively owned by this provider."""

        ...

    @property
    def owned_id_prefixes(self) -> frozenset[str]:
        """Stable-ID prefixes exclusively owned by this provider."""

        ...

    def supports_event_id(self, event_id: str) -> bool:
        """Return True when this provider owns the stable-ID prefix."""

        ...

    def can_contribute(self, context: TimelineQueryContext) -> bool:
        """Return False when filters prove this provider cannot contribute."""

        ...

    def list_events(self, context: TimelineQueryContext) -> list[TimelineEvent]:
        """Return at most ``context.limit`` ordered events.

        Must apply filters and cursor boundaries in SQL. Must not return an
        unbounded collection.
        """

        ...

    def get_event_by_id(self, event_id: str) -> TimelineEvent | None:
        """Resolve one owned event, or None when not found / not owned."""

        ...
