# Entity Query API (v0.4.1)

Read-only HTTP API for inspecting persistent entities and observations
produced by Jarvis entity memory (v0.4.0).

The API does **not** open cameras or load Hailo models. It only needs a
database URL and the entity-memory schema.

## Startup

From the repository root (after migrations / schema are applied):

```bash
set -a && source .env.jarvis && set +a
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

# Option A — module entrypoint (uses api.* config)
python -m api

# Option B — uvicorn factory
uvicorn api.app:create_app_from_config --factory \
  --host 0.0.0.0 --port 8080
```

OpenAPI docs are served at `/docs` when the process is running.

## Configuration

YAML section `api` (see `config/jarvis.example.yaml`):

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Soft flag (startup still allowed via `python -m api`) |
| `host` | `0.0.0.0` | Bind address |
| `port` | `8080` | Bind port |
| `default_limit` | `50` | Default page size for entity lists |
| `maximum_limit` | `200` | Maximum page size for entity lists |

Environment overrides:

- `JARVIS_API_ENABLED`
- `JARVIS_API_HOST`
- `JARVIS_API_PORT`
- `JARVIS_API_DEFAULT_LIMIT`
- `JARVIS_API_MAXIMUM_LIMIT`

Observation list defaults (not YAML-configurable):

- default limit `100`
- maximum limit `500`

Requires `database.url` / `JARVIS_DATABASE_URL`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness |
| `GET` | `/api/v1/entities` | Filtered entity list |
| `GET` | `/api/v1/entities/active` | Active entities only |
| `GET` | `/api/v1/entities/recent` | Entities by `last_seen` desc |
| `GET` | `/api/v1/entities/{entity_id}` | Single entity |
| `GET` | `/api/v1/entities/{entity_id}/observations` | Observations for one entity |

### Entity list filters

| Query param | Notes |
|-------------|-------|
| `status` | `active` or `closed` |
| `entity_type` | Detector label (`person`, `car`, …); maps to stored `label` |
| `camera_id` | Camera identifier |
| `seen_after` | ISO 8601; filters `last_seen >=` |
| `seen_before` | ISO 8601; filters `last_seen <=` |
| `limit` | 1 … `maximum_limit` (default `default_limit`) |
| `offset` | >= 0 |
| `sort` | `asc` or `desc` by `last_seen` (default `desc`) |

### Observation filters

| Query param | Notes |
|-------------|-------|
| `seen_after` / `seen_before` | Filter `observed_at` |
| `limit` / `offset` / `sort` | Page observations by `observed_at` |

## Pagination

Collection responses always include:

```json
{
  "items": [],
  "total": 0,
  "limit": 50,
  "offset": 0
}
```

Totals and page windows are computed in SQL (not by loading full tables into Python).

Invalid ranges (`seen_after` > `seen_before`) or oversize limits return HTTP `422`.
Unknown entity IDs return HTTP `404`. Database failures return HTTP `503` without
SQL text, stack traces, or connection strings.

## Example curl commands

```bash
# Health
curl -s http://127.0.0.1:8080/health

# All active persons from one camera
curl -s 'http://127.0.0.1:8080/api/v1/entities?status=active&entity_type=person&camera_id=azure_kinect&limit=20'

# Recent entities
curl -s 'http://127.0.0.1:8080/api/v1/entities/recent?limit=10'

# One entity
curl -s "http://127.0.0.1:8080/api/v1/entities/${ENTITY_ID}"

# Observations (newest first)
curl -s "http://127.0.0.1:8080/api/v1/entities/${ENTITY_ID}/observations?sort=desc&limit=50"
```

## Example JSON responses

### `GET /health`

```json
{
  "status": "ok",
  "service": "jarvis-entity-query-api"
}
```

### `GET /api/v1/entities`

```json
{
  "items": [
    {
      "id": "3f1c0e4a-2b8d-4c5a-9f11-0123456789ab",
      "identity_key": "camera:azure_kinect:tracker:1",
      "identity_strategy": "tracker_id",
      "entity_type": "person",
      "label": "person",
      "track_id": 1,
      "camera_id": "azure_kinect",
      "first_seen": "2026-07-27T12:00:00+00:00",
      "last_seen": "2026-07-27T12:00:05+00:00",
      "times_seen": 6,
      "average_confidence": 0.91,
      "status": "active",
      "bounding_box": {"x1": 10, "y1": 20, "x2": 100, "y2": 200}
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### `GET /api/v1/entities/{id}/observations`

```json
{
  "items": [
    {
      "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "entity_id": "3f1c0e4a-2b8d-4c5a-9f11-0123456789ab",
      "observed_at": "2026-07-27T12:00:05+00:00",
      "camera_id": "azure_kinect",
      "confidence": 0.94,
      "label": "person",
      "source_event_type": "vision.object_updated",
      "bounding_box": {"x1": 12, "y1": 22, "x2": 98, "y2": 198},
      "frame_number": 42,
      "track_id": 1
    }
  ],
  "total": 6,
  "limit": 100,
  "offset": 0
}
```

## Architecture

```
HTTP routes (api/entity_routes.py)
  → EntityQueryService
    → EntityRepository / ObservationRepository
      → SQLAlchemy entity-memory tables
```

Routes never issue SQLAlchemy queries directly.
