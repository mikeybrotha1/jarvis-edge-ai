# Timeline & Activity History API (v0.4.2)

Read-only chronological timeline derived from existing entity memory tables.
**No `timeline_events` table** is created.

## Architecture

```
FastAPI routes
  → TimelineService
    → TimelineRepository (SQLAlchemy Core UNION ALL)
      → entities / entity_observations
```

Routes never issue SQLAlchemy queries directly. The repository reuses the
shared SQLAlchemy session factory from entity memory.

## Event types

| Type | Source | `occurred_at` | Stable id |
|------|--------|---------------|-----------|
| `entity_created` | `entities` | `first_seen` | `entity-created:{entity_id}` |
| `entity_closed` | `entities` where `status=closed` | `last_seen` | `entity-closed:{entity_id}` |
| `observation_recorded` | `entity_observations` | `observed_at` | `observation:{observation_id}` |

There is no `entity_status_changed` event: the schema does not store a full
status-transition history.

### Default behaviour

`GET /api/v1/timeline` returns **lifecycle events only**:

- `entity_created`
- `entity_closed`

Observations appear **only** when `event_type=observation_recorded` is
requested (repeatable query parameter).

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/timeline` | Global timeline |
| `GET` | `/api/v1/timeline/{event_id}` | One event by stable id |
| `GET` | `/api/v1/entities/{entity_id}/timeline` | Entity-scoped timeline (404 if missing) |

### Filters (`GET /api/v1/timeline`)

| Param | Notes |
|-------|-------|
| `occurred_after` / `occurred_before` | ISO 8601; AND range on `occurred_at` |
| `entity_id` | UUID |
| `event_type` | Repeatable; default lifecycle-only |
| `camera_id` | Matches entity/observation camera |
| `entity_type` | Detector label (`person`, …) |
| `limit` | 1…`timeline.maximum_limit` (default 50) |
| `cursor` | Opaque next-page token |
| `sort` | `asc` or `desc` (default `desc`) |

**Naive timestamps** are treated as **UTC** (same convention as internal
normalisation). Prefer explicit offsets or `Z`.

## Cursor pagination

Response shape:

```json
{
  "items": [],
  "limit": 50,
  "next_cursor": null
}
```

- No `total` field.
- Cursor encodes last `(occurred_at, event_id)` as opaque base64url JSON.
- Total order: `occurred_at`, then `event_id`.
- Fetch uses `limit + 1` rows server-side to compute `next_cursor`.

## Configuration

```yaml
timeline:
  default_limit: 50
  maximum_limit: 200
```

Env:

- `JARVIS_TIMELINE_DEFAULT_LIMIT`
- `JARVIS_TIMELINE_MAXIMUM_LIMIT`

## Indexes (migration `20260728_0002`)

Added (no lifecycle schema changes):

- `entities (first_seen, id)`
- `entities (status, last_seen, id)`
- `entities (camera_id, first_seen, id)`
- `entities (camera_id, last_seen, id)`
- `entity_observations (observed_at, id)`
- `entity_observations (entity_id, observed_at, id)`
- `entity_observations (camera_id, observed_at, id)`

## Example curl

```bash
# Lifecycle-only (default)
curl -s 'http://127.0.0.1:8080/api/v1/timeline?limit=20'

# Include observations
curl -s 'http://127.0.0.1:8080/api/v1/timeline?event_type=entity_created&event_type=observation_recorded'

# Entity scoped
curl -s "http://127.0.0.1:8080/api/v1/entities/${ENTITY_ID}/timeline"

# Single event
curl -s "http://127.0.0.1:8080/api/v1/timeline/entity-created:${ENTITY_ID}"
```

## Example response

```json
{
  "items": [
    {
      "id": "entity-created:3f1c0e4a-2b8d-4c5a-9f11-0123456789ab",
      "event_type": "entity_created",
      "occurred_at": "2026-07-28T12:00:00+00:00",
      "source": "entity",
      "entity_id": "3f1c0e4a-2b8d-4c5a-9f11-0123456789ab",
      "camera_id": "front-door",
      "entity_type": "person",
      "summary": "Person appeared on front-door",
      "payload": {
        "identity_key": "camera:front-door:tracker:1",
        "track_id": 1,
        "status": "active"
      }
    }
  ],
  "limit": 50,
  "next_cursor": null
}
```

## Errors

| Status | When |
|--------|------|
| `404` | Unknown entity (scoped endpoint) or unknown event id |
| `422` | Invalid limit, sort, date range, event type, or cursor |
| `503` | Database failure (sanitized; no SQL/secrets/stack traces) |

## Performance constraints

- No full-table loads into Python for pagination.
- Filters, cursor predicates, ordering, and limits are applied in SQL.
- UNION ALL of selected event branches only.

## Non-goals

- No write endpoints
- No LLM-generated summaries
- No raw image paths / model weights in payloads
- No exact total counts
- No camera or Hailo initialisation for API startup
- No stored timeline table
