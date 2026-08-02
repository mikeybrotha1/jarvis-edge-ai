/**
 * Reconnect recovery using overlapping Timeline API windows.
 *
 * Strategy (safe with stable IDs):
 * - Remember last processed event (id + occurred_at).
 * - On reconnect, buffer live events.
 * - Query GET /api/v1/timeline with occurred_after = last_occurred_at - overlap
 *   (default overlap 5s) and current filters, sort=asc, paginating with cursor
 *   until past "now" or pages exhausted.
 * - Upsert recovered + buffered events by stable ID (authoritative dedupe).
 * - Overlap is safe because duplicate IDs collapse; we never invent events.
 */

import { getTimeline } from "./api.js";
import { SOURCE, normalizeTimestamp } from "./store.js";

export const DEFAULT_OVERLAP_MS = 5000;
export const DEFAULT_PAGE_LIMIT = 100;

/**
 * @param {object} options
 * @param {object} options.store
 * @param {() => object} options.getFilters
 * @param {(msg: string) => void} [options.onStatus]
 * @param {(err: object) => void} [options.onError]
 * @param {number} [options.overlapMs]
 */
export function createRecoveryController(options) {
  const {
    store,
    getFilters,
    onStatus = () => {},
    onError = () => {},
    overlapMs = DEFAULT_OVERLAP_MS,
  } = options;

  /** @type {{ id: string, occurred_at: string }|null} */
  let lastProcessed = null;
  /** @type {object[]} */
  let liveBuffer = [];
  let recovering = false;

  function isRecovering() {
    return recovering;
  }

  function noteProcessed(event) {
    if (!event || !event.id || !event.occurred_at) return;
    if (!lastProcessed) {
      lastProcessed = { id: String(event.id), occurred_at: String(event.occurred_at) };
      return;
    }
    const cmp =
      normalizeTimestamp(event.occurred_at) -
      normalizeTimestamp(lastProcessed.occurred_at);
    if (cmp > 0 || (cmp === 0 && String(event.id) >= lastProcessed.id)) {
      lastProcessed = {
        id: String(event.id),
        occurred_at: String(event.occurred_at),
      };
    }
  }

  function onLiveEventDuringRecovery(event) {
    if (!event || !event.id) return;
    liveBuffer.push(event);
  }

  /**
   * Begin recovery after connection.ready following a disconnect.
   */
  async function runRecovery() {
    if (recovering) return;
    recovering = true;
    liveBuffer = [];
    onStatus("recovering");

    try {
      const filters = getFilters();
      const recovered = await fetchOverlapWindow(filters, lastProcessed, overlapMs);
      store.upsertMany(recovered, SOURCE.RECOVERED);
      const buffered = liveBuffer.slice();
      liveBuffer = [];
      store.upsertMany(buffered, SOURCE.LIVE);
      for (const ev of store.getOrderedNewestFirst()) {
        noteProcessed(ev);
      }
      onStatus("live");
    } catch (err) {
      onError({
        code: "recovery_failed",
        message:
          err && typeof err.message === "string"
            ? err.message
            : "Recovery failed",
      });
      // Still merge any buffered live events so the console is not stuck.
      const buffered = liveBuffer.slice();
      liveBuffer = [];
      store.upsertMany(buffered, SOURCE.LIVE);
      onStatus("degraded");
    } finally {
      recovering = false;
    }
  }

  return {
    isRecovering,
    noteProcessed,
    onLiveEventDuringRecovery,
    runRecovery,
    getLastProcessed: () => lastProcessed,
    setLastProcessed: (value) => {
      lastProcessed = value;
    },
  };
}

/**
 * Fetch timeline pages covering [last - overlap, ∞) ascending.
 * @param {object} filters
 * @param {{ id: string, occurred_at: string }|null} lastProcessed
 * @param {number} overlapMs
 */
export async function fetchOverlapWindow(
  filters,
  lastProcessed,
  overlapMs = DEFAULT_OVERLAP_MS
) {
  /** @type {object[]} */
  const collected = [];
  let cursor = null;
  let pages = 0;
  const maxPages = 20;

  let occurred_after;
  if (lastProcessed && lastProcessed.occurred_at) {
    const base = normalizeTimestamp(lastProcessed.occurred_at);
    occurred_after = new Date(Math.max(0, base - overlapMs)).toISOString();
  }

  while (pages < maxPages) {
    pages += 1;
    const page = await getTimeline({
      ...filters,
      limit: DEFAULT_PAGE_LIMIT,
      sort: "asc",
      cursor: cursor || undefined,
      occurred_after: occurred_after || undefined,
    });
    const items = Array.isArray(page.items) ? page.items : [];
    for (const item of items) {
      if (item && item.id) collected.push(item);
    }
    if (!page.next_cursor || items.length === 0) break;
    cursor = page.next_cursor;
  }

  return collected;
}

/**
 * Serialize console filters for Timeline/WS APIs.
 * @param {object} ui
 */
export function filtersFromUi(ui) {
  const event_types =
    Array.isArray(ui.event_types) && ui.event_types.length
      ? ui.event_types.slice()
      : [
          "entity_created",
          "entity_closed",
          "zone_entered",
          "zone_exited",
          "zone_occupancy_changed",
          "alert_triggered",
          "alert_resolved",
        ];
  return {
    event_types,
    camera_id: ui.camera_id || "",
    entity_type: ui.entity_type || "",
    entity_id: ui.entity_id || "",
    zone_id: ui.zone_id || "",
  };
}
