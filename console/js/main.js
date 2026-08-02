/**
 * Live Activity Console coordinator.
 */

import {
  acknowledgeAlert,
  createAlertRule,
  createZone,
  getActiveEntities,
  getAlerts,
  getEntity,
  getHealth,
  getRecentEntities,
  getTimeline,
  getZoneOccupancy,
  getZoneSessions,
  getZones,
  patchZone,
  resolveAlert,
  sanitizeMessage,
} from "./api.js";
import { createRecoveryController, filtersFromUi } from "./recovery.js";
import { SOURCE, createStore } from "./store.js";
import {
  moveFeedSelection,
  renderEntityDetail,
  renderEntityList,
  renderEventFeed,
  renderOccupancyPanel,
  renderStatusBar,
  renderZoneList,
  renderZoneSessions,
  setWarning,
} from "./ui.js";
import { createActivitySocket } from "./ws.js";

const FEED_RENDER_CAP = 200;
const HISTORY_PAGE = 50;

const store = createStore(400);

const dom = {
  warning: document.getElementById("warning-banner"),
  feedState: document.getElementById("feed-state"),
  eventList: document.getElementById("event-list"),
  feedCount: document.getElementById("feed-count"),
  loadOlder: document.getElementById("btn-load-older"),
  historyEnd: document.getElementById("history-end"),
  filtersForm: document.getElementById("filters-form"),
  clearFilters: document.getElementById("btn-clear-filters"),
  filterCamera: document.getElementById("filter-camera"),
  filterEntityType: document.getElementById("filter-entity-type"),
  filterEntityId: document.getElementById("filter-entity-id"),
  filterZoneId: document.getElementById("filter-zone-id"),
  activeEntities: document.getElementById("active-entities"),
  recentEntities: document.getElementById("recent-entities"),
  entityDetail: document.getElementById("entity-detail"),
  entityDetailEmpty: document.getElementById("entity-detail-empty"),
  zoneList: document.getElementById("zone-list"),
  occupancyPanel: document.getElementById("occupancy-panel"),
  zoneSessions: document.getElementById("zone-sessions"),
  zoneForm: document.getElementById("zone-form"),
  zoneEditId: document.getElementById("zone-edit-id"),
  zoneName: document.getElementById("zone-name"),
  zoneCamera: document.getElementById("zone-camera"),
  zoneXmin: document.getElementById("zone-xmin"),
  zoneYmin: document.getElementById("zone-ymin"),
  zoneXmax: document.getElementById("zone-xmax"),
  zoneYmax: document.getElementById("zone-ymax"),
  zoneEntityTypes: document.getElementById("zone-entity-types"),
  zoneMinConf: document.getElementById("zone-min-conf"),
  zoneStrategy: document.getElementById("zone-strategy"),
  zoneEnabled: document.getElementById("zone-enabled"),
  zoneFormStatus: document.getElementById("zone-form-status"),
  btnZoneDisable: document.getElementById("btn-zone-disable"),
  btnZoneReset: document.getElementById("btn-zone-reset"),
  alertList: document.getElementById("alert-list"),
  alertDetail: document.getElementById("alert-detail"),
  btnAlertAck: document.getElementById("btn-alert-ack"),
  btnAlertResolve: document.getElementById("btn-alert-resolve"),
  alertRuleForm: document.getElementById("alert-rule-form"),
  ruleName: document.getElementById("rule-name"),
  ruleEventType: document.getElementById("rule-event-type"),
  ruleSeverity: document.getElementById("rule-severity"),
  ruleFormStatus: document.getElementById("rule-form-status"),
  status: {
    ws: {
      item: document.querySelector('[data-status="ws"]'),
      value: document.getElementById("status-ws"),
    },
    stream: {
      item: document.querySelector('[data-status="stream"]'),
      value: document.getElementById("status-stream"),
    },
    lastEvent: {
      item: document.querySelector('[data-status="last-event"]'),
      value: document.getElementById("status-last-event"),
    },
    reconnect: {
      item: document.querySelector('[data-status="reconnect"]'),
      value: document.getElementById("status-reconnect"),
    },
    observations: {
      item: document.querySelector('[data-status="observations"]'),
      value: document.getElementById("status-observations"),
    },
    mode: {
      item: document.querySelector('[data-status="mode"]'),
      value: document.getElementById("status-mode"),
    },
    alerts: {
      item: document.querySelector('[data-status="alerts"]'),
      value: document.getElementById("status-alerts"),
    },
  },
};

/** @type {string|null} */
let selectedAlertId = null;
/** @type {object[]} */
let alertsCache = [];

/** @type {object} */
let uiFilters = {
  event_types: [
    "entity_created",
    "entity_closed",
    "zone_entered",
    "zone_exited",
    "zone_occupancy_changed",
    "alert_triggered",
    "alert_resolved",
  ],
  camera_id: "",
  entity_type: "",
  entity_id: "",
  zone_id: "",
};

/** @type {string|null} */
let selectedZoneId = null;
/** @type {object[]} */
let zonesCache = [];

let nextCursor = null;
let historyExhausted = false;
let loadingHistory = false;
let loadingOlder = false;
let hasConnectedOnce = false;
/** @type {string} */
let mode = "loading";
/** @type {string} */
let wsState = "disconnected";
let reconnectAttempt = 0;
let lastEventAt = null;
let streamReady = false;
let lastWarning = null;

const recovery = createRecoveryController({
  store,
  getFilters: () => filtersFromUi(uiFilters),
  onStatus: (status) => {
    mode = status;
    refreshStatus();
    refreshFeed();
  },
  onError: (err) => {
    showWarning(sanitizeMessage(err.message || "Recovery error"));
  },
});

const socket = createActivitySocket({
  onState: (state, attempt) => {
    wsState = state;
    reconnectAttempt = attempt;
    if (state === "reconnecting" || state === "disconnected") {
      mode = state === "reconnecting" ? "reconnecting" : "disconnected";
    }
    refreshStatus();
  },
  onReady: async () => {
    streamReady = true;
    if (hasConnectedOnce) {
      await recovery.runRecovery();
    } else {
      hasConnectedOnce = true;
      mode = "live";
    }
    applyWsSubscription();
    refreshStatus();
    refreshFeed();
  },
  onEvent: (event) => {
    if (recovery.isRecovering()) {
      recovery.onLiveEventDuringRecovery(event);
      return;
    }
    store.upsertMany([event], SOURCE.LIVE);
    recovery.noteProcessed(event);
    lastEventAt = event.occurred_at || lastEventAt;
    refreshFeed();
    refreshStatus();
  },
  onWarning: (message) => {
    showWarning(sanitizeMessage(message));
  },
  onError: (err) => {
    showWarning(sanitizeMessage(err.message || "WebSocket error"));
  },
  onSubscriptionUpdated: () => {
    // no-op; status already reflects filters
  },
});

function showWarning(message) {
  lastWarning = message;
  setWarning(dom.warning, message);
}

function clearWarning() {
  lastWarning = null;
  setWarning(dom.warning, null);
}

function readFiltersFromForm() {
  const types = Array.from(
    dom.filtersForm.querySelectorAll('input[name="event_type"]:checked')
  ).map((n) => n.value);
  uiFilters = {
    event_types: types.length
      ? types
      : [
          "entity_created",
          "entity_closed",
          "zone_entered",
          "zone_exited",
          "zone_occupancy_changed",
          "alert_triggered",
          "alert_resolved",
        ],
    camera_id: dom.filterCamera.value.trim(),
    entity_type: dom.filterEntityType.value.trim(),
    entity_id: dom.filterEntityId.value.trim(),
    zone_id: dom.filterZoneId ? dom.filterZoneId.value.trim() : "",
  };
  return uiFilters;
}

function applyWsSubscription() {
  const f = filtersFromUi(uiFilters);
  socket.updateSubscription({
    event_types: f.event_types,
    camera_ids: f.camera_id ? [f.camera_id] : [],
    entity_ids: f.entity_id ? [f.entity_id] : [],
    entity_types: f.entity_type ? [f.entity_type] : [],
    zone_ids: f.zone_id ? [f.zone_id] : [],
  });
}

function refreshStatus() {
  const obsOn = uiFilters.event_types.includes("observation_recorded");
  const wsTone =
    wsState === "connected" ? "ok" : wsState === "reconnecting" ? "warn" : "error";
  const modeTone =
    mode === "live"
      ? "ok"
      : mode === "recovering" || mode === "reconnecting"
        ? "warn"
        : mode === "degraded" || mode === "error"
          ? "error"
          : null;

  const openAlerts = alertsCache.filter(
    (a) => a.status === "open" || a.status === "acknowledged"
  ).length;
  renderStatusBar(dom.status, {
    wsText: wsState,
    wsTone,
    streamText: streamReady ? "ready" : "not ready",
    streamTone: streamReady ? "ok" : "warn",
    lastEventText: lastEventAt ? String(lastEventAt) : "—",
    reconnectText: `${wsState === "reconnecting" ? "retrying" : "idle"} · ${reconnectAttempt}`,
    reconnectTone: wsState === "reconnecting" ? "warn" : null,
    observationsText: obsOn ? "on" : "off",
    modeText: mode,
    modeTone,
  });
  if (dom.status.alerts && dom.status.alerts.value) {
    dom.status.alerts.value.textContent = String(openAlerts);
  }
}

function refreshFeed() {
  const events = store.getOrderedNewestFirst();
  const selected = store.getSelectedEvent();
  const selectedId = selected ? selected.id : null;

  if (loadingHistory && events.length === 0) {
    dom.feedState.textContent = "Loading history…";
  } else if (mode === "recovering") {
    dom.feedState.textContent = "Recovering missed events…";
  } else if (mode === "reconnecting" || wsState === "reconnecting") {
    dom.feedState.textContent = "Reconnecting to live stream…";
  } else if (mode === "disconnected" || wsState === "disconnected") {
    dom.feedState.textContent =
      "Disconnected from live stream. Showing stored events.";
  } else if (events.length === 0) {
    dom.feedState.textContent = "No events for the current filters.";
  } else {
    dom.feedState.textContent = "";
  }

  dom.feedCount.textContent = `${events.length} event${events.length === 1 ? "" : "s"}`;
  renderEventFeed(
    dom.eventList,
    events,
    selectedId,
    (id) => {
      store.selectEvent(id);
      refreshFeed();
      void loadSelectedEntity();
    },
    FEED_RENDER_CAP
  );

  dom.loadOlder.disabled = loadingOlder || historyExhausted || loadingHistory;
  dom.historyEnd.hidden = !historyExhausted;
}

async function loadInitialHistory() {
  loadingHistory = true;
  historyExhausted = false;
  nextCursor = null;
  mode = "loading";
  refreshStatus();
  refreshFeed();
  try {
    const page = await getTimeline({
      ...filtersFromUi(uiFilters),
      limit: HISTORY_PAGE,
      sort: "desc",
    });
    const items = Array.isArray(page.items) ? page.items : [];
    store.clear();
    store.upsertMany(items, SOURCE.HISTORICAL);
    for (const ev of store.getOrderedNewestFirst()) {
      recovery.noteProcessed(ev);
    }
    nextCursor = page.next_cursor || null;
    historyExhausted = !nextCursor;
    if (items[0]) lastEventAt = items[0].occurred_at;
    mode = wsState === "connected" ? "live" : "historical";
    clearWarning();
  } catch (err) {
    mode = "error";
    showWarning(sanitizeMessage(err.message || "Failed to load timeline"));
  } finally {
    loadingHistory = false;
    refreshStatus();
    refreshFeed();
  }
}

async function loadOlder() {
  if (loadingOlder || historyExhausted || !nextCursor) return;
  loadingOlder = true;
  refreshFeed();
  try {
    const page = await getTimeline({
      ...filtersFromUi(uiFilters),
      limit: HISTORY_PAGE,
      sort: "desc",
      cursor: nextCursor,
    });
    const items = Array.isArray(page.items) ? page.items : [];
    store.upsertMany(items, SOURCE.HISTORICAL);
    nextCursor = page.next_cursor || null;
    historyExhausted = !nextCursor;
  } catch (err) {
    showWarning(sanitizeMessage(err.message || "Failed to load older events"));
  } finally {
    loadingOlder = false;
    refreshFeed();
  }
}

async function loadEntityLists() {
  try {
    const [active, recent] = await Promise.all([
      getActiveEntities(30),
      getRecentEntities(30),
    ]);
    renderEntityList(
      dom.activeEntities,
      active.items || [],
      store.getSelectedEntityId(),
      onEntitySelected
    );
    renderEntityList(
      dom.recentEntities,
      recent.items || [],
      store.getSelectedEntityId(),
      onEntitySelected
    );
  } catch (err) {
    showWarning(sanitizeMessage(err.message || "Failed to load entities"));
  }
}

async function onEntitySelected(entityId) {
  store.selectEntity(entityId);
  await loadSelectedEntity();
  refreshFeed();
  await loadEntityLists();
}

async function loadSelectedEntity() {
  const id = store.getSelectedEntityId();
  if (!id) {
    renderEntityDetail(dom.entityDetail, null, dom.entityDetailEmpty);
    return;
  }
  try {
    const entity = await getEntity(id);
    renderEntityDetail(dom.entityDetail, entity, dom.entityDetailEmpty);
  } catch (err) {
    renderEntityDetail(dom.entityDetail, null, dom.entityDetailEmpty);
    showWarning(sanitizeMessage(err.message || "Entity not found"));
  }
}

async function probeHealth() {
  try {
    await getHealth();
    streamReady = streamReady; // unchanged
  } catch {
    showWarning("API health check failed");
  }
}

function wireUi() {
  dom.filtersForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    readFiltersFromForm();
    applyWsSubscription();
    refreshStatus();
    await loadInitialHistory();
  });

  dom.clearFilters.addEventListener("click", async () => {
    const defaults = new Set([
      "entity_created",
      "entity_closed",
      "zone_entered",
      "zone_exited",
      "zone_occupancy_changed",
      "alert_triggered",
      "alert_resolved",
    ]);
    for (const input of dom.filtersForm.querySelectorAll(
      'input[name="event_type"]'
    )) {
      input.checked = defaults.has(input.value);
    }
    dom.filterCamera.value = "";
    dom.filterEntityType.value = "";
    dom.filterEntityId.value = "";
    if (dom.filterZoneId) dom.filterZoneId.value = "";
    readFiltersFromForm();
    applyWsSubscription();
    refreshStatus();
    await loadInitialHistory();
  });

  dom.loadOlder.addEventListener("click", () => {
    void loadOlder();
  });

  dom.eventList.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      const selected = store.getSelectedEvent();
      moveFeedSelection(
        dom.eventList,
        selected ? selected.id : null,
        ev.key,
        (id) => {
          store.selectEvent(id);
          refreshFeed();
          void loadSelectedEntity();
        }
      );
    }
  });

  if (dom.zoneForm) {
    dom.zoneForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      await saveZoneFromForm();
    });
  }
  if (dom.btnZoneDisable) {
    dom.btnZoneDisable.addEventListener("click", () => {
      void disableSelectedZone();
    });
  }
  if (dom.btnZoneReset) {
    dom.btnZoneReset.addEventListener("click", () => {
      resetZoneForm();
    });
  }
}

function resetZoneForm() {
  if (!dom.zoneForm) return;
  dom.zoneEditId.value = "";
  dom.zoneName.value = "";
  dom.zoneCamera.value = "";
  dom.zoneXmin.value = "0.2";
  dom.zoneYmin.value = "0.2";
  dom.zoneXmax.value = "0.8";
  dom.zoneYmax.value = "0.8";
  dom.zoneEntityTypes.value = "";
  dom.zoneMinConf.value = "";
  dom.zoneStrategy.value = "";
  dom.zoneEnabled.checked = true;
  setZoneFormStatus("");
}

function setZoneFormStatus(message) {
  if (dom.zoneFormStatus) {
    dom.zoneFormStatus.textContent = message ? String(message) : "";
  }
}

function fillZoneForm(zone) {
  if (!zone) return;
  dom.zoneEditId.value = String(zone.id || "");
  dom.zoneName.value = String(zone.name || "");
  dom.zoneCamera.value = String(zone.camera_id || "");
  const verts = zone.vertices || [];
  const xs = verts.map((v) => Number(v.x)).filter((n) => Number.isFinite(n));
  const ys = verts.map((v) => Number(v.y)).filter((n) => Number.isFinite(n));
  if (xs.length && ys.length) {
    dom.zoneXmin.value = String(Math.min(...xs));
    dom.zoneXmax.value = String(Math.max(...xs));
    dom.zoneYmin.value = String(Math.min(...ys));
    dom.zoneYmax.value = String(Math.max(...ys));
  }
  dom.zoneEntityTypes.value = Array.isArray(zone.entity_type_filters)
    ? zone.entity_type_filters.join(", ")
    : "";
  dom.zoneMinConf.value =
    zone.min_confidence == null ? "" : String(zone.min_confidence);
  dom.zoneStrategy.value = zone.position_strategy || "";
  dom.zoneEnabled.checked = zone.enabled !== false;
}

async function saveZoneFromForm() {
  const body = {
    name: dom.zoneName.value.trim(),
    camera_id: dom.zoneCamera.value.trim(),
    x_min: Number(dom.zoneXmin.value),
    y_min: Number(dom.zoneYmin.value),
    x_max: Number(dom.zoneXmax.value),
    y_max: Number(dom.zoneYmax.value),
    enabled: !!dom.zoneEnabled.checked,
  };
  const types = dom.zoneEntityTypes.value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (types.length) body.entity_type_filters = types;
  if (dom.zoneMinConf.value !== "") {
    body.min_confidence = Number(dom.zoneMinConf.value);
  }
  if (dom.zoneStrategy.value) {
    body.position_strategy = dom.zoneStrategy.value;
  }

  try {
    const editId = dom.zoneEditId.value.trim();
    if (editId) {
      await patchZone(editId, body);
      setZoneFormStatus("Zone updated.");
      selectedZoneId = editId;
    } else {
      const created = await createZone(body);
      setZoneFormStatus("Zone created.");
      selectedZoneId = created && created.id ? String(created.id) : null;
    }
    await loadZones();
  } catch (err) {
    setZoneFormStatus(sanitizeMessage(err.message || "Zone save failed"));
  }
}

async function disableSelectedZone() {
  if (!selectedZoneId) {
    setZoneFormStatus("Select a zone first.");
    return;
  }
  try {
    await patchZone(selectedZoneId, { enabled: false });
    setZoneFormStatus("Zone disabled.");
    await loadZones();
  } catch (err) {
    setZoneFormStatus(sanitizeMessage(err.message || "Disable failed"));
  }
}

async function loadZones() {
  if (!dom.zoneList) return;
  try {
    const page = await getZones({ limit: 50 });
    zonesCache = Array.isArray(page.items) ? page.items : [];
    renderZoneList(dom.zoneList, zonesCache, selectedZoneId, onZoneSelected);
    if (selectedZoneId) {
      await loadZoneDetails(selectedZoneId);
    } else {
      renderOccupancyPanel(dom.occupancyPanel, null);
      renderZoneSessions(dom.zoneSessions, []);
    }
  } catch (err) {
    showWarning(sanitizeMessage(err.message || "Failed to load zones"));
  }
}

async function onZoneSelected(zoneId) {
  selectedZoneId = zoneId;
  const zone = zonesCache.find((z) => String(z.id) === String(zoneId));
  if (zone) fillZoneForm(zone);
  renderZoneList(dom.zoneList, zonesCache, selectedZoneId, onZoneSelected);
  await loadZoneDetails(zoneId);
}

async function loadZoneDetails(zoneId) {
  try {
    const [occ, sessions] = await Promise.all([
      getZoneOccupancy(zoneId),
      getZoneSessions(zoneId, { limit: 15 }),
    ]);
    renderOccupancyPanel(dom.occupancyPanel, occ);
    renderZoneSessions(
      dom.zoneSessions,
      Array.isArray(sessions.items) ? sessions.items : []
    );
  } catch (err) {
    renderOccupancyPanel(dom.occupancyPanel, null);
    renderZoneSessions(dom.zoneSessions, []);
    showWarning(sanitizeMessage(err.message || "Failed to load zone detail"));
  }
}

async function loadAlerts() {
  if (!dom.alertList) return;
  try {
    const page = await getAlerts({ limit: 30, sort: "desc" });
    alertsCache = Array.isArray(page.items) ? page.items : [];
    while (dom.alertList.firstChild) dom.alertList.removeChild(dom.alertList.firstChild);
    for (const alert of alertsCache) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = `${alert.severity || "?"} · ${alert.status || "?"} · ${String(alert.summary || "").slice(0, 80)}`;
      btn.addEventListener("click", () => {
        selectedAlertId = String(alert.id);
        if (dom.alertDetail) {
          dom.alertDetail.textContent = `${alert.id} · rule ${alert.rule_id} · ${alert.summary || ""}`;
        }
        if (dom.btnAlertAck) dom.btnAlertAck.disabled = false;
        if (dom.btnAlertResolve) dom.btnAlertResolve.disabled = false;
      });
      li.appendChild(btn);
      dom.alertList.appendChild(li);
    }
    refreshStatus();
  } catch (err) {
    showWarning(sanitizeMessage(err.message || "Failed to load alerts"));
  }
}

async function init() {
  wireUi();
  readFiltersFromForm();
  refreshStatus();
  refreshFeed();
  renderEntityDetail(dom.entityDetail, null, dom.entityDetailEmpty);
  if (dom.occupancyPanel) renderOccupancyPanel(dom.occupancyPanel, null);
  if (dom.zoneSessions) renderZoneSessions(dom.zoneSessions, []);

  if (dom.btnAlertAck) {
    dom.btnAlertAck.addEventListener("click", async () => {
      if (!selectedAlertId) return;
      try {
        await acknowledgeAlert(selectedAlertId);
        await loadAlerts();
      } catch (err) {
        showWarning(sanitizeMessage(err.message || "Acknowledge failed"));
      }
    });
  }
  if (dom.btnAlertResolve) {
    dom.btnAlertResolve.addEventListener("click", async () => {
      if (!selectedAlertId) return;
      try {
        await resolveAlert(selectedAlertId);
        await loadAlerts();
      } catch (err) {
        showWarning(sanitizeMessage(err.message || "Resolve failed"));
      }
    });
  }
  if (dom.alertRuleForm) {
    dom.alertRuleForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        await createAlertRule({
          name: dom.ruleName.value.trim(),
          rule_type: "event_match",
          source_event_types: [dom.ruleEventType.value],
          severity: dom.ruleSeverity.value,
          cooldown_seconds: 0,
        });
        if (dom.ruleFormStatus) dom.ruleFormStatus.textContent = "Rule created.";
        dom.ruleName.value = "";
      } catch (err) {
        if (dom.ruleFormStatus) {
          dom.ruleFormStatus.textContent = sanitizeMessage(
            err.message || "Rule create failed"
          );
        }
      }
    });
  }

  await probeHealth();
  await loadInitialHistory();
  await loadEntityLists();
  await loadZones();
  await loadAlerts();
  socket.start();
  applyWsSubscription();
  refreshStatus();
}

void init();
