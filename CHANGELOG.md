# Changelog

## [0.10.0] — Operational Observability & Data Lifecycle

### Added

- Ops status collector and metrics registry (`/api/v1/ops/status`) with
  component health: database, timeline, activity listener, alert consumer,
  due reconciler, notification worker, retention worker.
- Retention configuration under `ops.retention` (typed YAML + env overrides).
- Bounded retention engine and optional background worker (sleep-first,
  batch/transaction limits, dry-run default, isolated failures).
- Manual retention APIs: `GET/POST /api/v1/ops/retention` (dry-run and
  multi-guard destructive run).
- Live Activity Console **Operations** panel (readiness badges, components,
  retention policy/worker, dry-run control, guarded cleanup + browser confirm).
- Documentation: operational observability, data retention (including entity
  cascade audit), console ops section, configuration env vars.
- Scripts: `scripts/ops_latency_bench.py`, `scripts/retention_pg_e2e_demo.py`.
- Live PostgreSQL retention e2e test (opt-in via temp DB URL).

### Safety

- Global retention and all domains default **off**; dry-run default **on**;
  manual destructive default **off**.
- Entity retention is **experimental**: eligibility requires no residual
  alerts, evaluator state, or open zone sessions (avoids silent CASCADE wipe
  of audit/recovery data).
- Checkpoints and non-terminal deliveries are never retention targets.
- Due reconciler readiness uses a **recent-error / last-success** rule so
  historical errors do not permanently degrade status.
- Ops payloads omit DSNs, credentials, Fernet keys, filesystem paths, raw SQL,
  and raw exception stacks.

### Compatibility

- Preserves v0.4–v0.9 API contracts.
- Additive ops/retention routes and console panel only.
- API process remains free of camera/Hailo imports.

## Prior releases (summary)

- **v0.9.0** — Outbound webhook notifications (transactional outbox, SSRF policy).
- **v0.8.0** — Durable alerts, consumer checkpoints, due reconciler.
- **v0.6.0** — Spatial zones and entity-zone sessions.
- **v0.5.x** — Live activity console and activity stream.
- **v0.4.x** — Entity memory and timeline foundation.
