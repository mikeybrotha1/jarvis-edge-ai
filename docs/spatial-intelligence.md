# Spatial Intelligence (v0.6.0)

Complete, independently useful zone membership for Jarvis Edge AI.

## Capabilities

Users can:

- create named camera-specific rectangle zones
- update and soft-disable zones
- observe entities entering and exiting zones
- query current zone occupancy and entities
- query historical entity-zone sessions
- query zones visited by an entity
- see spatial events in the Timeline API
- receive spatial events on the existing WebSocket
- manage and inspect zones in the Live Activity Console

## Coordinates and geometry

Zones are **camera-specific** and use **normalised coordinates** in `[0.0, 1.0]`.

v0.6.0 supports **rectangle creation only**. Geometry is stored in a polygon-ready form:

```json
{
  "geometry_type": "rectangle",
  "vertices": [
    {"x": x_min, "y": y_min},
    {"x": x_max, "y": y_min},
    {"x": x_max, "y": y_max},
    {"x": x_min, "y": y_max}
  ]
}
```

Boundaries are **inclusive**.

## Matching

Pixel bounding boxes are `{x1, y1, x2, y2}`. They are normalised with configured camera width/height.

Strategies:

- `bottom_center` — default for `person` (and global default)
- `center` — default for other labels; also available as a zone override

## State machine

```
outside → candidate_enter → inside → candidate_exit → exited
```

Defaults:

- `enter_confirm_observations: 3`
- `exit_confirm_observations: 3`
- `lost_track_timeout_seconds: 15`

Durable sessions open only on confirmed enter and close on confirmed exit, entity close, or lost-track reconciliation.

An entity may occupy **multiple overlapping zones** at once.

## Event types and stable IDs

| Event | Stable ID |
|-------|-----------|
| `zone_entered` | `zone-entered:{session_id}` |
| `zone_exited` | `zone-exited:{session_id}` |
| `zone_occupancy_changed` | `zone-occupancy:{session_id}:entered` or `:exited` |

Source: `spatial`.

Default timeline/WebSocket lifecycle includes these spatial events (observations remain opt-in).

## Occupancy

`GET /api/v1/zones/{zone_id}/occupancy` returns **current open sessions** after stale-session rules. Clients must not replay timeline history for occupancy.

Dwell seconds are derived from `entered_at` and `exited_at` / now (not per-frame counters).

## Transaction boundary

Spatial evaluation runs in the vision/persistence process inside the same `session_scope` as entity + observation writes:

1. persist entity/observation
2. evaluate zones
3. open/close sessions
4. register `pg_notify`
5. commit once

Rollback undoes all of the above.

## REST endpoints

| Method | Path |
|--------|------|
| GET | `/api/v1/zones` |
| POST | `/api/v1/zones` |
| GET | `/api/v1/zones/{zone_id}` |
| PATCH | `/api/v1/zones/{zone_id}` |
| GET | `/api/v1/zones/{zone_id}/occupancy` |
| GET | `/api/v1/zones/{zone_id}/entities` |
| GET | `/api/v1/zones/{zone_id}/sessions` |
| GET | `/api/v1/entities/{entity_id}/zones` |

Prefer disable (`enabled: false`) over hard delete.

## WebSocket

Same `/ws/v1/activity` endpoint. Subscription filters add optional `zone_ids`. Filtering is AND across categories, OR within a category.

Default subscription includes entity lifecycle + spatial events.

## Configuration

```yaml
spatial:
  enabled: true
  position_strategy: bottom_center
  enter_confirm_observations: 3
  exit_confirm_observations: 3
  lost_track_timeout_seconds: 15
  maximum_zones_per_camera: 10
  occupancy_stale_seconds: 60
  publish_occupancy_changes: true
```

Environment: `JARVIS_SPATIAL_*`.

When `spatial.enabled` is false: entity persistence continues; zone REST remains available; no matching or spatial events.

## Performance limits (Pi 5)

- max 10 zones per camera (default)
- pure point-in-rectangle
- writes only on transitions / stale reconciliation
- per-camera zone cache with invalidation on zone updates
- occupancy from indexed open sessions

## Explicit non-goals

- arbitrary polygon editing
- Shapely / OpenCV in API
- natural-language queries, LLM summaries, agents
- face recognition, cross-camera identity
- floor plans / 3D mapping / live video
- second event log or message broker
- per-frame presence events
- `zone_dwell_threshold_reached` (deferred)

## Restart behaviour

Open sessions survive process restart. Transient candidate counters do not; membership is recovered from durable open sessions. Lost-track timeout closes stale opens after unclean shutdown or camera disconnect.
