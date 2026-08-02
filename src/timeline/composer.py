"""Compose multiple TimelineProviders into one ordered page (v0.7.0)."""

from __future__ import annotations

from collections.abc import Sequence

from storage.timeline_cursor import encode_cursor
from storage.timeline_models import TimelineEvent, TimelineListFilter, TimelinePage
from timeline.contracts import compare_events
from timeline.provider import TimelineProvider, TimelineQueryContext


class TimelineProviderRegistrationError(RuntimeError):
    """Raised when provider ownership conflicts are detected at construction."""


class TimelineComposer:
    """Register providers and merge bounded provider streams.

    Public pagination matches v0.6.0:

    1. Apply the same filters/cursor to every relevant provider.
    2. Fetch at most public_limit + 1 from each.
    3. Merge with occurred_at then event_id ordering.
    4. Return first N; set next_cursor when a global N+1 exists.
    """

    def __init__(self, providers: Sequence[TimelineProvider]) -> None:
        self._providers = self._validate_and_order(providers)

    @property
    def providers(self) -> tuple[TimelineProvider, ...]:
        return self._providers

    def list_events(self, filters: TimelineListFilter) -> TimelinePage:
        if filters.limit < 1:
            raise ValueError("limit must be >= 1")

        public_limit = filters.limit
        provider_limit = public_limit + 1
        context = TimelineQueryContext.from_list_filter(
            filters,
            provider_limit=provider_limit,
        )

        streams: list[list[TimelineEvent]] = []
        for provider in self._providers:
            if not provider.can_contribute(context):
                continue
            batch = provider.list_events(context)
            if len(batch) > provider_limit:
                raise RuntimeError(
                    f"Provider {provider.name!r} returned {len(batch)} events "
                    f"exceeding bound {provider_limit}."
                )
            if batch:
                streams.append(batch)

        merged = merge_ordered_streams(
            streams,
            sort=filters.sort,
            limit=provider_limit,
        )
        has_more = len(merged) > public_limit
        items = merged[:public_limit]

        next_cursor: str | None = None
        if has_more and items:
            last = items[-1]
            next_cursor = encode_cursor(last.occurred_at, last.id)

        return TimelinePage(
            items=items,
            limit=public_limit,
            next_cursor=next_cursor,
        )

    def get_event_by_id(self, event_id: str) -> TimelineEvent | None:
        event_id = event_id.strip()
        if not event_id:
            return None

        for provider in self._providers:
            if provider.supports_event_id(event_id):
                return provider.get_event_by_id(event_id)
        return None

    @staticmethod
    def _validate_and_order(
        providers: Sequence[TimelineProvider],
    ) -> tuple[TimelineProvider, ...]:
        if not providers:
            raise TimelineProviderRegistrationError(
                "At least one TimelineProvider is required."
            )

        ordered = tuple(sorted(providers, key=lambda p: p.name))
        seen_types: dict[str, str] = {}
        seen_prefixes: dict[str, str] = {}

        for provider in ordered:
            for event_type in provider.owned_event_types:
                key = event_type.value
                if key in seen_types:
                    raise TimelineProviderRegistrationError(
                        f"Duplicate event-type ownership for {key!r}: "
                        f"{seen_types[key]!r} and {provider.name!r}."
                    )
                seen_types[key] = provider.name

            for prefix in provider.owned_id_prefixes:
                if prefix in seen_prefixes:
                    raise TimelineProviderRegistrationError(
                        f"Duplicate stable-ID prefix ownership for {prefix!r}: "
                        f"{seen_prefixes[prefix]!r} and {provider.name!r}."
                    )
                seen_prefixes[prefix] = provider.name

        return ordered


def merge_ordered_streams(
    streams: list[list[TimelineEvent]],
    *,
    sort: str,
    limit: int,
) -> list[TimelineEvent]:
    """Bounded k-way merge of already-ordered provider streams.

    Memory is O(provider_count) plus the output list (≤ limit). Does not sort
    full histories.
    """

    if limit < 1:
        return []
    if not streams:
        return []
    if len(streams) == 1:
        return streams[0][:limit]

    indices = [0] * len(streams)
    result: list[TimelineEvent] = []
    while len(result) < limit:
        best_i: int | None = None
        best: TimelineEvent | None = None
        for i, stream in enumerate(streams):
            pos = indices[i]
            if pos >= len(stream):
                continue
            candidate = stream[pos]
            if best is None or compare_events(candidate, best, sort=sort) < 0:
                best = candidate
                best_i = i
        if best is None or best_i is None:
            break
        result.append(best)
        indices[best_i] += 1
    return result
