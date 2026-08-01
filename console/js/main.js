/**
 * Live Activity Console coordinator.
 */

import {
  getActiveEntities,
  getEntity,
  getHealth,
  getRecentEntities,
  getTimeline,
  sanitizeMessage,
} from "./api.js";
import { createRecoveryController, filtersFromUi } from "./recovery.js";
import { SOURCE, createStore } from "./store.js";
import {
  moveFeedSelection,
  renderEntityDetail,
  renderEntityList,
  renderEventFeed,
  renderStatusBar,
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
  activeEntities: document.getElementById("active-entities"),
  recentEntities: document.getElementById("recent-entities"),
  entityDetail: document.getElementById("entity-detail"),
  entityDetailEmpty: document.getElementById("entity-detail-empty"),
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
  },
};

/** @type {object} */
let uiFilters = {
  event_types: ["entity_created", "entity_closed"],
  camera_id: "",
  entity_type: "",
  entity_id: "",
};

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
    event_types: types.length ? types : ["entity_created", "entity_closed"],
    camera_id: dom.filterCamera.value.trim(),
    entity_type: dom.filterEntityType.value.trim(),
    entity_id: dom.filterEntityId.value.trim(),
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
    for (const input of dom.filtersForm.querySelectorAll(
      'input[name="event_type"]'
    )) {
      input.checked =
        input.value === "entity_created" || input.value === "entity_closed";
    }
    dom.filterCamera.value = "";
    dom.filterEntityType.value = "";
    dom.filterEntityId.value = "";
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
}

async function init() {
  wireUi();
  readFiltersFromForm();
  refreshStatus();
  refreshFeed();
  renderEntityDetail(dom.entityDetail, null, dom.entityDetailEmpty);

  await probeHealth();
  await loadInitialHistory();
  await loadEntityLists();
  socket.start();
  applyWsSubscription();
  refreshStatus();
}

void init();
