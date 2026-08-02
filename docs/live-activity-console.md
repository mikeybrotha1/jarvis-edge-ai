# Live Activity Console (v0.5.1 + spatial v0.6.0)

Browser UI for Jarvis entity activity history, live stream, and zones.

**URL:** [http://127.0.0.1:8080/console](http://127.0.0.1:8080/console)

## What it is

A same-origin static console (HTML/CSS/vanilla JS, no build step) that consumes
public APIs:

| Interface | Role in console |
|-----------|-----------------|
| `GET /health` | API reachability |
| `GET /ready` | Readiness (ops panel) |
| `GET /api/v1/ops/status` | Component status + bounded metrics |
| `GET /api/v1/ops/retention` | Retention policy + worker state |
| `POST /api/v1/ops/retention/dry-run` | Manual non-destructive cycle |
| `POST /api/v1/ops/retention/run` | Manual destructive cycle (server-guarded) |
| `GET /api/v1/timeline` | Initial history + Load older + reconnect recovery |
| `GET /api/v1/entities/active` | Active entities panel |
| `GET /api/v1/entities/recent` | Recent entities panel |
| `GET /api/v1/entities/{id}` | Selected entity detail |
| `GET/POST/PATCH /api/v1/zones…` | Zone list, occupancy, sessions, create/update |
| `WS /ws/v1/activity` | Live timeline + spatial events |

Zero build step: no Node.js, no frameworks, no live video, no canvas drawing.

## Startup

```bash
set -a && source .env.jarvis && set +a
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

# Live mode needs the activity stream (LISTEN/NOTIFY) enabled:
export JARVIS_ACTIVITY_STREAM_ENABLED=true

PYTHONPATH=src python -m api
# open http://127.0.0.1:8080/console
```

The API process still does **not** load camera, Hailo, OpenCV, or NumPy.

### Live vs historical-only

| Mode | Requirement |
|------|-------------|
| Historical timeline + entity panels | API + database only |
| Live feed + reconnect recovery | `activity_stream.enabled=true` and working PostgreSQL LISTEN |

If the stream is disabled, the console still loads history over REST and shows a
disconnected/degraded WebSocket status.

## Layout

1. **Status bar** — WebSocket state, stream readiness, last event time, reconnect
   attempts, observation opt-in, mode (`loading` / `live` / `recovering` / …).
2. **Filters** — event types (lifecycle default; observations opt-in), camera,
   entity type, entity ID.
3. **Activity feed** — newest first; badges for `live` / `recovered` /
   `historical`; keyboard listbox navigation.
4. **Entity panel** — active/recent lists + selected entity detail.
5. **Load older** — Timeline cursor pagination with current filters.

## Lifecycle vs observations

- Default filters and WebSocket subscription: `entity_created`, `entity_closed`.
- `observation_recorded` is opt-in in the UI; applying filters updates the
  WebSocket subscription via `subscription.update`.

## Recovery (reconnect)

1. Remember last processed stable event id + `occurred_at`.
2. On disconnect, show reconnecting; WS client uses bounded exponential backoff.
3. On `connection.ready`, enter **recovering**, buffer live events.
4. Query `GET /api/v1/timeline` with `sort=asc`, current filters, and
   `occurred_after = last_occurred_at − 5s` (overlap).
5. Paginate with `next_cursor` until exhausted.
6. Upsert recovered events (`source=recovered`) and buffered live events by
   **stable ID** (authoritative dedupe).
7. Resume live processing.

Overlap is safe because duplicate IDs collapse; no events are invented.

## Security

- Dynamic values use `textContent` / attribute APIs — no unsafe `innerHTML`.
- API/WS payloads treated as untrusted.
- Error strings sanitized; credentials/SQL/DB URLs suppressed in display.
- Malformed WS messages ignored with a visible sanitized warning.

## Bounded memory

- In-memory event Map + ordered list, hard cap **400** (configurable in
  `store.js`).
- DOM feed render cap **200** nodes.
- Oldest events drop first when capped.

## Browser support

Modern evergreen browsers with ES modules + `fetch` + `WebSocket`
(Chromium/Firefox/Safari recent). No bundler required.

## Manual test procedure (Chromium)

1. Start API with `JARVIS_ACTIVITY_STREAM_ENABLED=true` (optional for ops-only).
2. Open `http://127.0.0.1:8080/console`.
3. Confirm status bar and initial history (or empty state).
4. Confirm WebSocket **connected** when stream is ready.
5. Produce a durable entity lifecycle event (vision process or direct DB write
   with NOTIFY path).
6. Confirm the event appears with source **live**.
7. Restart API; confirm reconnect + **recovering** then **live**.
8. Use **Load older** when `next_cursor` exists.
9. Select an event/entity and confirm detail panel.
10. Confirm API process logs never open camera/Hailo.
11. Open **Operations**: readiness/overall badges, component rows, retention
    policy + worker state.
12. With retention enabled + dry-run: **Run dry-run** succeeds; cleanup stays
    disabled under safe defaults; cooldown applies after triggers.
13. Force ops endpoint failure (stop API briefly or block route): ops shows
    stale hint; timeline/WebSocket remain usable when API returns.

## Notifications panel (v0.9.0)

The console includes:

- Notification target list + create/edit/disable form
- Signing-secret set/replace control (secret never displayed after save)
- Rule–target association controls
- Per-alert delivery status and history
- Retry for failed/exhausted deliveries
- Status badges: pending, processing, delivered, failed, exhausted

See [outbound-notifications.md](outbound-notifications.md).

## Operations panel (v0.10.0)

Collapsible **Operations** section in the left sidebar:

| Area | Content |
|------|---------|
| Overall / ready | Text badges: healthy, degraded, unavailable, disabled, ready/not_ready |
| Components | Database, timeline, activity listener, alert consumer, due reconciler, notification worker, retention worker |
| Metrics | Allow-listed gauges only (uptime, queue depth, delivery totals, latencies, retention cycle state) |
| Retention policy | Global enabled, dry-run, manual destructive flag, batch bounds, per-domain periods |
| Last run | Examined / deleted / skipped + per-domain lines |
| Controls | **Run dry-run** (recommended) · **Run cleanup** (guarded) |

### Polling

- Every **8s**: `/ready` + `/api/v1/ops/status` + `/api/v1/ops/retention` when the ops section is open.
- Ops poll failures set a “stale” hint and **do not** clear prior rows or break timeline/WebSocket/alerts UI.

### Manual retention controls

- Dry-run uses server policy only (no user cutoffs/SQL/batch overrides).
- Cleanup is **disabled** under safe defaults (`enabled=false`, `dry_run=true`, `allow_manual_destructive_run=false`).
- Cleanup requires server `destructive_permitted` plus idle cycle and elapsed cooldown; browser `confirm()` states that eligible historical data will be permanently deleted.
- 403/409/422/429/503 messages are sanitized via existing helpers.
- Buttons disable while a request is in flight.

### Local-network security assumption

The console and ops APIs assume a **trusted local / edge network** (same as the rest of the zero-auth API). Do not expose `/console` or retention controls to untrusted networks without additional access control.

See [data-retention.md](data-retention.md).

## Explicit non-goals

- SPA frameworks / Node build toolchain
- New write APIs or auth beyond existing REST
- AI summaries / agents
- Perfect at-least-once live delivery (use Timeline REST for truth)
- Serving the console from a separate origin/server

## Asset layout

```
console/
  index.html
  css/console.css
  js/api.js
  js/ws.js
  js/store.js
  js/recovery.js
  js/ui.js
  js/main.js
  js/ops.js
```

Mounted by FastAPI at `/console` (HTML routes + static assets).
