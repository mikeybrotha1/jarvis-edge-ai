# Real-time Activity Stream (v0.5.0)

Best-effort, read-only WebSocket stream of the same **TimelineEvent**
projections exposed by the v0.4.2 Timeline REST API.

## Architecture and process boundaries

```
Vision / EntityMemoryService process
  └─ durable write (entities / entity_observations)
     + SELECT pg_notify(channel, minimal_payload)   # same SQLAlchemy transaction
     └─ COMMIT  →  PostgreSQL delivers NOTIFY

API process (no camera / Hailo)
  └─ one async LISTEN connection
     └─ resolve TimelineEvent once via TimelineService
        └─ ActivityStreamBroker
           └─ per-WebSocket bounded queues
```

- PostgreSQL tables remain the durable source of truth.
- LISTEN/NOTIFY is ephemeral live delivery only.
- No `timeline_events` table, queue table, or durable stream store.

## PostgreSQL LISTEN/NOTIFY

Channel default: `jarvis_activity` (`activity_stream.notify_channel`).

Inside the **same** SQLAlchemy transaction that writes a durable row:

1. `INSERT` / `UPDATE`
2. `SELECT pg_notify(:channel, :payload)`
3. `commit`

Rollback ⇒ no notification.

### Minimal NOTIFY payload

```json
{
  "event_id": "entity-created:…",
  "event_type": "entity_created",
  "occurred_at": "2026-07-28T15:00:00Z"
}
```

Full `TimelineEvent` bodies are **not** sent through PostgreSQL.

### Event types

| Type | Stable id |
|------|-----------|
| `entity_created` | `entity-created:{entity_id}` |
| `entity_closed` | `entity-closed:{entity_id}` |
| `observation_recorded` | `observation:{observation_id}` |

Observation NOTIFY is **disabled by default** and per-entity throttled when
enabled (`observation_notifications_enabled`, `observation_min_interval_seconds`).

## WebSocket

```
WS /ws/v1/activity
```

API starts without camera, OpenCV, NumPy, Kinect, Hailo, or the vision pipeline.

### Protocol

**Server → client on connect**

```json
{
  "type": "connection.ready",
  "protocol_version": "1",
  "stream_version": "0.5.0",
  "connected_at": "…",
  "subscription": {
    "event_types": ["entity_created", "entity_closed"]
  }
}
```

Default subscription is lifecycle-only (no observations).

**Client → server subscription update**

```json
{
  "type": "subscription.update",
  "filters": {
    "event_types": ["entity_created", "observation_recorded"],
    "camera_ids": ["front-door"],
    "entity_ids": [],
    "entity_types": ["person"]
  }
}
```

Server responds with `subscription.updated`.

**Timeline events**

```json
{
  "type": "timeline.event",
  "event": { /* exact v0.4.2 TimelineEvent serialization */ }
}
```

**Heartbeat**

```json
{ "type": "heartbeat", "sent_at": "…" }
```

Optional client ack: `{ "type": "heartbeat.ack" }` (not required).

### Filters

- Categories combined with **AND**
- Values within a category combined with **OR**
- Applied after TimelineEvent resolution, per client

### Back-pressure

- Per-client queue capacity: `client_queue_size` (default 100)
- On overflow: drop oldest `observation_recorded` first; preserve lifecycle
- May emit `stream.warning` with `events_dropped`
- If still full: close client as slow consumer (**4002**)

### Close codes

| Code | Meaning |
|------|---------|
| 1001 | Server shutdown |
| 1011 | Listener / internal failure / stream not ready |
| 4001 | Protocol / subscription / max connections |
| 4002 | Slow consumer queue exhaustion |

## Recovery

The WebSocket is **non-durable**. Clients recover gaps via the v0.4.2 Timeline
REST API using stable event IDs. Live ordering is PostgreSQL delivery order;
canonical chronology is the Timeline API.

## Configuration

```yaml
activity_stream:
  enabled: true
  notify_channel: jarvis_activity
  observation_notifications_enabled: false
  observation_min_interval_seconds: 1.0
  client_queue_size: 100
  heartbeat_interval_seconds: 20.0
  max_connections: 25
  reconnect_initial_seconds: 1.0
  reconnect_max_seconds: 30.0
```

Environment overrides use `JARVIS_ACTIVITY_STREAM_*` (see `docs/configuration.md`).

## Example client (Python)

```python
from websockets.sync.client import connect
import json

with connect("ws://127.0.0.1:8080/ws/v1/activity") as ws:
    print(json.loads(ws.recv()))  # connection.ready
    ws.send(json.dumps({
        "type": "subscription.update",
        "filters": {"event_types": ["entity_created", "entity_closed"]},
    }))
    while True:
        print(json.loads(ws.recv()))
```

## Explicit non-goals

- Historical replay over WebSocket
- Durable queues / retained messages
- New timeline persistence tables
- LLM summaries, agents, semantic search
- Face matching / dashboard UI
- Auth redesign
- Camera/Hailo init in the API process
