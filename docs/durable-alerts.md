# Durable Alerts & Rule Evaluation (v0.8.0)

Complete alerting capability on the released entity, spatial, timeline provider,
LISTEN/NOTIFY, WebSocket, REST, and console foundation.

## Architecture

Vision process commits entity/spatial writes and source `pg_notify` only.

API alert subsystem (separate transactions):

1. Resolve committed `TimelineEvent` (LISTEN fan-out or checkpoint replay)
2. Evaluate enabled rules
3. Write alerts / evaluator state
4. Register alert `pg_notify` in the same alert transaction
5. Commit

Alert failures never roll back core vision persistence.

## Rule types (flat AND filters)

| Type | Required | Behavior |
|------|----------|----------|
| `event_match` | `source_event_types` | Trigger on matching source event |
| `occupancy_threshold` | `occupancy_threshold`, `zone_ids` | Entity-scoped. Optional `occupancy_duration_seconds` (null/0 = immediate). When duration &gt; 0: occupancy ≥ threshold creates pending state with `due_at`; drop below clears pending; still above at due triggers; later drop auto-resolves |
| `dwell_threshold` | `dwell_threshold_seconds`, `zone_ids` | Pending `due_at` on zone enter; trigger if still inside; clear on exit |

### Sustained occupancy

```
occupancy reaches threshold
  → if occupancy_duration_seconds is null or 0: trigger immediately
  → else: pending evaluator state, due_at = condition_started_at + duration
occupancy drops before due → clear pending (no alert)
still ≥ threshold at due → trigger alert
later drops below threshold → auto-resolve open alert
```

## Entity-scope limitation

`TimelineEvent.entity_id` is required. v0.8.0 alerts are always entity-linked.
Zone-only aggregate alerts without an entity are deferred.

## Lifecycle

`open` → `acknowledged` → `resolved`

- Acknowledgment is **state-only** (no timeline event)
- Occupancy/dwell auto-resolve when condition clears
- Manual resolve always available
- Disabling a rule stops new triggers only

## Timeline events

| Type | Stable ID |
|------|-----------|
| `alert_triggered` | `alert-triggered:{alert_id}` |
| `alert_resolved` | `alert-resolved:{alert_id}` |

Included in default timeline/WebSocket lifecycle sets. `observation_recorded` remains opt-in.

## Checkpoint recovery

Table `alert_evaluator_checkpoint` stores `(last_occurred_at, last_event_id)`.
On startup/reconnect: Timeline replay with overlap, idempotent evaluate, advance checkpoint after commit, then live fan-out.

## REST

- `GET/POST /api/v1/alert-rules`, `GET/PATCH /api/v1/alert-rules/{id}`
- `GET /api/v1/alerts`, `GET /api/v1/alerts/{id}`
- `POST .../acknowledge`, `POST .../resolve`

## Config

```yaml
alerts:
  enabled: true
  consumer_name: jarvis-alert-evaluator
  queue_size: 500
  reconcile_interval_seconds: 2
  ...
```

Env: `JARVIS_ALERTS_*`.

## Non-goals

No NL rules, scripts, expressions, agents, embeddings, outbound integrations,
or system-only alerts without `entity_id`.
