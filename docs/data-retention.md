# Data Retention Policy (v0.10.0)

Bounded, configuration-driven cleanup of historical operational data. Defaults
are safe for upgrade: **global off**, **dry-run on**, **every domain off**,
**manual destructive off**.

## Safe upgrade defaults

| Setting | Default | Meaning |
|---------|---------|---------|
| `ops.retention.enabled` | `false` | Global retention off |
| `ops.retention.dry_run` | `true` | Plan-only mode even when enabled |
| `ops.retention.allow_manual_destructive_run` | `false` | Destructive POST blocked |
| Every domain `enabled` | `false` | No domain scheduled for cleanup |
| Keep periods | 30–90 days | Conservative |

**Upgrading to v0.10.0 does not delete any existing data.**

## Configuration schema

```yaml
ops:
  retention:
    enabled: false
    dry_run: true
    allow_manual_destructive_run: false
    interval_seconds: 86400
    batch_size: 250
    max_batches_per_run: 4

    observations:
      enabled: false
      keep_days: 30

    entities:                    # experimental — see cascade section
      enabled: false
      keep_closed_days: 90

    zone_sessions:
      enabled: false
      keep_closed_days: 90

    alerts:
      enabled: false
      keep_resolved_days: 90

    evaluator_state:
      enabled: false
      keep_inactive_days: 30

    notification_deliveries:
      enabled: false
      keep_terminal_days: 90
```

## Domain eligibility

| Domain | Eligible | Never delete |
|--------|----------|--------------|
| **observations** | `observed_at` **strictly before** cutoff | Rows at/after cutoff |
| **entities** (experimental) | **Closed** entities older than cutoff with **no** remaining alerts, evaluator rows, or open zone sessions | Active entities; entities with dependents |
| **zone_sessions** | **Closed** sessions with `exited_at` before cutoff | **Open** sessions |
| **alerts** | **Resolved** alerts with `resolved_at` before cutoff | **Open** / **acknowledged** |
| **evaluator_state** | **Cleared** state older than cutoff | **Pending** / **triggered** |
| **notification_deliveries** | **delivered** / **exhausted** older than cutoff (+ attempts via CASCADE) | **pending**, **processing**, **failed** |
| **checkpoints / recovery** | **Out of scope** | All `alert_evaluator_checkpoints` and related recovery rows |

Cutoffs use exclusive comparison (`timestamp < cutoff`). Boundary-equal rows are retained.

## Entity cascade audit (Phase 7)

Database `ON DELETE CASCADE` from `entities.id`:

| Child table | Foreign key | ON DELETE | Intended with entity retention? | Risk if deleted with entity |
|-------------|-------------|-----------|----------------------------------|-----------------------------|
| `entity_observations` | `entity_id → entities.id` | CASCADE | Yes (history of that entity) | Loses observation history for that entity |
| `entity_snapshots` | `entity_id → entities.id` | CASCADE | Yes | Loses snapshot history |
| `entity_zone_sessions` | `entity_id → entities.id` | CASCADE | Partial | Would wipe closed dwell history if not pruned first |
| `alerts` | `entity_id → entities.id` | CASCADE | **No without prior prune** | Would erase open/acked/resolved audit + deliveries |
| `alert_evaluator_state` | `entity_id → entities.id` | CASCADE | **No without prior prune** | Would erase pending/triggered dwell conditions |
| `notification_deliveries` | via `alerts.id` CASCADE | CASCADE | Indirect | Terminal delivery audit removed with alert |

### Safety policy (v0.10.0)

1. Entity retention remains **default-off** and documented as **experimental**.
2. Eligibility is narrowed: an entity is never selected while any alert or
   evaluator row (any status) or open zone session remains.
3. Prefer enabling `alerts`, `evaluator_state`, and `zone_sessions` domains
   first; the worker runs **entities last**.
4. Operators who enable entity deletion accept CASCADE of remaining
   observations/snapshots (and any closed sessions still present).
5. Broad CASCADE is **not** treated as silent automatic cleanup of alert
   history.

## Execution model

Optional `RetentionWorker` (started only when `ops.retention.enabled=true`):

1. Interval sleep (`interval_seconds`; minimum 60s; **sleep-first** — no fire on start).
2. Each enabled domain runs independently in fixed order (entities last).
3. Per batch: fetch up to `batch_size` eligible IDs → short transaction delete
   (or dry-run skip) → commit.
4. Cap batches with `max_batches_per_run`.
5. Dry-run (`dry_run=true`, default): full path except DELETE.
6. Failures isolated — never stop API, timeline, alerts, or notifications.
7. Shared cycle lock + 30s manual cooldown for POST triggers.

### Checkpoint and recovery safety

Retention code paths **do not** query or delete:

- `alert_evaluator_checkpoints` (consumer catch-up)
- Active evaluator pending/triggered rows (due reconciler / dwell)
- Open zone sessions (occupancy reconciliation)
- Non-terminal notification deliveries (retry)
- Timeline recovery uses event history + cursors; observation/entity prune is
  bounded by keep periods and never targets checkpoint tables.

## Manual APIs

| Method | Path | Behavior |
|--------|------|----------|
| GET | `/api/v1/ops/retention` | Policy + worker + guard state |
| POST | `/api/v1/ops/retention/dry-run` | One forced dry-run cycle (`enabled` required) |
| POST | `/api/v1/ops/retention/run` | One destructive cycle (all guards required) |

Guards for destructive run:

1. `enabled=true`
2. `dry_run=false`
3. `allow_manual_destructive_run=true` (default **false**)
4. ≥1 domain enabled
5. No active cycle (409)
6. 30s process-local cooldown (429)

Request bodies **cannot** override policy, cutoffs, batch sizes, domains, or SQL.

## Console

Live Activity Console **Operations** panel:

- Polls readiness/status/retention (~8s).
- Shows policy, worker state, last-run summary.
- **Run dry-run** when enabled and idle.
- **Run cleanup** only when server reports `destructive_permitted=true`.
- Browser `confirm()` required for cleanup.
- Ops failures isolated from timeline/WebSocket/alerts UI.

See [live-activity-console.md](live-activity-console.md).

## Validation bounds

| Field | Min | Max |
|-------|-----|-----|
| `interval_seconds` | 60 | 604800 (7 days) |
| `batch_size` | 1 | 1000 |
| `max_batches_per_run` | 1 | 100 |
| keep-day fields | 1 | 3650 |

Unsafe combination rejected:

- `enabled=true` **and** `dry_run=false` **and** zero domain policies enabled.

## Environment variables

See [configuration.md](configuration.md).

## Live PostgreSQL validation

Use a **temporary** database owned by `jarvis_app` — never `jarvis_vision`.

```bash
export JARVIS_PG_ADMIN_URL=postgresql://admin:...@127.0.0.1:5432/postgres
export JARVIS_PG_APP_URL=postgresql://jarvis_app:...@127.0.0.1:5432/postgres
python scripts/retention_pg_e2e_demo.py
```

The demo creates a temp DB, runs Alembic to head, seeds all domains, dry-runs
(zero deletes), destructive-runs with guards, verifies protected rows, and
drops the temp DB.

Alternatively set `JARVIS_RETENTION_PG_E2E_URL` to an already-created temp DB
and run `pytest tests/test_retention_pg_e2e.py`.

## Relation to ops status

`GET /api/v1/ops/status` includes an additive `retention` object (policy +
worker + last run). See [operational-observability.md](operational-observability.md).
