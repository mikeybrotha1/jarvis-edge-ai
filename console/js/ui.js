/**
 * DOM rendering helpers — no unsafe innerHTML with dynamic values.
 */

import { SOURCE } from "./store.js";

/**
 * @param {ParentNode} parent
 */
export function clearChildren(parent) {
  while (parent.firstChild) {
    parent.removeChild(parent.firstChild);
  }
}

/**
 * @param {string} tag
 * @param {object} [attrs]
 * @param {(Node|string)[]} [children]
 */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "className") {
      node.className = String(value);
    } else if (key === "text") {
      node.textContent = String(value);
    } else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "dataset" && typeof value === "object") {
      for (const [dk, dv] of Object.entries(value)) {
        node.dataset[dk] = String(dv);
      }
    } else {
      node.setAttribute(key, value === true ? "" : String(value));
    }
  }
  for (const child of children) {
    if (child == null) continue;
    if (typeof child === "string") {
      node.appendChild(document.createTextNode(child));
    } else {
      node.appendChild(child);
    }
  }
  return node;
}

/**
 * @param {HTMLElement} banner
 * @param {string|null} message
 */
export function setWarning(banner, message) {
  if (!message) {
    banner.hidden = true;
    clearChildren(banner);
    return;
  }
  banner.hidden = false;
  clearChildren(banner);
  banner.appendChild(document.createTextNode(String(message)));
}

/**
 * @param {object} els
 * @param {object} status
 */
export function renderStatusBar(els, status) {
  setStatusItem(els.ws, status.wsText, status.wsTone);
  setStatusItem(els.stream, status.streamText, status.streamTone);
  setStatusItem(els.lastEvent, status.lastEventText, null);
  setStatusItem(els.reconnect, status.reconnectText, status.reconnectTone);
  setStatusItem(els.observations, status.observationsText, null);
  setStatusItem(els.mode, status.modeText, status.modeTone);
}

/**
 * @param {{ value: HTMLElement, item: HTMLElement }} target
 * @param {string} text
 * @param {string|null} tone
 */
function setStatusItem(target, text, tone) {
  target.value.textContent = text;
  if (tone) {
    target.item.dataset.tone = tone;
  } else {
    delete target.item.dataset.tone;
  }
}

/**
 * @param {HTMLElement} listEl
 * @param {object[]} events newest-first
 * @param {string|null} selectedId
 * @param {(id: string) => void} onSelect
 * @param {number} renderCap
 */
export function renderEventFeed(listEl, events, selectedId, onSelect, renderCap = 200) {
  clearChildren(listEl);
  const slice = events.slice(0, renderCap);
  if (slice.length === 0) {
    return;
  }
  for (const event of slice) {
    const id = String(event.id);
    const selected = selectedId === id;
    const item = el("li", {
      className: "event-item",
      id: `event-${cssEscape(id)}`,
      role: "option",
      tabindex: "-1",
      "aria-selected": selected ? "true" : "false",
      dataset: { eventId: id },
    });

    const type = el("span", {
      className: `event-type ${safeClass(event.event_type)}`,
      text: String(event.event_type || "event"),
    });

    const zoneName =
      (event.payload && event.payload.zone_name) ||
      (isSpatialType(event.event_type) ? "zone" : null);

    const summary = el("p", {
      className: "event-summary",
      text: String(event.summary || "(no summary)"),
    });
    const metaChildren = [
      el("span", { text: formatTime(event.occurred_at) }),
      el("span", { text: String(event.camera_id || "—") }),
      el("span", { text: String(event.entity_type || "—") }),
      el("span", {
        className: "mono",
        text: truncateId(String(event.entity_id || "")),
      }),
    ];
    if (zoneName) {
      metaChildren.push(
        el("span", {
          className: "zone-badge",
          text: String(zoneName),
        })
      );
    }
    const meta = el("p", { className: "event-meta" }, metaChildren);
    const body = el("div", { className: "event-body" }, [summary, meta]);

    const source = el("span", {
      className: `event-source ${safeClass(event._source || SOURCE.HISTORICAL)}`,
      text: String(event._source || SOURCE.HISTORICAL),
    });

    item.appendChild(type);
    item.appendChild(body);
    item.appendChild(source);

    item.addEventListener("click", () => onSelect(id));
    item.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        onSelect(id);
      }
    });

    listEl.appendChild(item);
  }

  if (selectedId) {
    listEl.setAttribute("aria-activedescendant", `event-${cssEscape(selectedId)}`);
  } else {
    listEl.setAttribute("aria-activedescendant", "");
  }
}

/**
 * @param {HTMLElement} listEl
 * @param {object[]} entities
 * @param {string|null} selectedEntityId
 * @param {(id: string) => void} onSelect
 */
export function renderEntityList(listEl, entities, selectedEntityId, onSelect) {
  clearChildren(listEl);
  if (!entities || entities.length === 0) {
    listEl.appendChild(el("li", {}, [el("p", { className: "empty-list", text: "None" })]));
    return;
  }
  for (const entity of entities) {
    const id = String(entity.id);
    const btn = el("button", {
      type: "button",
      "aria-pressed": selectedEntityId === id ? "true" : "false",
    });
    const line = el("span", { className: "entity-line" }, [
      el("span", {
        text: `${entity.entity_type || entity.label || "entity"} · ${entity.status || "—"}`,
      }),
      el("span", { className: "entity-id", text: id }),
    ]);
    btn.appendChild(line);
    btn.addEventListener("click", () => onSelect(id));
    const li = el("li");
    li.appendChild(btn);
    listEl.appendChild(li);
  }
}

/**
 * @param {HTMLElement} root
 * @param {object|null} entity
 * @param {HTMLElement} emptyEl
 */
export function renderEntityDetail(root, entity, emptyEl) {
  const fields = root.querySelectorAll("[data-field]");
  if (!entity) {
    emptyEl.hidden = false;
    for (const node of fields) {
      node.textContent = "—";
    }
    return;
  }
  emptyEl.hidden = true;
  const map = {
    id: entity.id,
    entity_type: entity.entity_type || entity.label,
    label: entity.label,
    status: entity.status,
    camera_id: entity.camera_id,
    first_seen: formatTime(entity.first_seen),
    last_seen: formatTime(entity.last_seen),
    average_confidence:
      entity.average_confidence == null
        ? "—"
        : String(entity.average_confidence),
    times_seen: entity.times_seen,
  };
  for (const node of fields) {
    const key = node.getAttribute("data-field");
    const value = map[key];
    node.textContent = value == null || value === "" ? "—" : String(value);
  }
}

/**
 * @param {HTMLElement} listEl
 * @param {string|null} selectedId
 * @param {"ArrowDown"|"ArrowUp"} direction
 * @param {(id: string) => void} onSelect
 */
export function moveFeedSelection(listEl, selectedId, direction, onSelect) {
  const items = Array.from(listEl.querySelectorAll(".event-item"));
  if (items.length === 0) return;
  let index = items.findIndex((n) => n.dataset.eventId === selectedId);
  if (direction === "ArrowDown") {
    index = Math.min(items.length - 1, index + 1);
  } else {
    index = Math.max(0, index <= 0 ? 0 : index - 1);
  }
  const next = items[index];
  if (next && next.dataset.eventId) {
    onSelect(next.dataset.eventId);
    next.focus();
  }
}

/**
 * @param {HTMLElement} listEl
 * @param {object[]} zones
 * @param {string|null} selectedZoneId
 * @param {(id: string) => void} onSelect
 */
export function renderZoneList(listEl, zones, selectedZoneId, onSelect) {
  clearChildren(listEl);
  if (!zones || zones.length === 0) {
    listEl.appendChild(
      el("li", {}, [el("p", { className: "empty-list", text: "No zones" })])
    );
    return;
  }
  for (const zone of zones) {
    const id = String(zone.id);
    const btn = el("button", {
      type: "button",
      "aria-pressed": selectedZoneId === id ? "true" : "false",
    });
    const enabled = zone.enabled === false ? "disabled" : "enabled";
    const line = el("span", { className: "entity-line" }, [
      el("span", {
        text: `${zone.name || "zone"} · ${enabled} · ${zone.camera_id || "—"}`,
      }),
      el("span", { className: "entity-id", text: id }),
    ]);
    btn.appendChild(line);
    btn.addEventListener("click", () => onSelect(id));
    const li = el("li");
    li.appendChild(btn);
    listEl.appendChild(li);
  }
}

/**
 * @param {HTMLElement} panel
 * @param {object|null} occupancy
 */
export function renderOccupancyPanel(panel, occupancy) {
  clearChildren(panel);
  if (!occupancy) {
    panel.appendChild(
      el("p", { className: "hint", text: "Select a zone to view occupancy." })
    );
    return;
  }
  panel.appendChild(
    el("p", {
      className: "occupancy-title",
      text: `${occupancy.zone_name || "Zone"} · occupancy ${occupancy.occupancy ?? 0}`,
    })
  );
  panel.appendChild(
    el("p", {
      className: "hint",
      text: `Camera ${occupancy.camera_id || "—"} · updated ${formatTime(occupancy.updated_at)}`,
    })
  );
  const entities = occupancy.entities || [];
  if (entities.length === 0) {
    panel.appendChild(el("p", { className: "empty-list", text: "Empty" }));
    return;
  }
  const list = el("ul", { className: "occupancy-entities" });
  for (const ent of entities) {
    const dwell =
      ent.dwell_seconds == null ? "—" : `${Number(ent.dwell_seconds).toFixed(1)}s`;
    list.appendChild(
      el("li", {
        text: `${ent.entity_type || ent.label || "entity"} · dwell ${dwell} · ${truncateId(String(ent.entity_id || ""))}`,
      })
    );
  }
  panel.appendChild(list);
}

/**
 * @param {HTMLElement} listEl
 * @param {object[]} sessions
 */
export function renderZoneSessions(listEl, sessions) {
  clearChildren(listEl);
  if (!sessions || sessions.length === 0) {
    listEl.appendChild(
      el("li", {}, [el("p", { className: "empty-list", text: "No sessions" })])
    );
    return;
  }
  for (const sess of sessions) {
    const dwell =
      sess.dwell_seconds == null ? "—" : `${Number(sess.dwell_seconds).toFixed(1)}s`;
    listEl.appendChild(
      el("li", {
        text: `${sess.status || "—"} · ${formatTime(sess.entered_at)} · dwell ${dwell} · ${truncateId(String(sess.entity_id || ""))}`,
      })
    );
  }
}

function isSpatialType(eventType) {
  const t = String(eventType || "");
  return (
    t === "zone_entered" ||
    t === "zone_exited" ||
    t === "zone_occupancy_changed"
  );
}

function formatTime(value) {
  if (value == null || value === "") return "—";
  const ms = Date.parse(String(value));
  if (!Number.isFinite(ms)) return String(value);
  try {
    return new Date(ms).toISOString().replace(".000Z", "Z");
  } catch {
    return String(value);
  }
}

function truncateId(id) {
  if (id.length <= 14) return id;
  return id.slice(0, 8) + "…" + id.slice(-4);
}

function safeClass(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .slice(0, 40);
}

function cssEscape(value) {
  // Minimal escape for id attributes; not for CSS selectors injection.
  return String(value).replace(/[^a-zA-Z0-9_-]/g, "_");
}
