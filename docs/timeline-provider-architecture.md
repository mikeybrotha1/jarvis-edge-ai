# Timeline Provider Architecture (v0.7.0)

v0.7.0 replaces the monolithic timeline projection with **domain providers**
composed by `TimelineComposer`. Public REST, WebSocket, console, and
TimelineEvent behaviour remain identical to released v0.6.0.

## Modules

```
src/timeline/
  contracts.py          # typed projection contract + row → TimelineEvent
  provider.py           # TimelineProvider protocol + TimelineQueryContext
  composer.py           # registration + bounded k-way merge
  factory.py            # default provider set
  providers/
    entity_lifecycle.py
    spatial.py

src/storage/timeline_repository.py   # BC facade over composer
src/services/timeline_service.py     # validation + composition backend
```

## Provider interface

Each provider implements:

| Member | Responsibility |
|--------|----------------|
| `name` | Stable diagnostic name; also used for deterministic registration order |
| `owned_event_types` | Exclusive event-type ownership |
| `owned_id_prefixes` | Exclusive stable-ID prefixes |
| `supports_event_id` | Prefix ownership check |
| `can_contribute` | Cheap filter short-circuit (e.g. `zone_id` skips lifecycle) |
| `list_events(context)` | ≤ `context.limit` ordered events; filters in SQL |
| `get_event_by_id` | Single owned event or `None` |

Providers must **not** expose SQLAlchemy sessions or raw selectables through
the public protocol.

## Ownership (non-overlapping)

| Provider | Event types | ID prefixes |
|----------|-------------|-------------|
| `EntityLifecycleTimelineProvider` | `entity_created`, `entity_closed`, `observation_recorded` | `entity-created:`, `entity-closed:`, `observation:` |
| `SpatialTimelineProvider` | `zone_entered`, `zone_exited`, `zone_occupancy_changed` | `zone-entered:`, `zone-exited:`, `zone-occupancy:` |

Registration rejects duplicate event-type or prefix ownership at construction
time (`TimelineProviderRegistrationError`). Failures surface at app/test
startup, not on the first request.

**Rule:** event-type ownership cannot overlap. Future providers must claim
disjoint types and prefixes.

## Typed projection contract

Formalized in `timeline.contracts` (same columns as v0.6.0):

`event_id`, `event_type`, `occurred_at`, `source`, `entity_id`, `camera_id`,
`entity_type`, `identity_key`, `track_id`, `status`, `confidence`,
`frame_number`, `source_event_type`, `zone_id`, `zone_name`, `session_id`,
`occupancy`.

Every multi-branch SELECT uses `projection(...)` with typed NULLs
(`CAST(NULL AS VARCHAR|INTEGER|BIGINT|FLOAT|TIMESTAMP)`) so PostgreSQL UNION
ALL remains safe. See `tests/test_timeline_union_types.py`.

## Query context

`TimelineQueryContext` carries existing filters:

- time range, cursor, entity_id, camera_id, entity_type, zone_id
- event_types, sort, limit

Public parameter names and validation are unchanged. The composer sets each
provider’s limit to **public N + 1**.

## Pagination / merge algorithm

For public limit `N`:

1. Select relevant providers (`can_contribute`).
2. Each provider fetches ≤ `N + 1` ordered events with the same global cursor.
3. Merge streams (k-way / two-pointer) by `occurred_at` then stable `event_id`
   with existing asc/desc semantics.
4. Take first `N + 1` globally; return first `N`.
5. Set `next_cursor` from the last returned event iff a global `N + 1` exists.

Memory is O(providers × N). Providers never return unbounded histories.

## get_event_by_id routing

Composer dispatches by prefix ownership only. Unknown/malformed IDs remain
not-found (404 at the API).

## Adding a future provider

1. Implement `TimelineProvider` with exclusive types and prefixes.
2. Project through `timeline.contracts.projection`.
3. Register via `TimelineComposer([...])` or extend `build_default_timeline_providers`.
4. Add isolation tests + composer merge tests + PostgreSQL typed-null compile test.
5. Do **not** add overlapping ownership.

## Performance expectations (Pi 5)

- Provider SQL bound: `LIMIT N+1`
- Filters pushed into SQL
- No full-history sort in application memory
- Index-friendly predicates preserved from v0.6.0

## Compatibility

`TimelineRepository(session_factory)` remains a facade over the default
composer so existing tests and DI keep working without silent alternate
production paths.
