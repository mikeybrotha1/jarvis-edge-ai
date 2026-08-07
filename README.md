# Jarvis Edge AI

![Status](https://img.shields.io/badge/status-v1.0.0-brightgreen)
![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi%205-red)
![Accelerator](https://img.shields.io/badge/AI-Hailo--10H-blue)

Edge-AI platform for Raspberry Pi 5 with optional Hailo acceleration, durable
entity memory on PostgreSQL, REST/timeline APIs, spatial zones, durable alerts,
outbound webhooks, and operational data lifecycle controls.

## What works today (v1.0.0)

| Area | Capability |
|------|------------|
| Vision (optional process) | On-device object detection via Hailo (separate from API process) |
| Entity memory | Durable entities, observations, snapshots (PostgreSQL + Alembic) |
| Timeline API | Paginated historical activity with recovery-friendly cursors |
| Activity stream | Optional PostgreSQL LISTEN/NOTIFY + WebSocket live feed |
| Spatial intelligence | Camera zones, dwell sessions, occupancy |
| Durable alerts | Rules, evaluator state, consumer checkpoints, due reconciler |
| Outbound notifications | Webhook outbox, SSRF-safe delivery, attempts, retries |
| Ops observability | `/health`, `/ready`, `/api/v1/ops/status`, bounded metrics |
| Data retention | Config + optional worker; dry-run default; multi-guard manual run |
| Live Activity Console | Zero-build browser UI: timeline, entities, zones, alerts, notifications, **Operations** panel |

The API process does **not** load camera, Hailo, OpenCV, or NumPy. Vision remains
a separate edge process when used.

## Hardware (typical)

- Raspberry Pi 5
- Raspberry Pi AI HAT+ 2 / Hailo-10H (for vision acceleration)
- PostgreSQL (authoritative store)

## Quick start (API + console, no camera)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure database URL (see config/jarvis.example.yaml)
export PYTHONPATH=src
export JARVIS_DATABASE_URL=postgresql://jarvis_app:***@127.0.0.1:5432/jarvis_vision
alembic upgrade head

PYTHONPATH=src python -m api
# Console: http://127.0.0.1:8080/console
```

## Configuration

Typed YAML + environment overrides. See [docs/configuration.md](docs/configuration.md).

Retention and ops defaults are safe:

- `ops.retention.enabled=false`
- `ops.retention.dry_run=true`
- `ops.retention.allow_manual_destructive_run=false`
- every retention domain `enabled=false`

## Documentation

| Doc | Topic |
|-----|-------|
| [docs/operational-observability.md](docs/operational-observability.md) | Health, ready, ops status, metrics |
| [docs/data-retention.md](docs/data-retention.md) | Retention policy, cascade audit, guards |
| [docs/live-activity-console.md](docs/live-activity-console.md) | Browser console + Operations panel |
| [docs/configuration.md](docs/configuration.md) | Config + env vars |
| [docs/timeline-api.md](docs/timeline-api.md) | Timeline REST |
| [docs/durable-alerts.md](docs/durable-alerts.md) | Alerts |
| [docs/outbound-notifications.md](docs/outbound-notifications.md) | Webhooks |
| [docs/spatial-intelligence.md](docs/spatial-intelligence.md) | Zones / sessions |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |

## Ops & retention tooling

```bash
# Endpoint latency micro-benchmark (local TestClient or live base URL)
PYTHONPATH=src python scripts/ops_latency_bench.py

# Live PostgreSQL retention e2e on a temporary database (never jarvis_vision)
export JARVIS_PG_ADMIN_URL=postgresql://admin:...@127.0.0.1:5432/postgres
export JARVIS_PG_APP_URL=postgresql://jarvis_app:...@127.0.0.1:5432/postgres
python scripts/retention_pg_e2e_demo.py
```

## Roadmap

### Completed product stages

- [x] **v0.4.x** — Entity memory, timeline foundation
- [x] **v0.5.x** — Live activity console + activity stream
- [x] **v0.6.0** — Spatial intelligence (zones, dwell)
- [x] **v0.8.0** — Durable alerts
- [x] **v0.9.0** — Outbound webhook notifications (transactional outbox)
- [x] **v0.10.0** — Operational observability & data lifecycle

### Next

- [ ] **v1.0.0** — Major release after merge of v0.10.0, stabilization, and
      architect review (auth/hardening, install/deploy polish, production ops
      runbooks as needed)

### Longer-term (not claimed as shipping)

- Deeper home/Tesla/Seedo integrations
- Local LLM assistant workflows
- Voice interaction

## Security notes

- Do not commit real DSNs, Fernet keys, or webhook signing secrets.
- Ops and retention APIs assume a trusted local/edge network (no built-in auth).
- Retention never accepts arbitrary SQL or cleanup overrides from the browser.
- Entity retention is **experimental** and cascade-hardened; see
  [docs/data-retention.md](docs/data-retention.md).

## Author

Michael Inzinna · 2026
