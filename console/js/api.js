/**
 * Same-origin REST helpers for the Jarvis public API.
 * All dynamic values are treated as untrusted; query params are encoded.
 */

/**
 * @typedef {object} ApiError
 * @property {string} message
 * @property {number|null} status
 * @property {string} code
 */

/**
 * @param {Response} response
 * @returns {Promise<never>}
 */
async function rejectResponse(response) {
  let detail = `Request failed (${response.status})`;
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") {
      detail = sanitizeMessage(body.detail);
    } else if (body && Array.isArray(body.detail)) {
      detail = "Validation error";
    }
  } catch {
    // ignore non-JSON error bodies
  }
  /** @type {ApiError} */
  const err = {
    message: detail,
    status: response.status,
    code: "http_error",
  };
  throw err;
}

/**
 * Strip control characters and truncate for UI display.
 * @param {unknown} value
 * @param {number} [max]
 */
export function sanitizeMessage(value, max = 240) {
  const text = String(value ?? "")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!text) return "Unexpected error";
  // Never surface credential-like material.
  if (/password|secret|postgresql:\/\//i.test(text)) {
    return "Request failed";
  }
  return text.length > max ? text.slice(0, max - 1) + "…" : text;
}

/**
 * @param {string} path
 * @param {Record<string, string|number|boolean|string[]|null|undefined>} [params]
 */
export function buildUrl(path, params = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params)) {
    if (value == null || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item == null || item === "") continue;
        url.searchParams.append(key, String(item));
      }
    } else if (typeof value === "boolean") {
      url.searchParams.set(key, value ? "true" : "false");
    } else {
      url.searchParams.set(key, String(value));
    }
  }
  return url.pathname + url.search;
}

/**
 * @param {string} path
 * @param {Record<string, string|number|boolean|string[]|null|undefined>} [params]
 */
export async function apiGet(path, params = {}) {
  const target = buildUrl(path, params);
  let response;
  try {
    response = await fetch(target, {
      method: "GET",
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
  } catch {
    /** @type {ApiError} */
    const err = {
      message: "Network error contacting API",
      status: null,
      code: "network_error",
    };
    throw err;
  }
  if (!response.ok) {
    await rejectResponse(response);
  }
  return response.json();
}

export function getHealth() {
  return apiGet("/health");
}

export function getReady() {
  return apiGet("/ready");
}

export function getOpsStatus() {
  return apiGet("/api/v1/ops/status");
}

export function getOpsRetention() {
  return apiGet("/api/v1/ops/retention");
}

/** Non-destructive bounded retention cycle. */
export function postRetentionDryRun() {
  return apiSend("/api/v1/ops/retention/dry-run", "POST", {});
}

/**
 * Destructive bounded retention cycle (server-guarded).
 * Callers must confirm in the UI first.
 */
export function postRetentionRun() {
  return apiSend("/api/v1/ops/retention/run", "POST", {});
}

/**
 * @param {object} filters
 */
export function getTimeline(filters = {}) {
  const params = {
    limit: filters.limit ?? 50,
    sort: filters.sort ?? "desc",
    cursor: filters.cursor ?? undefined,
    camera_id: filters.camera_id || undefined,
    entity_type: filters.entity_type || undefined,
    entity_id: filters.entity_id || undefined,
    zone_id: filters.zone_id || undefined,
    occurred_after: filters.occurred_after || undefined,
    occurred_before: filters.occurred_before || undefined,
    event_type: filters.event_types || undefined,
  };
  return apiGet("/api/v1/timeline", params);
}

/**
 * @param {object} [filters]
 */
export function getZones(filters = {}) {
  return apiGet("/api/v1/zones", {
    camera_id: filters.camera_id || undefined,
    enabled: filters.enabled,
    limit: filters.limit ?? 50,
    offset: filters.offset ?? 0,
    sort: filters.sort ?? "asc",
  });
}

/**
 * @param {string} zoneId
 */
export function getZone(zoneId) {
  return apiGet(`/api/v1/zones/${encodeURIComponent(zoneId)}`);
}

/**
 * @param {string} zoneId
 */
export function getZoneOccupancy(zoneId) {
  return apiGet(`/api/v1/zones/${encodeURIComponent(zoneId)}/occupancy`);
}

/**
 * @param {string} zoneId
 * @param {object} [filters]
 */
export function getZoneSessions(zoneId, filters = {}) {
  return apiGet(`/api/v1/zones/${encodeURIComponent(zoneId)}/sessions`, {
    limit: filters.limit ?? 20,
    sort: filters.sort ?? "desc",
    status: filters.status || undefined,
  });
}

/**
 * @param {object} body
 */
export async function createZone(body) {
  return apiSend("/api/v1/zones", "POST", body);
}

/**
 * @param {string} zoneId
 * @param {object} body
 */
export async function patchZone(zoneId, body) {
  return apiSend(`/api/v1/zones/${encodeURIComponent(zoneId)}`, "PATCH", body);
}

export function getAlertRules(filters = {}) {
  return apiGet("/api/v1/alert-rules", {
    enabled: filters.enabled,
    limit: filters.limit ?? 50,
    offset: filters.offset ?? 0,
  });
}

export function createAlertRule(body) {
  return apiSend("/api/v1/alert-rules", "POST", body);
}

export function patchAlertRule(ruleId, body) {
  return apiSend(
    `/api/v1/alert-rules/${encodeURIComponent(ruleId)}`,
    "PATCH",
    body
  );
}

export function getAlerts(filters = {}) {
  return apiGet("/api/v1/alerts", {
    status: filters.status || undefined,
    severity: filters.severity || undefined,
    limit: filters.limit ?? 50,
    offset: filters.offset ?? 0,
    sort: filters.sort ?? "desc",
  });
}

export function acknowledgeAlert(alertId) {
  return apiSend(
    `/api/v1/alerts/${encodeURIComponent(alertId)}/acknowledge`,
    "POST",
    {}
  );
}

export function resolveAlert(alertId) {
  return apiSend(
    `/api/v1/alerts/${encodeURIComponent(alertId)}/resolve`,
    "POST",
    {}
  );
}

export function getNotificationTargets(filters = {}) {
  return apiGet("/api/v1/notification-targets", {
    enabled: filters.enabled,
    is_global: filters.is_global,
    limit: filters.limit ?? 50,
    offset: filters.offset ?? 0,
  });
}

export function createNotificationTarget(body) {
  return apiSend("/api/v1/notification-targets", "POST", body);
}

export function patchNotificationTarget(targetId, body) {
  return apiSend(
    `/api/v1/notification-targets/${encodeURIComponent(targetId)}`,
    "PATCH",
    body
  );
}

export function associateRuleTarget(ruleId, targetId) {
  return apiSend(
    `/api/v1/alert-rules/${encodeURIComponent(ruleId)}/notification-targets/${encodeURIComponent(targetId)}`,
    "POST",
    {}
  );
}

export function getAlertDeliveries(alertId, filters = {}) {
  return apiGet(
    `/api/v1/alerts/${encodeURIComponent(alertId)}/deliveries`,
    {
      limit: filters.limit ?? 50,
      offset: filters.offset ?? 0,
    }
  );
}

export function getNotificationDeliveries(filters = {}) {
  return apiGet("/api/v1/notification-deliveries", {
    status: filters.status || undefined,
    alert_id: filters.alert_id || undefined,
    target_id: filters.target_id || undefined,
    limit: filters.limit ?? 50,
    offset: filters.offset ?? 0,
    sort: filters.sort ?? "desc",
  });
}

export function retryNotificationDelivery(deliveryId) {
  return apiSend(
    `/api/v1/notification-deliveries/${encodeURIComponent(deliveryId)}/retry`,
    "POST",
    {}
  );
}

/**
 * @param {string} path
 * @param {string} method
 * @param {object} body
 */
async function apiSend(path, method, body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      credentials: "same-origin",
      body: JSON.stringify(body),
    });
  } catch {
    /** @type {ApiError} */
    const err = {
      message: "Network error contacting API",
      status: null,
      code: "network_error",
    };
    throw err;
  }
  if (!response.ok) {
    await rejectResponse(response);
  }
  return response.json();
}

export function getActiveEntities(limit = 50) {
  return apiGet("/api/v1/entities/active", { limit, sort: "desc" });
}

export function getRecentEntities(limit = 50) {
  return apiGet("/api/v1/entities/recent", { limit });
}

/**
 * @param {string} entityId
 */
export function getEntity(entityId) {
  return apiGet(`/api/v1/entities/${encodeURIComponent(entityId)}`);
}

/**
 * @param {string} entityId
 * @param {object} [filters]
 */
export function getEntityObservations(entityId, filters = {}) {
  return apiGet(`/api/v1/entities/${encodeURIComponent(entityId)}/observations`, {
    limit: filters.limit ?? 50,
    sort: filters.sort ?? "desc",
  });
}

/**
 * @param {string} entityId
 * @param {object} [filters]
 */
export function getEntityTimeline(entityId, filters = {}) {
  return apiGet(`/api/v1/entities/${encodeURIComponent(entityId)}/timeline`, {
    limit: filters.limit ?? 50,
    sort: filters.sort ?? "desc",
    cursor: filters.cursor ?? undefined,
    event_type: filters.event_types || undefined,
  });
}
