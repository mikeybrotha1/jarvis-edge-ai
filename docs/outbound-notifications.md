# Outbound Notification Delivery (v0.9.0)

Webhook-only notification delivery built on durable alerts (v0.8.0).
Deliveries are at-least-once, failure-isolated from alert durability, and
driven by a dedicated worker with SSRF protection.

## Architecture

```
Same DB transaction:
  select matching targets
  insert/update alert state
  insert deterministic notification_deliveries (outbox)
  register alert pg_notify
  commit atomically
After commit:
  NotificationDeliveryWorker claims rows (FOR UPDATE SKIP LOCKED on PG)
  HTTP outside any DB transaction
  record attempt + status (delivered | failed+retry | exhausted)
```

### Frozen transaction boundary (transactional outbox)

- **Local outbox persistence is part of durable alert bookkeeping.** Alert
  state change and all matching `notification_deliveries` rows commit in the
  **same** database transaction.
- Select matching enabled targets before/during that transaction; insert one
  deterministic delivery per target (de-duplicated).
- **No outbound HTTP** runs inside entity, spatial, alert-evaluation, or
  alert-state transactions.
- Outbox **database** insert failures must **not** be swallowed: the alert
  transaction rolls back so an alert is never committed without its required
  durable delivery work (when targets match).
- After commit, **external delivery is fully isolated**: network failures,
  retries, and exhaustion never modify or roll back alert state.

### Components

| Layer | Responsibility |
|-------|----------------|
| `notification_targets` | Webhook target configuration |
| `rule_notification_targets` | Rule ↔ target M:N associations |
| `notification_deliveries` | Logical outbox row per alert+target+event |
| `notification_delivery_attempts` | Per-network-attempt history |
| `NotificationProvider` | Channel interface (no DB sessions) |
| `WebhookNotificationProvider` | HTTP POST implementation |
| `NotificationDeliveryWorker` | Claim / deliver / record cycle |

## Global vs per-rule targets

- **Global** (`is_global=true`): all alerts whose severity is listed in
  `severity_filters` (empty filters = all severities).
- **Per-rule**: enabled rows in `rule_notification_targets`.
- Both paths are unioned and **de-duplicated by target id**.

## Delivery semantics

- **At-least-once**: retries and restarts may redeliver the same event.
- Receivers should dedupe on `X-Jarvis-Delivery-ID` /
  `idempotency_key` = `{alert_id}:{target_id}:{event_type}`.
- Retries reuse the same idempotency key and header.
- Status model:
  - `pending` — ready for claim
  - `processing` — claimed by a worker
  - `delivered` — terminal success
  - `failed` — retry scheduled (`next_attempt_at` set)
  - `exhausted` — terminal after max attempts or non-retryable error

## Webhook payload (schema_version 1)

```json
{
  "schema_version": "1",
  "delivery_id": "<uuid>",
  "event_type": "alert_triggered",
  "occurred_at": "2026-08-02T12:00:00+00:00",
  "alert": {
    "id": "...",
    "rule_id": "...",
    "status": "open",
    "severity": "warning",
    "entity_id": "...",
    "zone_id": null,
    "camera_id": "azure_kinect",
    "summary": "...",
    "payload": {},
    "triggered_at": "...",
    "resolved_at": null,
    "acknowledged_at": null,
    "subject_key": "...",
    "source_event_id": "..."
  }
}
```

`alert_resolved` includes resolved timestamps and status `resolved`.

## Headers and signing

Always sent:

- `Content-Type: application/json`
- `User-Agent: JarvisEdgeAI-NotificationWorker/0.9.0`
- `X-Jarvis-Delivery-ID: <idempotency_key>`

When a signing secret is configured:

- `X-Jarvis-Timestamp: <unix seconds>`
- `X-Jarvis-Signature: sha256=<hex>`

Signature input: UTF-8 `{timestamp}.` + exact request body bytes,
HMAC-SHA256 with the shared secret.

Verification (receiver):

```python
import hmac, hashlib
msg = f"{timestamp}.".encode() + body
assert hmac.compare_digest(
    header.removeprefix("sha256="),
    hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest(),
)
```

## Secret handling

- Secrets are encrypted at rest with Fernet using
  `JARVIS_NOTIFICATIONS_ENCRYPTION_KEY`.
- API responses expose only `has_signing_secret: true|false`.
- Raw secrets are never returned after create/update.
- Without the encryption key, setting a signing secret is rejected (422).

Generate a key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
export JARVIS_NOTIFICATIONS_ENCRYPTION_KEY=...
```

## SSRF controls

Before every request and on target create/update:

- schemes: `http` / `https` only
- no embedded credentials
- **loopback always blocked** (`127.0.0.1`, `::1`, `localhost` and aliases) —
  independent of configuration
- link-local always blocked (incl. cloud metadata `169.254.169.254`)
- multicast / unspecified always blocked
- metadata hostnames always blocked
- **private RFC1918 / ULA** blocked by default; permitted only when
  `allow_private_targets=true` (lab / on-LAN smoke: bind receiver to `0.0.0.0`
  and target the host **LAN** address, not loopback)
- DNS resolve + address policy (best-effort rebinding resistance)
- redirects disabled (`follow_redirects=False`)

`NotificationsConfig.allow_private_targets` is passed to both target
create/update validation and delivery-time `WebhookNotificationProvider`.
It does **not** open loopback.

## Retry / backoff

- Success: HTTP 2xx
- Retryable: 408, 425, 429, 5xx, timeouts, connection errors
- Terminal: most other 4xx, SSRF block, missing target, decrypt failure
- Backoff: `min(initial * multiplier^(attempt-1), max_backoff)`
- Defaults: initial 30s, multiplier 2.0, max 1800s, max_attempts 5
- Manual retry: `POST /api/v1/notification-deliveries/{id}/retry`
  (failed/exhausted only; idempotent re-queue)

## Worker recovery

- Stale `processing` locks older than `lock_timeout_seconds` are returned
  to `pending`.
- Pending/failed due rows are claimed after restart.
- Concurrency bounded by `max_concurrent_deliveries` and `batch_size`.

## REST API

| Method | Path |
|--------|------|
| GET/POST | `/api/v1/notification-targets` |
| GET/PATCH | `/api/v1/notification-targets/{id}` |
| GET/POST/DELETE | `/api/v1/alert-rules/{rule_id}/notification-targets[/{target_id}]` |
| GET | `/api/v1/notification-deliveries` |
| GET | `/api/v1/notification-deliveries/{id}` |
| GET | `/api/v1/notification-deliveries/{id}/attempts` |
| POST | `/api/v1/notification-deliveries/{id}/retry` |
| GET | `/api/v1/alerts/{alert_id}/deliveries` |

## Configuration

```yaml
notifications:
  enabled: true
  worker_poll_interval_seconds: 1
  max_attempts: 5
  initial_backoff_seconds: 30
  max_backoff_seconds: 1800
  backoff_multiplier: 2.0
  request_timeout_seconds: 5
  max_concurrent_deliveries: 3
  batch_size: 50
  lock_timeout_seconds: 60
  max_request_bytes: 65536
  max_response_bytes: 8192
  allow_private_targets: false
  retention_days: 30
  worker_id: jarvis-notification-worker
```

Environment overrides: `JARVIS_NOTIFICATIONS_*` (see `docs/configuration.md`).

## Performance limits

- Bounded request/response body sizes
- Bounded worker concurrency and batch size
- Bounded exponential backoff
- Attempt response bodies truncated to 512 chars in history

## Timeline / WebSocket

v0.9.0 does **not** emit delivery-status timeline events by default.
Queryable delivery history via REST is sufficient. Optional
`alert_notification_sent` is deferred.

## Non-goals (v0.9.0)

- Email, SMS, push, Slack/Discord/Teams, MQTT
- Escalation policies, OAuth, user accounts
- Exactly-once external delivery
- Natural-language configuration / agents / automated response actions
- Second message broker
- Recursive alert or notification creation from deliveries

## Migration

- Alembic: `20260802_0005`
- SQL: `migrations/006_outbound_notifications.sql`
