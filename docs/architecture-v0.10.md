# Architecture notes — v0.10.0 Operational Observability & Data Lifecycle

Additive layer on the existing FastAPI + PostgreSQL + SQLAlchemy edge stack.

```
┌─────────────────────────────────────────────────────────────┐
│ Live Activity Console (/console)                            │
│  timeline · entities · zones · alerts · notifications · ops │
└───────────────┬───────────────────────────────┬─────────────┘
                │ REST / WS                     │ ops poll
                ▼                               ▼
┌──────────────────────────────┐   ┌──────────────────────────┐
│ Entity Query API (FastAPI)   │   │ OpsStatusCollector       │
│ /health /ready /timeline …   │──▶│ metrics (bounded)        │
│ /api/v1/ops/status|retention │   │ component probes         │
└──────────────┬───────────────┘   └────────────┬─────────────┘
               │                                 │
               │         ┌───────────────────────┘
               ▼         ▼
┌──────────────────────────────────────────────────────────────┐
│ Optional workers (asyncio, isolated failures)                │
│  activity LISTEN · alert consumer · due reconciler           │
│  notification outbox worker · retention worker (sleep-first) │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ PostgreSQL (authoritative)                                   │
│  entities · observations · zone sessions · alerts            │
│  evaluator state · checkpoints · notification outbox         │
└──────────────────────────────────────────────────────────────┘
```

## Design constraints

- API process never imports camera / Hailo / OpenCV / NumPy.
- PostgreSQL remains authoritative; SQLite used for unit tests.
- Retention deletes only by explicit ID batches; no unbounded DELETE.
- Checkpoints and active recovery rows are not retention domains.
- Entity deletion is experimental and cascade-hardened (see
  [data-retention.md](data-retention.md)).

## Related docs

- [operational-observability.md](operational-observability.md)
- [data-retention.md](data-retention.md)
- [live-activity-console.md](live-activity-console.md)
- [timeline-provider-architecture.md](timeline-provider-architecture.md)
