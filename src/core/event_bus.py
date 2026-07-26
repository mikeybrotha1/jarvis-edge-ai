"""Thread-safe publish/subscribe event bus for Jarvis Edge AI."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from threading import RLock
from typing import TypeAlias

from core.events import EventType, JarvisEvent


logger = logging.getLogger("jarvis.event_bus")

EventHandler: TypeAlias = Callable[[JarvisEvent], None]


class EventBus:
    """Dispatch structured events to subscribed handlers.

    Handlers run synchronously in the publishing thread for now. This keeps the
    first implementation deterministic and easy to debug. The public interface
    can later be backed by queues or worker threads without changing publishers.
    """

    def __init__(self) -> None:
        self._subscribers: dict[
            EventType | None,
            list[EventHandler],
        ] = defaultdict(list)

        self._lock = RLock()

    def subscribe(
        self,
        event_type: EventType | None,
        handler: EventHandler,
    ) -> None:
        """Subscribe a handler.

        Passing ``None`` subscribes the handler to every event.
        """

        with self._lock:
            handlers = self._subscribers[event_type]

            if handler not in handlers:
                handlers.append(handler)

    def unsubscribe(
        self,
        event_type: EventType | None,
        handler: EventHandler,
    ) -> None:
        """Remove a handler if currently subscribed."""

        with self._lock:
            handlers = self._subscribers.get(event_type)

            if not handlers:
                return

            try:
                handlers.remove(handler)
            except ValueError:
                return

    def publish(self, event: JarvisEvent) -> None:
        """Publish an event without allowing one handler to break the bus."""

        with self._lock:
            handlers = list(self._subscribers.get(event.event_type, []))
            handlers.extend(self._subscribers.get(None, []))

        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Event handler failed",
                    extra={
                        "event_id": event.event_id,
                        "event_type": event.event_type.value,
                        "handler": repr(handler),
                    },
                )

    def subscriber_count(
        self,
        event_type: EventType | None = None,
    ) -> int:
        """Return the number of subscribers for one event or the whole bus."""

        with self._lock:
            if event_type is not None:
                return len(self._subscribers.get(event_type, []))

            return sum(
                len(handlers)
                for handlers in self._subscribers.values()
            )

    def clear(self) -> None:
        """Remove all subscribers."""

        with self._lock:
            self._subscribers.clear()
