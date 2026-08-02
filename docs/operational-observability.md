# Operational Observability (v0.10.0)

Jarvis Edge AI exposes lightweight, camera/Hailo-free operational endpoints so
operators can see whether core edge services are healthy without scraping logs.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness (process up) |
| GET | `/ready` | Readiness (database + core services) |
| GET | `/api/v1/ops/status` | Component statuses + bounded metrics + retention summary |
| GET | `/api/v1/ops/retention` | Retention policy, worker state, guards |
| POST | `/api/v1/ops/retention/dry-run` | One non-destructive cycle |
| POST | `/api/v1/ops/retention/run` | One destructive cycle (multi-guard) |

These routes never import vision, camera, Hailo, OpenCV, or NumPy.

## Component statuses

Each component reports one of:

| Status | Meaning |
|--------|---------|
| `healthy` | Operating normally |
| `degraded` | Running with recent faults or partial capability |
| `unavailable` | Required dependency missing (e.g. database down) |
| `disabled` | Intentionally not configured / off |

Overall status:

| Overall | Rule |
|---------|------|
| `healthy` | Critical components healthy |
| `degraded` | At least one non-critical fault or partial service |
| `unavailable` | Database unavailable (or equivalent hard failure) |

### Components covered

| Component | Notes |
|-----------|-------|
| `database` | Connectivity check |
| `timeline` | Timeline service availability |
| `activity_listener` | PostgreSQL LISTEN worker (disabled / ready / degraded) |
| `alert_consumer` | Durable alert event consumer + checkpoint |
| `due_reconciler` | Sustained-condition due_at reconciler |
| `notification_worker` | Outbox delivery worker |

Retention worker state is exposed under the additive top-level `retention`
object (`execution`, `worker.state`, last-run summary), not as a sibling
component key.

### Due reconciler degradation rule

Historical errors must **not** permanently degrade readiness. The collector
marks the reconciler degraded only when:

1. it is enabled but not running, or
2. `last_error_at` is more recent than `last_success_at` (current-cycle failure), or
3. there has never been a success and `last_error_at` is within a short recent
   window (5 minutes).

After a successful cycle following an error, status returns to `healthy`.

## Metrics

`GET /api/v1/ops/status` includes a **bounded**, allow-listed metrics snapshot:

- Fixed key cardinality caps (no per-entity / per-alert series).
- Counters, gauges, last-success / last-error timestamps, simple latencies.
- No database DSNs, credentials, filesystem paths, raw SQL, or exception
  stacks.

## Retention integration

When configured, the status payload includes an additive `retention` object
(policy summary + worker state + last-run summary). Full detail and manual
controls live under `/api/v1/ops/retention*`.

See [data-retention.md](data-retention.md).

## Console

The Live Activity Console **Operations** panel polls readiness/status/retention
on a slow interval and isolates ops failures from timeline/WebSocket UI.

See [live-activity-console.md](live-activity-console.md).

## Performance targets (edge)

On Raspberry Pi 5 class hardware, under normal load:

| Endpoint | Target |
|----------|--------|
| `/health`, `/ready` | p95 typically under 100–200 ms |
| `/api/v1/ops/status`, `/api/v1/ops/retention` | p95 typically under 100–200 ms |

Use `scripts/ops_latency_bench.py` for a small repeatable local micro-benchmark
(no heavy benchmark dependencies).

## Security assumptions

Ops endpoints and the console assume a **trusted local / edge network**. They
are not a substitute for authentication on untrusted networks.

## Related

- [configuration.md](configuration.md) — env overrides
- [data-retention.md](data-retention.md) — lifecycle policies
- [durable-alerts.md](durable-alerts.md) — alert consumer / reconciler
- [outbound-notifications.md](outbound-notifications.md) — delivery worker
