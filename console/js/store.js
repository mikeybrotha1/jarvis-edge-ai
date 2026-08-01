/**
 * In-memory activity store with deterministic ordering and hard cap.
 *
 * Ordering: occurred_at ascending for history merge, newest-first for display.
 * Stable TimelineEvent IDs are the deduplication key.
 */

export const DEFAULT_CAP = 400;
export const SOURCE = Object.freeze({
  LIVE: "live",
  RECOVERED: "recovered",
  HISTORICAL: "historical",
});

/**
 * Compare two events: occurred_at then id (lexicographic).
 * @param {object} a
 * @param {object} b
 * @returns {number}
 */
export function compareEvents(a, b) {
  const ta = normalizeTimestamp(a.occurred_at);
  const tb = normalizeTimestamp(b.occurred_at);
  if (ta < tb) return -1;
  if (ta > tb) return 1;
  const ida = String(a.id || "");
  const idb = String(b.id || "");
  if (ida < idb) return -1;
  if (ida > idb) return 1;
  return 0;
}

/**
 * @param {string|number|Date|null|undefined} value
 * @returns {number} epoch ms
 */
export function normalizeTimestamp(value) {
  if (value == null) return 0;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const text = String(value).trim();
  if (!text) return 0;
  const ms = Date.parse(text.endsWith("Z") || text.includes("+") || text.includes("-", 10)
    ? text
    : text + "Z");
  return Number.isFinite(ms) ? ms : 0;
}

/**
 * @param {number} [cap]
 */
export function createStore(cap = DEFAULT_CAP) {
  /** @type {Map<string, object>} */
  const byId = new Map();
  /** @type {string[]} newest-first ordered IDs */
  let orderedIds = [];
  /** @type {string|null} */
  let selectedEventId = null;
  /** @type {string|null} */
  let selectedEntityId = null;
  const hardCap = Math.max(50, Number(cap) || DEFAULT_CAP);

  function size() {
    return byId.size;
  }

  function get(id) {
    return byId.get(String(id)) || null;
  }

  function getOrderedNewestFirst() {
    return orderedIds.map((id) => byId.get(id)).filter(Boolean);
  }

  function getSelectedEvent() {
    return selectedEventId ? get(selectedEventId) : null;
  }

  function getSelectedEntityId() {
    return selectedEntityId;
  }

  function selectEvent(id) {
    if (id == null) {
      selectedEventId = null;
      return;
    }
    const key = String(id);
    if (!byId.has(key)) return;
    selectedEventId = key;
    const ev = byId.get(key);
    if (ev && ev.entity_id) {
      selectedEntityId = String(ev.entity_id);
    }
  }

  function selectEntity(entityId) {
    selectedEntityId = entityId == null ? null : String(entityId);
  }

  /**
   * Insert or update events. Returns { added, updated, dropped }.
   * @param {object[]} events
   * @param {string} source
   */
  function upsertMany(events, source = SOURCE.HISTORICAL) {
    let added = 0;
    let updated = 0;
    for (const raw of events || []) {
      if (!raw || raw.id == null) continue;
      const id = String(raw.id);
      const existing = byId.get(id);
      const record = {
        ...raw,
        id,
        _source: existing && existing._source === SOURCE.LIVE && source !== SOURCE.LIVE
          ? existing._source
          : source,
      };
      if (existing) {
        // Prefer non-empty fields; keep earliest source preference for live.
        byId.set(id, { ...existing, ...record, _source: record._source });
        updated += 1;
      } else {
        byId.set(id, record);
        added += 1;
      }
    }
    rebuildOrder();
    const dropped = enforceCap();
    return { added, updated, dropped };
  }

  function rebuildOrder() {
    orderedIds = Array.from(byId.values())
      .sort((a, b) => -compareEvents(a, b))
      .map((e) => e.id);
  }

  function enforceCap() {
    let dropped = 0;
    while (orderedIds.length > hardCap) {
      // Drop oldest (end of newest-first list).
      const id = orderedIds.pop();
      if (id == null) break;
      byId.delete(id);
      if (selectedEventId === id) selectedEventId = null;
      dropped += 1;
    }
    return dropped;
  }

  function clear() {
    byId.clear();
    orderedIds = [];
    selectedEventId = null;
  }

  function snapshot() {
    return {
      size: byId.size,
      orderedIds: orderedIds.slice(),
      selectedEventId,
      selectedEntityId,
      cap: hardCap,
    };
  }

  return {
    size,
    get,
    getOrderedNewestFirst,
    getSelectedEvent,
    getSelectedEntityId,
    selectEvent,
    selectEntity,
    upsertMany,
    clear,
    snapshot,
    compareEvents,
  };
}
