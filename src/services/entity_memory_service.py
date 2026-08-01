"""Persistent entity memory service for Jarvis Edge AI.

Purpose
-------
Subscribe to short-term object lifecycle events and maintain durable entity
state (entities, observations, snapshots) while publishing ENTITY_* events.

Architecture notes
------------------
- Follows the same start/stop/subscribe lifecycle as IdentityHistoryService and
  VisionPersistenceService.
- Database work runs on a background worker so event-bus publish stays
  non-blocking (unlike pure in-memory handlers).
- Each vision event is processed in a single repository transaction so partial
  writes roll back on failure.
- Identity resolution is delegated to an IdentityMatcher so camera-scoped
  tracker matching can later be replaced without changing consumers.
- Closed entities are never reopened: a later OBJECT_ENTERED creates a new
  entity row. Future matchers may associate multiple rows with one object.
"""

from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import UUID

from core.event_bus import EventBus
from core.events import EventType, JarvisEvent
from core.identity import (
    IdentityMatch,
    IdentityMatcher,
    ObservationContext,
    TrackerIdIdentityMatcher,
)
from sqlalchemy.orm import Session, sessionmaker
from storage.entity_orm import EntityStatus
from storage.entity_records import (
    EntityCreate,
    EntityRecord,
    EntityUpdate,
    ObservationCreate,
    ObservationRecord,
)
from storage.activity_notify import ActivityNotificationPublisher
from storage.entity_repository import EntityRepository
from storage.observation_repository import ObservationRepository
from storage.sqlalchemy_db import session_scope


class EntityMemoryService:
    """Persist tracked objects as long-lived entities."""

    _OBJECT_EVENTS = (
        EventType.OBJECT_ENTERED,
        EventType.OBJECT_UPDATED,
        EventType.OBJECT_EXITED,
    )

    def __init__(
        self,
        event_bus: EventBus,
        entity_repository: EntityRepository,
        observation_repository: ObservationRepository,
        *,
        session_factory: sessionmaker[Session] | None = None,
        identity_matcher: IdentityMatcher | None = None,
        camera_id: str = "azure_kinect",
        source: str = "entity_memory_service",
        process_inline: bool = False,
        snapshot_min_interval_seconds: float = 0.0,
        snapshot_on_update: bool = True,
        activity_publisher: ActivityNotificationPublisher | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if snapshot_min_interval_seconds < 0:
            raise ValueError(
                "snapshot_min_interval_seconds cannot be negative"
            )

        self._event_bus = event_bus
        self._entities = entity_repository
        self._observations = observation_repository
        self._session_factory = session_factory
        self._identity_matcher = (
            identity_matcher or TrackerIdIdentityMatcher()
        )
        self._camera_id = camera_id
        self._source = source
        # process_inline=True keeps unit tests deterministic without a worker.
        self._process_inline = process_inline
        self._snapshot_min_interval_seconds = float(
            snapshot_min_interval_seconds
        )
        self._snapshot_on_update = bool(snapshot_on_update)
        self._activity_publisher = activity_publisher
        self._logger = logger or logging.getLogger(__name__)

        # Last intermediate snapshot time per entity (throttling).
        self._last_update_snapshot_at: dict[UUID, datetime] = {}

        self._lock = RLock()
        self._running = False
        self._queue: queue.Queue[JarvisEvent | None] = queue.Queue()
        self._worker: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        """Return whether the service is currently subscribed."""

        with self._lock:
            return self._running

    @property
    def identity_strategy(self) -> str:
        """Return the active identity matching strategy name."""

        return self._identity_matcher.strategy_name

    def start(self) -> None:
        """Subscribe to object lifecycle events and start the worker."""

        with self._lock:
            if self._running:
                return

            for event_type in self._OBJECT_EVENTS:
                self._event_bus.subscribe(
                    event_type,
                    self.handle_object_event,
                )

            if not self._process_inline:
                self._worker = threading.Thread(
                    target=self._worker_loop,
                    name="entity-memory-worker",
                    daemon=True,
                )
                self._worker.start()

            self._running = True
            self._logger.info(
                "Entity memory started; strategy=%s camera_id=%s inline=%s",
                self.identity_strategy,
                self._camera_id,
                self._process_inline,
            )

    def stop(self) -> None:
        """Unsubscribe, drain the worker queue, and stop cleanly."""

        with self._lock:
            if not self._running:
                return

            for event_type in self._OBJECT_EVENTS:
                self._event_bus.unsubscribe(
                    event_type,
                    self.handle_object_event,
                )

            self._running = False

        if not self._process_inline:
            self._queue.put(None)
            worker = self._worker
            if worker is not None and worker.is_alive():
                worker.join(timeout=5.0)
            self._worker = None

        self._logger.info("Entity memory stopped")

    def flush(self, timeout: float = 5.0) -> None:
        """Block until queued events are processed (tests / graceful drain)."""

        if self._process_inline:
            return

        deadline = threading.Event()
        # Sentinel: after existing items, worker sets the event via a marker
        # by processing a no-op wait using queue join semantics.
        self._queue.join()
        if timeout <= 0:
            return
        # join() already waited; timeout kept for API stability.
        _ = deadline
        _ = timeout

    def handle_object_event(self, event: JarvisEvent) -> None:
        """Enqueue or process one object lifecycle event without blocking long.

        When ``process_inline`` is False the handler only queues the event so
        the event bus publish path stays free of database I/O.
        """

        if event.event_type not in self._OBJECT_EVENTS:
            return

        if self._process_inline:
            self._process_event(event)
            return

        self._queue.put(event)

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                self._process_event(item)
            finally:
                self._queue.task_done()

    def _process_event(self, event: JarvisEvent) -> None:
        if self._session_factory is None:
            # Fallback path used by pure unit tests with fake repositories.
            self._process_without_shared_session(event)
            return

        try:
            with session_scope(self._session_factory) as session:
                self._process_with_session(event, session)
        except Exception:
            self._logger.exception(
                "Entity memory transaction rolled back for event_id=%s",
                event.event_id,
            )
            # Re-raise in inline/test mode so callers can assert failures.
            # Background worker path swallows after logging to keep the queue
            # draining.
            if self._process_inline:
                raise

    def _process_without_shared_session(self, event: JarvisEvent) -> None:
        if event.event_type is EventType.OBJECT_ENTERED:
            self._handle_entered(event, session=None)
        elif event.event_type is EventType.OBJECT_UPDATED:
            self._handle_updated(event, session=None)
        elif event.event_type is EventType.OBJECT_EXITED:
            self._handle_exited(event, session=None)

    def _process_with_session(
        self,
        event: JarvisEvent,
        session: Session,
    ) -> None:
        if self._observations.has_source_event(
            event.event_id,
            session=session,
        ):
            self._logger.debug(
                "Skipping duplicate vision event_id=%s",
                event.event_id,
            )
            return

        if event.event_type is EventType.OBJECT_ENTERED:
            self._handle_entered(event, session=session)
        elif event.event_type is EventType.OBJECT_UPDATED:
            self._handle_updated(event, session=session)
        elif event.event_type is EventType.OBJECT_EXITED:
            self._handle_exited(event, session=session)

    def _handle_entered(
        self,
        event: JarvisEvent,
        *,
        session: Session | None,
    ) -> None:
        observation = self._extract_observation(event)
        identity = self._match_identity(observation)

        existing = self._entities.get_active_by_identity_key(
            identity.identity_key,
            session=session,
        )

        if existing is None:
            # Never reopen a CLOSED entity. A new appearance becomes a new
            # row; future identity layers may link rows to one real object.
            entity = self._entities.create(
                self._entity_create(identity, observation),
                session=session,
            )
            created = True
        else:
            entity = self._entities.apply_observation(
                existing.id,
                self._entity_update(observation),
                session=session,
            )
            created = False

        self._finalise(
            entity,
            observation,
            event=event,
            identity=identity,
            created=created,
            closing=False,
            session=session,
        )

    def _handle_updated(
        self,
        event: JarvisEvent,
        *,
        session: Session | None,
    ) -> None:
        observation = self._extract_observation(event)
        identity = self._match_identity(observation)

        entity = self._entities.get_active_by_identity_key(
            identity.identity_key,
            session=session,
        )

        if entity is None:
            # Out-of-order / mid-stream: do not reopen closed entities on
            # update (avoids corrupting a completed lifecycle). Create only
            # when this identity has never been seen.
            latest = self._entities.get_latest_by_identity_key(
                identity.identity_key,
                session=session,
            )
            if latest is not None and latest.status is EntityStatus.CLOSED:
                self._logger.debug(
                    "Ignoring late update for closed identity_key=%s",
                    identity.identity_key,
                )
                # Still record the observation against the closed entity so
                # history is complete without mutating aggregate counters.
                self._record_observation(
                    latest,
                    observation,
                    source_event_type=event.event_type.value,
                    identity=identity,
                    parent_event=event,
                    session=session,
                )
                return

            entity = self._entities.create(
                self._entity_create(
                    identity,
                    observation,
                    extra={
                        "source_identity": observation.get("source_identity"),
                        "bootstrapped_from": event.event_type.value,
                    },
                ),
                session=session,
            )
            created = True
        else:
            entity = self._entities.apply_observation(
                entity.id,
                self._entity_update(observation),
                session=session,
            )
            created = False

        self._finalise(
            entity,
            observation,
            event=event,
            identity=identity,
            created=created,
            closing=False,
            session=session,
        )

    def _handle_exited(
        self,
        event: JarvisEvent,
        *,
        session: Session | None,
    ) -> None:
        observation = self._extract_observation(event)
        identity = self._match_identity(observation)

        entity = self._entities.get_active_by_identity_key(
            identity.identity_key,
            session=session,
        )

        if entity is None:
            latest = self._entities.get_latest_by_identity_key(
                identity.identity_key,
                session=session,
            )
            if latest is None:
                entity = self._entities.create(
                    self._entity_create(
                        identity,
                        observation,
                        extra={
                            "source_identity": observation.get(
                                "source_identity"
                            ),
                            "bootstrapped_from": event.event_type.value,
                        },
                    ),
                    session=session,
                )
            elif latest.status is EntityStatus.CLOSED:
                # Duplicate or late exit for already-closed entity: record
                # observation only, do not re-count or re-close.
                self._record_observation(
                    latest,
                    observation,
                    source_event_type=event.event_type.value,
                    identity=identity,
                    parent_event=event,
                    session=session,
                )
                return
            else:
                entity = self._entities.apply_observation(
                    latest.id,
                    self._entity_update(observation),
                    session=session,
                )
        else:
            entity = self._entities.apply_observation(
                entity.id,
                self._entity_update(observation),
                session=session,
            )

        self._finalise(
            entity,
            observation,
            event=event,
            identity=identity,
            created=False,
            closing=True,
            session=session,
        )

    def _finalise(
        self,
        entity: EntityRecord,
        observation: dict[str, Any],
        *,
        event: JarvisEvent,
        identity: IdentityMatch,
        created: bool,
        closing: bool,
        session: Session | None,
    ) -> None:
        # Capture first_seen before close mutates aggregate state.
        created_at = entity.first_seen

        obs_record = self._record_observation(
            entity,
            observation,
            source_event_type=event.event_type.value,
            identity=identity,
            parent_event=event,
            session=session,
        )

        if closing:
            entity = self._entities.close(
                entity.id,
                last_seen=observation["observed_at"],
                bounding_box=observation["bounding_box"],
                session=session,
            )
            reason = "closed"
            event_type = EventType.ENTITY_CLOSED
        elif created:
            reason = "created"
            event_type = EventType.ENTITY_CREATED
        else:
            reason = "updated"
            event_type = EventType.ENTITY_UPDATED

        if self._should_write_snapshot(
            entity_id=entity.id,
            reason=reason,
            observed_at=observation["observed_at"],
        ):
            self._entities.create_snapshot(
                entity,
                reason=reason,
                snapshot_at=observation["observed_at"],
                session=session,
            )
            if reason == "updated":
                self._last_update_snapshot_at[entity.id] = observation[
                    "observed_at"
                ]
            elif reason == "closed":
                self._last_update_snapshot_at.pop(entity.id, None)

        # Live activity notifications (same transaction as durable writes).
        if session is not None and self._activity_publisher is not None:
            if created:
                self._activity_publisher.publish_entity_created(
                    session,
                    entity_id=entity.id,
                    occurred_at=created_at,
                )
            if closing:
                self._activity_publisher.publish_entity_closed(
                    session,
                    entity_id=entity.id,
                    occurred_at=entity.last_seen,
                )
            if obs_record is not None:
                self._activity_publisher.publish_observation_recorded(
                    session,
                    observation_id=obs_record.id,
                    entity_id=entity.id,
                    occurred_at=obs_record.observed_at,
                )

        self._publish_entity_event(
            event_type,
            entity,
            parent_event=event,
            identity=identity,
        )

    def _should_write_snapshot(
        self,
        *,
        entity_id: UUID,
        reason: str,
        observed_at: datetime,
    ) -> bool:
        """Apply snapshot config (create/close always; updates may throttle)."""

        if reason in {"created", "closed"}:
            return True

        if reason != "updated":
            return True

        if not self._snapshot_on_update:
            return False

        if self._snapshot_min_interval_seconds <= 0:
            return True

        previous = self._last_update_snapshot_at.get(entity_id)
        if previous is None:
            return True

        elapsed = (observed_at - previous).total_seconds()
        return elapsed >= self._snapshot_min_interval_seconds

    def _record_observation(
        self,
        entity: EntityRecord,
        observation: dict[str, Any],
        *,
        source_event_type: str,
        identity: IdentityMatch,
        parent_event: JarvisEvent,
        session: Session | None,
    ) -> ObservationRecord | None:
        record, created = self._observations.append(
            ObservationCreate(
                entity_id=entity.id,
                observed_at=observation["observed_at"],
                camera_id=observation["camera_id"],
                confidence=observation["confidence"],
                label=observation["label"],
                source_event_type=source_event_type,
                bounding_box=observation["bounding_box"],
                frame_number=observation["frame_number"],
                track_id=observation["track_id"],
                source_event_id=parent_event.event_id,
                payload={
                    "parent_event_id": parent_event.event_id,
                    "source_identity": observation.get("source_identity"),
                    "identity_key": identity.identity_key,
                    "identity_strategy": identity.strategy,
                    "frames_seen": observation.get("frames_seen"),
                },
            ),
            session=session,
        )
        return record if created else None

    def _entity_create(
        self,
        identity: IdentityMatch,
        observation: dict[str, Any],
        *,
        extra: dict[str, Any] | None = None,
    ) -> EntityCreate:
        return EntityCreate(
            identity_key=identity.identity_key,
            identity_strategy=identity.strategy,
            label=observation["label"],
            track_id=observation["track_id"],
            camera_id=observation["camera_id"],
            first_seen=observation["observed_at"],
            last_seen=observation["observed_at"],
            confidence=observation["confidence"],
            bounding_box=observation["bounding_box"],
            extra=extra
            or {
                "source_identity": observation.get("source_identity"),
            },
        )

    @staticmethod
    def _entity_update(observation: dict[str, Any]) -> EntityUpdate:
        return EntityUpdate(
            last_seen=observation["observed_at"],
            confidence=observation["confidence"],
            label=observation["label"],
            track_id=observation["track_id"],
            camera_id=observation["camera_id"],
            bounding_box=observation["bounding_box"],
            reopen=False,
        )

    def _publish_entity_event(
        self,
        event_type: EventType,
        entity: EntityRecord,
        *,
        parent_event: JarvisEvent,
        identity: IdentityMatch,
    ) -> None:
        payload = entity.to_event_data()
        payload.update(
            {
                "parent_event_id": parent_event.event_id,
                "parent_event_type": parent_event.event_type.value,
                "identity_strategy": identity.strategy,
            }
        )

        self._event_bus.publish(
            JarvisEvent.create(
                event_type,
                source=self._source,
                **payload,
            )
        )

        self._logger.debug(
            "%s entity_id=%s identity_key=%s times_seen=%s status=%s",
            event_type.value,
            entity.id,
            entity.identity_key,
            entity.times_seen,
            entity.status.value,
        )

    def _match_identity(
        self,
        observation: dict[str, Any],
    ) -> IdentityMatch:
        context = ObservationContext(
            track_id=observation["track_id"],
            label=observation["label"],
            confidence=observation["confidence"],
            bounding_box=observation["bounding_box"],
            camera_id=observation["camera_id"],
            frame_number=observation["frame_number"],
            extra={
                "source_identity": observation.get("source_identity"),
                "frames_seen": observation.get("frames_seen"),
            },
        )
        return self._identity_matcher.match(context)

    def _extract_observation(self, event: JarvisEvent) -> dict[str, Any]:
        track_id = self._required_int(event, "track_id")
        label = self._required_string(event, "label")
        confidence = self._required_float(event, "confidence")

        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

        camera_id = str(
            event.data.get("camera_id")
            or event.data.get("frame_source")
            or self._camera_id
        )

        frame_number = event.data.get("frame_id")
        if frame_number is not None:
            frame_number = int(frame_number)

        bounding_box = event.data.get("bounding_box")
        if bounding_box is not None and not isinstance(bounding_box, dict):
            raise TypeError("bounding_box must be a dictionary when present")

        observed_at = self._parse_timestamp(
            event.data.get("last_seen") or event.timestamp
        )

        frames_seen = event.data.get("frames_seen")
        if frames_seen is not None:
            frames_seen = int(frames_seen)

        return {
            "track_id": track_id,
            "label": label,
            "confidence": confidence,
            "camera_id": camera_id,
            "frame_number": frame_number,
            "bounding_box": bounding_box,
            "observed_at": observed_at,
            "source_identity": event.data.get("identity"),
            "frames_seen": frames_seen,
        }

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value

        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _required_string(event: JarvisEvent, field_name: str) -> str:
        value = event.data.get(field_name)
        if value is None:
            raise ValueError(f"Object event is missing {field_name!r}")
        text = str(value).strip()
        if not text:
            raise ValueError(f"Object event field {field_name!r} is empty")
        return text

    @staticmethod
    def _required_int(event: JarvisEvent, field_name: str) -> int:
        value = event.data.get(field_name)
        if value is None:
            raise ValueError(f"Object event is missing {field_name!r}")
        return int(value)

    @staticmethod
    def _required_float(event: JarvisEvent, field_name: str) -> float:
        value = event.data.get(field_name)
        if value is None:
            raise ValueError(f"Object event is missing {field_name!r}")
        return float(value)
