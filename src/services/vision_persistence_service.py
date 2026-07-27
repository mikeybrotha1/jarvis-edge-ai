"""Persistent storage service for Jarvis vision activity.

Purpose
-------
Connect Jarvis object-lifecycle events to the PostgreSQL repository.

Responsibilities
----------------
- Create one vision-run record when the service starts.
- Hold the active run identifier.
- Subscribe to object lifecycle events.
- Unsubscribe cleanly when stopped.
- Delegate all database operations to VisionRepository.

Non-responsibilities
--------------------
- Executing SQL directly.
- Detecting or tracking objects.
- Assigning identities.
- Rendering detections.
- Calculating identity-session history.

Identity-event persistence is intentionally added in the next commit.
"""

from __future__ import annotations

import logging
import socket
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

from core.event_bus import EventBus
from core.events import EventType, JarvisEvent
from storage.models import VisionRunRecord
from storage.repository import VisionRepository


class VisionPersistenceService:
    """Manage persistent storage for one Jarvis vision run."""

    _EVENT_TYPES = (
        EventType.OBJECT_ENTERED,
        EventType.OBJECT_UPDATED,
        EventType.OBJECT_EXITED,
    )

    def __init__(
        self,
        event_bus: EventBus,
        repository: VisionRepository,
        *,
        camera_source: str = "azure_kinect",
        hostname: str | None = None,
        metadata: dict[str, Any] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._repository = repository
        self._camera_source = camera_source
        self._hostname = hostname or socket.gethostname()
        self._metadata = dict(metadata or {})
        self._logger = logger or logging.getLogger(__name__)

        self._run_id: UUID | None = None
        self._running = False
        self._lock = RLock()

    @property
    def run_id(self) -> UUID | None:
        """Return the active run identifier, if the service has started."""

        with self._lock:
            return self._run_id

    @property
    def is_running(self) -> bool:
        """Return whether the service is currently subscribed."""

        with self._lock:
            return self._running

    def start(self) -> UUID:
        """Create a vision run and subscribe to lifecycle events.

        Calling start more than once is safe. When already running, the
        existing run identifier is returned and no duplicate row is created.
        """

        with self._lock:
            if self._running:
                if self._run_id is None:
                    raise RuntimeError(
                        "Persistence service is running without a run_id."
                    )

                return self._run_id

            run_id = uuid4()

            self._repository.create_run(
                VisionRunRecord(
                    run_id=run_id,
                    hostname=self._hostname,
                    camera_source=self._camera_source,
                    metadata=self._metadata,
                )
            )

            subscribed: list[EventType] = []

            try:
                for event_type in self._EVENT_TYPES:
                    self._event_bus.subscribe(
                        event_type,
                        self._handle_object_event,
                    )
                    subscribed.append(event_type)
            except Exception:
                for event_type in reversed(subscribed):
                    self._event_bus.unsubscribe(
                        event_type,
                        self._handle_object_event,
                    )

                raise

            self._run_id = run_id
            self._running = True

            self._logger.info(
                "Vision persistence started; run_id=%s",
                run_id,
            )

            return run_id

    def stop(self) -> None:
        """Unsubscribe without finishing the database run yet.

        Run finalisation will be introduced with frame counts and shutdown
        status in the run-lifecycle commit.
        """

        with self._lock:
            if not self._running:
                return

            for event_type in self._EVENT_TYPES:
                self._event_bus.unsubscribe(
                    event_type,
                    self._handle_object_event,
                )

            run_id = self._run_id
            self._running = False

            self._logger.info(
                "Vision persistence stopped; run_id=%s",
                run_id,
            )

    def _handle_object_event(self, event: JarvisEvent) -> None:
        """Receive object events before persistence is added next commit."""

        if event.event_type not in self._EVENT_TYPES:
            return

        self._logger.debug(
            "Persistence received %s for identity=%s",
            event.event_type.value,
            event.data.get("identity", "unknown"),
        )
