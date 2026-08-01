/**
 * WebSocket client for /ws/v1/activity with reconnect backoff.
 */

const DEFAULT_LIFECYCLE = Object.freeze([
  "entity_created",
  "entity_closed",
  "zone_entered",
  "zone_exited",
  "zone_occupancy_changed",
]);

/**
 * @typedef {"disconnected"|"connecting"|"connected"|"reconnecting"|"closed"} WsState
 */

/**
 * @param {object} options
 */
export function createActivitySocket(options = {}) {
  const {
    path = "/ws/v1/activity",
    initialBackoffMs = 1000,
    maxBackoffMs = 30000,
    onState = () => {},
    onReady = () => {},
    onEvent = () => {},
    onSubscriptionUpdated = () => {},
    onWarning = () => {},
    onError = () => {},
    onHeartbeat = () => {},
  } = options;

  /** @type {WebSocket|null} */
  let socket = null;
  /** @type {WsState} */
  let state = "disconnected";
  let attempt = 0;
  let stopped = false;
  /** @type {ReturnType<typeof setTimeout>|null} */
  let reconnectTimer = null;
  /** @type {object|null} */
  let pendingFilters = null;

  function setState(next) {
    state = next;
    onState(state, attempt);
  }

  function wsUrl() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}${path}`;
  }

  function clearReconnectTimer() {
    if (reconnectTimer != null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function scheduleReconnect() {
    if (stopped) return;
    clearReconnectTimer();
    attempt += 1;
    setState("reconnecting");
    const exp = Math.min(
      maxBackoffMs,
      initialBackoffMs * 2 ** Math.min(attempt - 1, 8)
    );
    const jitter = Math.floor(Math.random() * 250);
    reconnectTimer = setTimeout(() => {
      connect();
    }, exp + jitter);
  }

  function connect() {
    if (stopped) return;
    clearReconnectTimer();
    if (
      socket &&
      (socket.readyState === WebSocket.OPEN ||
        socket.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    setState(attempt > 0 ? "reconnecting" : "connecting");
    try {
      socket = new WebSocket(wsUrl());
    } catch (err) {
      onError({ code: "ws_construct", message: "Unable to open WebSocket" });
      scheduleReconnect();
      return;
    }

    socket.addEventListener("open", () => {
      // Wait for connection.ready before treating as fully connected.
    });

    socket.addEventListener("message", (ev) => {
      let data;
      try {
        data = JSON.parse(String(ev.data));
      } catch {
        onWarning("Ignored malformed WebSocket message");
        return;
      }
      if (!data || typeof data !== "object") {
        onWarning("Ignored non-object WebSocket message");
        return;
      }
      const type = data.type;
      if (type === "connection.ready") {
        attempt = 0;
        setState("connected");
        onReady(data);
        if (pendingFilters) {
          updateSubscription(pendingFilters);
        }
        return;
      }
      if (type === "timeline.event") {
        if (data.event && typeof data.event === "object" && data.event.id) {
          onEvent(data.event, data);
        } else {
          onWarning("Ignored timeline.event without valid payload");
        }
        return;
      }
      if (type === "subscription.updated") {
        onSubscriptionUpdated(data);
        return;
      }
      if (type === "heartbeat") {
        onHeartbeat(data);
        try {
          socket?.send(JSON.stringify({ type: "heartbeat.ack" }));
        } catch {
          // ignore
        }
        return;
      }
      if (type === "stream.warning") {
        onWarning(
          typeof data.message === "string"
            ? data.message
            : "Stream warning"
        );
        return;
      }
      if (type === "error") {
        onError({
          code: String(data.code || "protocol_error"),
          message:
            typeof data.message === "string"
              ? data.message
              : "Protocol error",
        });
        return;
      }
      onWarning("Ignored unknown WebSocket message type");
    });

    socket.addEventListener("close", () => {
      socket = null;
      if (stopped) {
        setState("closed");
        return;
      }
      scheduleReconnect();
    });

    socket.addEventListener("error", () => {
      // close handler will reconnect
    });
  }

  /**
   * @param {object} filters
   */
  function updateSubscription(filters) {
    pendingFilters = filters;
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    const payload = {
      type: "subscription.update",
      filters: {
        event_types: filters.event_types || DEFAULT_LIFECYCLE.slice(),
        camera_ids: filters.camera_ids || [],
        entity_ids: filters.entity_ids || [],
        entity_types: filters.entity_types || [],
      },
    };
    try {
      socket.send(JSON.stringify(payload));
      return true;
    } catch {
      onError({ code: "send_failed", message: "Failed to send subscription" });
      return false;
    }
  }

  function start() {
    stopped = false;
    connect();
  }

  function stop() {
    stopped = true;
    clearReconnectTimer();
    if (socket) {
      try {
        socket.close(1000, "console stop");
      } catch {
        // ignore
      }
      socket = null;
    }
    setState("closed");
  }

  function getState() {
    return { state, attempt };
  }

  return {
    start,
    stop,
    connect,
    updateSubscription,
    getState,
    DEFAULT_LIFECYCLE,
  };
}

export { DEFAULT_LIFECYCLE };
