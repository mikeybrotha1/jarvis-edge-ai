/**
 * Operational view helpers (v0.10.0 phase 6).
 * Pure render / guard logic — no business policy beyond UI enablement rules.
 */

import { sanitizeMessage } from "./api.js";

/** Allow-listed metric rows for the ops panel. */
export const OPS_METRIC_ALLOWLIST = [
  { key: "uptime_seconds", label: "Uptime (s)", path: "metrics.uptime_seconds" },
  {
    key: "alert_consumer_queue_depth",
    label: "Alert queue depth",
    path: "metrics.gauges.alert_consumer_queue_depth",
  },
  {
    key: "alert_consumer_dropped",
    label: "Alert drops",
    path: "metrics.gauges.alert_consumer_dropped",
  },
  {
    key: "notification_pending",
    label: "Notifications pending",
    path: "metrics.gauges.notification_pending",
  },
  {
    key: "notification_processing",
    label: "Notifications processing",
    path: "metrics.gauges.notification_processing",
  },
  {
    key: "notification_delivered_total",
    label: "Delivered total",
    path: "metrics.gauges.notification_delivered_total",
  },
  {
    key: "notification_failed_total",
    label: "Failed total",
    path: "metrics.gauges.notification_failed_total",
  },
  {
    key: "notification_retry_total",
    label: "Retry total",
    path: "metrics.gauges.notification_retry_total",
  },
  {
    key: "notification_exhausted_total",
    label: "Exhausted total",
    path: "metrics.gauges.notification_exhausted_total",
  },
];

const COMPONENT_ORDER = [
  ["database", "Database"],
  ["timeline_composition", "Timeline composition"],
  ["activity_listener", "Activity listener"],
  ["alert_consumer", "Alert consumer"],
  ["due_reconciler", "Due reconciler"],
  ["notification_worker", "Notification worker"],
];

const VALID_STATUSES = new Set([
  "healthy",
  "degraded",
  "unavailable",
  "disabled",
  "ready",
  "not_ready",
  "unknown",
  "idle",
  "running",
]);

/**
 * @param {unknown} value
 */
export function normalizeStatus(value) {
  const s = String(value ?? "unknown").toLowerCase();
  return VALID_STATUSES.has(s) ? s : "unknown";
}

/**
 * @param {object|null|undefined} obj
 * @param {string} path
 */
export function getPath(obj, path) {
  if (!obj) return undefined;
  const parts = path.split(".");
  let cur = obj;
  for (const p of parts) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = cur[p];
  }
  return cur;
}

/**
 * @param {unknown} value
 */
export function formatMetricValue(value) {
  if (value == null || value === "") return "—";
  if (typeof value === "number" && Number.isFinite(value)) {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  return sanitizeMessage(value, 80);
}

/**
 * Destructive cleanup UI may be enabled only when server + local guards allow.
 * @param {object|null|undefined} retention
 */
export function canEnableDestructiveCleanup(retention) {
  if (!retention || typeof retention !== "object") return false;
  if (retention.enabled !== true) return false;
  if (retention.dry_run !== false) return false;
  if (retention.allow_manual_destructive_run !== true) return false;
  if (retention.destructive_permitted !== true) return false;
  if (retention.any_domain_enabled !== true) return false;
  const worker = retention.worker || {};
  if (worker.cycle_active === true) return false;
  if (worker.state === "running") return false;
  const cool = Number(retention.cooldown_remaining_seconds || 0);
  if (cool > 0) return false;
  return true;
}

/**
 * Dry-run allowed when retention enabled and not rate-limited / busy.
 * @param {object|null|undefined} retention
 */
export function canEnableDryRun(retention) {
  if (!retention || typeof retention !== "object") return false;
  if (retention.enabled !== true) return false;
  const worker = retention.worker || {};
  if (worker.cycle_active === true) return false;
  if (worker.state === "running") return false;
  const cool = Number(retention.cooldown_remaining_seconds || 0);
  if (cool > 0) return false;
  return true;
}

/**
 * @param {HTMLElement|null} listEl
 * @param {object|null|undefined} opsStatus
 * @param {object|null|undefined} retention
 */
export function renderOpsComponents(listEl, opsStatus, retention) {
  if (!listEl) return;
  while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
  const components =
    opsStatus && opsStatus.components && typeof opsStatus.components === "object"
      ? opsStatus.components
      : {};

  for (const [key, label] of COMPONENT_ORDER) {
    const body = components[key] || {};
    listEl.appendChild(
      componentItem(label, normalizeStatus(body.status), body.detail)
    );
  }

  // Retention worker as separate row
  const exec =
    retention && retention.worker && retention.worker.state
      ? retention.worker.state
      : retention && retention.execution
        ? retention.execution
        : "unknown";
  const retStatus =
    exec === "disabled" || exec === "not_configured"
      ? "disabled"
      : exec === "running"
        ? "degraded"
        : exec === "idle"
          ? "healthy"
          : normalizeStatus(exec);
  listEl.appendChild(
    componentItem(
      "Retention worker",
      retStatus,
      retention && retention.note ? String(retention.note) : undefined
    )
  );
}

/**
 * @param {string} label
 * @param {string} status
 * @param {string|undefined} detail
 */
function componentItem(label, status, detail) {
  const li = document.createElement("li");
  li.className = "ops-component-item";
  li.dataset.status = status;

  const name = document.createElement("span");
  name.className = "ops-component-name";
  name.textContent = label;

  const badge = document.createElement("span");
  badge.className = "ops-badge";
  badge.dataset.status = status;
  badge.textContent = status;
  badge.setAttribute("aria-label", `status ${status}`);

  li.appendChild(name);
  li.appendChild(badge);

  if (detail) {
    const d = document.createElement("span");
    d.className = "ops-component-detail";
    d.textContent = sanitizeMessage(detail, 120);
    li.appendChild(d);
  }
  return li;
}

/**
 * @param {HTMLElement|null} dl
 * @param {object|null|undefined} opsStatus
 * @param {object|null|undefined} retention
 */
export function renderOpsMetrics(dl, opsStatus, retention) {
  if (!dl) return;
  while (dl.firstChild) dl.removeChild(dl.firstChild);

  for (const row of OPS_METRIC_ALLOWLIST) {
    const value = getPath(opsStatus, row.path);
    appendMetric(dl, row.label, formatMetricValue(value));
  }

  const lastSuccess = getPath(opsStatus, "metrics.last_success_at");
  if (lastSuccess && typeof lastSuccess === "object") {
    const keys = Object.keys(lastSuccess).slice(0, 6);
    for (const k of keys) {
      appendMetric(dl, `Last success · ${k}`, formatMetricValue(lastSuccess[k]));
    }
  }
  const lastError = getPath(opsStatus, "metrics.last_error_at");
  if (lastError && typeof lastError === "object") {
    const keys = Object.keys(lastError).slice(0, 4);
    for (const k of keys) {
      appendMetric(dl, `Last error · ${k}`, formatMetricValue(lastError[k]));
    }
  }

  const lat = getPath(opsStatus, "metrics.latencies_ms.notification_delivery");
  if (lat && typeof lat === "object") {
    appendMetric(dl, "Delivery latency last (ms)", formatMetricValue(lat.last_ms));
    appendMetric(dl, "Delivery latency avg (ms)", formatMetricValue(lat.average_ms));
  }

  const worker = retention && retention.worker ? retention.worker : {};
  appendMetric(dl, "Retention cycle state", formatMetricValue(worker.state || "—"));
  appendMetric(
    dl,
    "Retention cycle active",
    worker.cycle_active === true ? "yes" : "no"
  );
}

/**
 * @param {HTMLElement|null} dl
 * @param {object|null|undefined} retention
 */
export function renderRetentionPolicy(dl, retention) {
  if (!dl) return;
  while (dl.firstChild) dl.removeChild(dl.firstChild);
  if (!retention) {
    appendMetric(dl, "Status", "unavailable");
    return;
  }
  appendMetric(dl, "Global enabled", retention.enabled === true ? "yes" : "no");
  appendMetric(dl, "Dry-run mode", retention.dry_run === false ? "no" : "yes");
  appendMetric(
    dl,
    "Manual destructive permitted",
    retention.destructive_permitted === true ? "yes" : "no"
  );
  appendMetric(
    dl,
    "allow_manual_destructive_run",
    retention.allow_manual_destructive_run === true ? "yes" : "no"
  );
  appendMetric(dl, "Batch size", formatMetricValue(retention.batch_size));
  appendMetric(
    dl,
    "Max batches / run",
    formatMetricValue(retention.max_batches_per_run)
  );
  appendMetric(dl, "Interval (s)", formatMetricValue(retention.interval_seconds));
  appendMetric(
    dl,
    "Cooldownive cooldown remaining (s)",
    formatMetricValue(retention.cooldown_remaining_seconds)
  );

  const domains = retention.domains || {};
  const domainKeys = [
    ["observations", "keep_days"],
    ["entities", "keep_closed_days"],
    ["zone_sessions", "keep_closed_days"],
    ["alerts", "keep_resolved_days"],
    ["evaluator_state", "keep_inactive_days"],
    ["notification_deliveries", "keep_terminal_days"],
  ];
  for (const [name, keepKey] of domainKeys) {
    const d = domains[name] || {};
    const en = d.enabled === true ? "on" : "off";
    const keep = d[keepKey] != null ? d[keepKey] : "—";
    appendMetric(dl, `Domain ${name}`, `${en} · ${keepKey}=${keep}`);
  }
}

/**
 * @param {HTMLElement|null} el
 * @param {object|null|undefined} retention
 */
export function renderRetentionLastRun(el, retention) {
  if (!el) return;
  const last =
    retention && retention.worker && retention.worker.last_run
      ? retention.worker.last_run
      : null;
  if (!last) {
    el.textContent = "No run yet.";
    return;
  }
  const lines = [
    `status=${sanitizeMessage(last.status, 40)} dry_run=${last.dry_run === true}`,
    `examined=${formatMetricValue(last.rows_examined)} deleted=${formatMetricValue(last.rows_deleted)} skipped=${formatMetricValue(last.rows_skipped)}`,
    `duration_ms=${formatMetricValue(last.duration_ms)}`,
    `started=${formatMetricValue(last.started_at)}`,
  ];
  if (Array.isArray(last.domains)) {
    for (const d of last.domains.slice(0, 8)) {
      lines.push(
        `· ${sanitizeMessage(d.domain, 40)}: exam=${formatMetricValue(d.rows_examined)} del=${formatMetricValue(d.rows_deleted)} skip=${formatMetricValue(d.rows_skipped)}`
      );
    }
  }
  el.textContent = lines.join("\n");
}

/**
 * @param {HTMLElement|null} dl
 * @param {string} label
 * @param {string} value
 */
function appendMetric(dl, label, value) {
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value;
  dl.appendChild(dt);
  dl.appendChild(dd);
}

/**
 * Destructive confirmation copy.
 */
export const DESTRUCTIVE_CONFIRM_TEXT =
  "This will permanently delete eligible historical data according to the server retention policy. Open alerts, active entities, open sessions, and non-terminal deliveries are not deleted. Continue?";
