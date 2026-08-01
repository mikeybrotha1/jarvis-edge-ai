"""Spatial intelligence: zone matching, session lifecycle, events (v0.6.0).

Runs inside the vision/persistence process, in the same SQLAlchemy
``session_scope`` as entity/observation writes. Durable sessions open only
after enter confirmation and close only after exit confirmation, entity
close, or lost-track timeout.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import RLock
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from storage.activity_notify import ActivityNotificationPublisher
from storage.entity_zone_session_repository import EntityZoneSessionRepository
from storage.spatial_geometry import (
    GeometryError,
    normalize_bbox_point,
    point_in_rectangle,
    resolve_position_strategy,
)
from storage.zone_orm import ZoneSessionStatus
from storage.zone_records import EntityZoneSessionRecord, ZoneRecord
from storage.zone_repository import ZoneRepository


class SpatialPhase(str, Enum):
    """Transient per-(entity, zone) membership phase."""

    OUTSIDE = "outside"
    CANDIDATE_ENTER = "candidate_enter"
    INSIDE = "inside"
    CANDIDATE_EXIT = "candidate_exit"


@dataclass
class _MembershipState:
    phase: SpatialPhase = SpatialPhase.OUTSIDE
    enter_count: int = 0
    exit_count: int = 0
    open_session_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SpatialEvent:
    """One spatial timeline notification to register on the open session."""

    event_id: str
    event_type: str
    occurred_at: datetime
    zone_id: UUID
    zone_name: str
    entity_id: UUID
    camera_id: str
    entity_type: str
    session_id: UUID
    occupancy: int | None = None
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class SpatialService:
    """Evaluate zone membership and manage entity-zone sessions."""

    def __init__(
        self,
        zone_repository: ZoneRepository,
        session_repository: EntityZoneSessionRepository,
        *,
        enabled: bool = True,
        position_strategy: str = "bottom_center",
        enter_confirm_observations: int = 3,
        exit_confirm_observations: int = 3,
        lost_track_timeout_seconds: float = 15.0,
        maximum_zones_per_camera: int = 10,
        occupancy_stale_seconds: float = 60.0,
        publish_occupancy_changes: bool = True,
        camera_width: int = 1280,
        camera_height: int = 720,
        activity_publisher: ActivityNotificationPublisher | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if enter_confirm_observations < 1:
            raise ValueError("enter_confirm_observations must be >= 1")
        if exit_confirm_observations < 1:
            raise ValueError("exit_confirm_observations must be >= 1")
        if lost_track_timeout_seconds <= 0:
            raise ValueError("lost_track_timeout_seconds must be > 0")
        if maximum_zones_per_camera < 1:
            raise ValueError("maximum_zones_per_camera must be >= 1")
        if occupancy_stale_seconds <= 0:
            raise ValueError("occupancy_stale_seconds must be > 0")
        if camera_width < 1 or camera_height < 1:
            raise ValueError("camera dimensions must be positive")

        self._zones = zone_repository
        self._sessions = session_repository
        self.enabled = bool(enabled)
        self.position_strategy = str(position_strategy).strip().lower()
        self.enter_confirm_observations = int(enter_confirm_observations)
        self.exit_confirm_observations = int(exit_confirm_observations)
        self.lost_track_timeout_seconds = float(lost_track_timeout_seconds)
        self.maximum_zones_per_camera = int(maximum_zones_per_camera)
        self.occupancy_stale_seconds = float(occupancy_stale_seconds)
        self.publish_occupancy_changes = bool(publish_occupancy_changes)
        self.camera_width = int(camera_width)
        self.camera_height = int(camera_height)
        self._activity_publisher = activity_publisher
        self._logger = logger or logging.getLogger(__name__)

        self._lock = RLock()
        # Transient candidate counters: (entity_id, zone_id) -> state
        self._membership: dict[tuple[UUID, UUID], _MembershipState] = {}
        # Per-camera zone cache
        self._zone_cache: dict[str, list[ZoneRecord]] = {}
        self._zone_cache_mono: dict[str, float] = {}
        self._zone_cache_ttl = 2.0
        self._last_reconcile_mono = 0.0
        self._reconcile_interval = 1.0

    def invalidate_zone_cache(self, camera_id: str | None = None) -> None:
        """Drop cached enabled zones (call after zone create/update)."""

        with self._lock:
            if camera_id is None:
                self._zone_cache.clear()
                self._zone_cache_mono.clear()
            else:
                self._zone_cache.pop(camera_id, None)
                self._zone_cache_mono.pop(camera_id, None)

    def process_observation(
        self,
        *,
        entity_id: UUID,
        camera_id: str,
        label: str,
        confidence: float,
        bounding_box: dict[str, Any] | None,
        observed_at: datetime,
        session: Session,
        entity_closing: bool = False,
    ) -> list[SpatialEvent]:
        """Evaluate zones for one observation inside an open DB transaction.

        Returns spatial events that were registered for NOTIFY (also published
        via ``activity_publisher`` when configured).
        """

        if not self.enabled:
            return []

        events: list[SpatialEvent] = []

        # Lightweight stale reconciliation (event-driven + periodic).
        events.extend(
            self._maybe_reconcile_stale(now=observed_at, session=session)
        )

        if entity_closing:
            events.extend(
                self.force_close_entity(
                    entity_id=entity_id,
                    label=label,
                    camera_id=camera_id,
                    exited_at=observed_at,
                    session=session,
                )
            )
            return events

        if not camera_id or not bounding_box:
            return events

        zones = self._enabled_zones_for_camera(camera_id, session=session)
        if not zones:
            return events

        for zone in zones:
            if not self._zone_matches_filters(zone, label=label, confidence=confidence):
                # Filters exclude this entity: treat as outside for this zone.
                events.extend(
                    self._observe_outside(
                        zone=zone,
                        entity_id=entity_id,
                        label=label,
                        camera_id=camera_id,
                        observed_at=observed_at,
                        session=session,
                    )
                )
                continue

            try:
                strategy = resolve_position_strategy(
                    label=label,
                    zone_override=zone.position_strategy,
                    global_default=self.position_strategy,
                )
                point = normalize_bbox_point(
                    bounding_box,
                    camera_width=self.camera_width,
                    camera_height=self.camera_height,
                    strategy=strategy,
                )
                inside = point_in_rectangle(
                    point[0],
                    point[1],
                    zone.vertices,
                    inclusive=True,
                )
            except GeometryError:
                self._logger.debug(
                    "Skipping zone match for zone_id=%s (geometry error)",
                    zone.id,
                    exc_info=True,
                )
                continue

            if inside:
                events.extend(
                    self._observe_inside(
                        zone=zone,
                        entity_id=entity_id,
                        label=label,
                        camera_id=camera_id,
                        observed_at=observed_at,
                        session=session,
                    )
                )
            else:
                events.extend(
                    self._observe_outside(
                        zone=zone,
                        entity_id=entity_id,
                        label=label,
                        camera_id=camera_id,
                        observed_at=observed_at,
                        session=session,
                    )
                )

        return events

    def force_close_entity(
        self,
        *,
        entity_id: UUID,
        label: str,
        camera_id: str,
        exited_at: datetime,
        session: Session,
    ) -> list[SpatialEvent]:
        """Force-close all open zone sessions for an entity (entity close)."""

        if not self.enabled:
            return []

        events: list[SpatialEvent] = []
        open_sessions = self._sessions.list_open_for_entity(
            entity_id,
            session=session,
        )
        for open_sess in open_sessions:
            zone = self._zones.get_by_id(open_sess.zone_id, session=session)
            zone_name = zone.name if zone is not None else "unknown"
            events.extend(
                self._close_open_session(
                    open_sess,
                    zone_name=zone_name,
                    label=label,
                    exited_at=exited_at,
                    session=session,
                )
            )

        with self._lock:
            keys = [key for key in self._membership if key[0] == entity_id]
            for key in keys:
                self._membership.pop(key, None)

        return events

    def reconcile_stale_sessions(
        self,
        *,
        now: datetime | None = None,
        session: Session,
    ) -> list[SpatialEvent]:
        """Close open sessions whose last_seen_at exceeds lost-track timeout."""

        if not self.enabled:
            return []

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)

        cutoff = current - timedelta(seconds=self.lost_track_timeout_seconds)
        stale = self._sessions.list_stale_open(
            older_than=cutoff,
            limit=100,
            session=session,
        )
        events: list[SpatialEvent] = []
        for open_sess in stale:
            zone = self._zones.get_by_id(open_sess.zone_id, session=session)
            zone_name = zone.name if zone is not None else "unknown"
            events.extend(
                self._close_open_session(
                    open_sess,
                    zone_name=zone_name,
                    label="entity",
                    exited_at=current,
                    session=session,
                )
            )
            with self._lock:
                self._membership.pop(
                    (open_sess.entity_id, open_sess.zone_id),
                    None,
                )
        return events

    def _maybe_reconcile_stale(
        self,
        *,
        now: datetime,
        session: Session,
    ) -> list[SpatialEvent]:
        mono = time.monotonic()
        with self._lock:
            if mono - self._last_reconcile_mono < self._reconcile_interval:
                return []
            self._last_reconcile_mono = mono
        return self.reconcile_stale_sessions(now=now, session=session)

    def _observe_inside(
        self,
        *,
        zone: ZoneRecord,
        entity_id: UUID,
        label: str,
        camera_id: str,
        observed_at: datetime,
        session: Session,
    ) -> list[SpatialEvent]:
        key = (entity_id, zone.id)
        events: list[SpatialEvent] = []

        with self._lock:
            state = self._membership.get(key)
            if state is None:
                # Recover durable open session after process restart.
                existing = self._sessions.get_open_session(
                    zone.id,
                    entity_id,
                    session=session,
                )
                if existing is not None:
                    state = _MembershipState(
                        phase=SpatialPhase.INSIDE,
                        open_session_id=existing.id,
                    )
                else:
                    state = _MembershipState()
                self._membership[key] = state

            if state.phase in {
                SpatialPhase.OUTSIDE,
                SpatialPhase.CANDIDATE_ENTER,
            }:
                state.phase = SpatialPhase.CANDIDATE_ENTER
                state.enter_count += 1
                state.exit_count = 0
                if state.enter_count >= self.enter_confirm_observations:
                    # Confirm enter outside lock for DB work.
                    confirm_enter = True
                else:
                    confirm_enter = False
            elif state.phase is SpatialPhase.INSIDE:
                confirm_enter = False
                if state.open_session_id is not None:
                    self._sessions.touch_session(
                        state.open_session_id,
                        last_seen_at=observed_at,
                        session=session,
                    )
            elif state.phase is SpatialPhase.CANDIDATE_EXIT:
                # Re-entered before exit confirmed — cancel exit, stay inside.
                state.phase = SpatialPhase.INSIDE
                state.exit_count = 0
                confirm_enter = False
                if state.open_session_id is not None:
                    self._sessions.touch_session(
                        state.open_session_id,
                        last_seen_at=observed_at,
                        session=session,
                    )
            else:
                confirm_enter = False

        if confirm_enter:
            events.extend(
                self._confirm_enter(
                    zone=zone,
                    entity_id=entity_id,
                    label=label,
                    camera_id=camera_id,
                    entered_at=observed_at,
                    session=session,
                )
            )
        return events

    def _observe_outside(
        self,
        *,
        zone: ZoneRecord,
        entity_id: UUID,
        label: str,
        camera_id: str,
        observed_at: datetime,
        session: Session,
    ) -> list[SpatialEvent]:
        key = (entity_id, zone.id)
        events: list[SpatialEvent] = []

        with self._lock:
            state = self._membership.get(key)
            if state is None:
                existing = self._sessions.get_open_session(
                    zone.id,
                    entity_id,
                    session=session,
                )
                if existing is not None:
                    state = _MembershipState(
                        phase=SpatialPhase.INSIDE,
                        open_session_id=existing.id,
                    )
                    self._membership[key] = state
                else:
                    return []

            if state.phase in {
                SpatialPhase.OUTSIDE,
            }:
                state.enter_count = 0
                return []

            if state.phase is SpatialPhase.CANDIDATE_ENTER:
                # Lost candidate before confirmation — reset.
                state.phase = SpatialPhase.OUTSIDE
                state.enter_count = 0
                state.exit_count = 0
                return []

            if state.phase in {
                SpatialPhase.INSIDE,
                SpatialPhase.CANDIDATE_EXIT,
            }:
                state.phase = SpatialPhase.CANDIDATE_EXIT
                state.exit_count += 1
                state.enter_count = 0
                confirm_exit = state.exit_count >= self.exit_confirm_observations
            else:
                confirm_exit = False

        if confirm_exit:
            events.extend(
                self._confirm_exit(
                    zone=zone,
                    entity_id=entity_id,
                    label=label,
                    camera_id=camera_id,
                    exited_at=observed_at,
                    session=session,
                )
            )
        return events

    def _confirm_enter(
        self,
        *,
        zone: ZoneRecord,
        entity_id: UUID,
        label: str,
        camera_id: str,
        entered_at: datetime,
        session: Session,
    ) -> list[SpatialEvent]:
        key = (entity_id, zone.id)
        existing = self._sessions.get_open_session(
            zone.id,
            entity_id,
            session=session,
        )
        if existing is not None:
            with self._lock:
                state = self._membership.setdefault(key, _MembershipState())
                state.phase = SpatialPhase.INSIDE
                state.enter_count = 0
                state.exit_count = 0
                state.open_session_id = existing.id
            self._sessions.touch_session(
                existing.id,
                last_seen_at=entered_at,
                session=session,
            )
            return []

        current_occ = self._sessions.count_open_for_zone(
            zone.id,
            session=session,
        )
        occupancy_after = current_occ + 1
        opened = self._sessions.open_session(
            zone_id=zone.id,
            entity_id=entity_id,
            camera_id=camera_id,
            entered_at=entered_at,
            occupancy_after_enter=occupancy_after,
            session=session,
        )

        with self._lock:
            state = self._membership.setdefault(key, _MembershipState())
            state.phase = SpatialPhase.INSIDE
            state.enter_count = 0
            state.exit_count = 0
            state.open_session_id = opened.id

        events = self._build_enter_events(
            opened,
            zone_name=zone.name,
            label=label,
            occupancy=occupancy_after,
        )
        self._publish_events(session, events)
        return events

    def _confirm_exit(
        self,
        *,
        zone: ZoneRecord,
        entity_id: UUID,
        label: str,
        camera_id: str,
        exited_at: datetime,
        session: Session,
    ) -> list[SpatialEvent]:
        _ = camera_id
        key = (entity_id, zone.id)
        open_sess = self._sessions.get_open_session(
            zone.id,
            entity_id,
            session=session,
        )
        if open_sess is None:
            with self._lock:
                state = self._membership.setdefault(key, _MembershipState())
                state.phase = SpatialPhase.OUTSIDE
                state.enter_count = 0
                state.exit_count = 0
                state.open_session_id = None
            return []

        return self._close_open_session(
            open_sess,
            zone_name=zone.name,
            label=label,
            exited_at=exited_at,
            session=session,
        )

    def _close_open_session(
        self,
        open_sess: EntityZoneSessionRecord,
        *,
        zone_name: str,
        label: str,
        exited_at: datetime,
        session: Session,
    ) -> list[SpatialEvent]:
        current_occ = self._sessions.count_open_for_zone(
            open_sess.zone_id,
            session=session,
        )
        occupancy_after = max(0, current_occ - 1)
        closed = self._sessions.close_session(
            open_sess.id,
            exited_at=exited_at,
            occupancy_after_exit=occupancy_after,
            session=session,
        )

        key = (open_sess.entity_id, open_sess.zone_id)
        with self._lock:
            state = self._membership.setdefault(key, _MembershipState())
            state.phase = SpatialPhase.OUTSIDE
            state.enter_count = 0
            state.exit_count = 0
            state.open_session_id = None

        events = self._build_exit_events(
            closed,
            zone_name=zone_name,
            label=label,
            occupancy=occupancy_after,
        )
        self._publish_events(session, events)
        return events

    def _build_enter_events(
        self,
        sess: EntityZoneSessionRecord,
        *,
        zone_name: str,
        label: str,
        occupancy: int,
    ) -> list[SpatialEvent]:
        title = f"{label[:1].upper()}{label[1:]}" if label else "Entity"
        events = [
            SpatialEvent(
                event_id=f"zone-entered:{sess.id}",
                event_type="zone_entered",
                occurred_at=sess.entered_at,
                zone_id=sess.zone_id,
                zone_name=zone_name,
                entity_id=sess.entity_id,
                camera_id=sess.camera_id,
                entity_type=label,
                session_id=sess.id,
                occupancy=occupancy,
                summary=f"{title} entered {zone_name}",
                payload={
                    "zone_id": str(sess.zone_id),
                    "zone_name": zone_name,
                    "session_id": str(sess.id),
                    "occupancy": occupancy,
                },
            )
        ]
        if self.publish_occupancy_changes:
            events.append(
                SpatialEvent(
                    event_id=f"zone-occupancy:{sess.id}:entered",
                    event_type="zone_occupancy_changed",
                    occurred_at=sess.entered_at,
                    zone_id=sess.zone_id,
                    zone_name=zone_name,
                    entity_id=sess.entity_id,
                    camera_id=sess.camera_id,
                    entity_type=label,
                    session_id=sess.id,
                    occupancy=occupancy,
                    summary=f"{zone_name} occupancy is now {occupancy}",
                    payload={
                        "zone_id": str(sess.zone_id),
                        "zone_name": zone_name,
                        "session_id": str(sess.id),
                        "occupancy": occupancy,
                        "cause": "entered",
                    },
                )
            )
        return events

    def _build_exit_events(
        self,
        sess: EntityZoneSessionRecord,
        *,
        zone_name: str,
        label: str,
        occupancy: int,
    ) -> list[SpatialEvent]:
        occurred = sess.exited_at or sess.last_seen_at
        title = f"{label[:1].upper()}{label[1:]}" if label else "Entity"
        events = [
            SpatialEvent(
                event_id=f"zone-exited:{sess.id}",
                event_type="zone_exited",
                occurred_at=occurred,
                zone_id=sess.zone_id,
                zone_name=zone_name,
                entity_id=sess.entity_id,
                camera_id=sess.camera_id,
                entity_type=label,
                session_id=sess.id,
                occupancy=occupancy,
                summary=f"{title} exited {zone_name}",
                payload={
                    "zone_id": str(sess.zone_id),
                    "zone_name": zone_name,
                    "session_id": str(sess.id),
                    "occupancy": occupancy,
                    "dwell_seconds": sess.dwell_seconds(now=occurred),
                },
            )
        ]
        if self.publish_occupancy_changes:
            events.append(
                SpatialEvent(
                    event_id=f"zone-occupancy:{sess.id}:exited",
                    event_type="zone_occupancy_changed",
                    occurred_at=occurred,
                    zone_id=sess.zone_id,
                    zone_name=zone_name,
                    entity_id=sess.entity_id,
                    camera_id=sess.camera_id,
                    entity_type=label,
                    session_id=sess.id,
                    occupancy=occupancy,
                    summary=f"{zone_name} occupancy is now {occupancy}",
                    payload={
                        "zone_id": str(sess.zone_id),
                        "zone_name": zone_name,
                        "session_id": str(sess.id),
                        "occupancy": occupancy,
                        "cause": "exited",
                    },
                )
            )
        return events

    def _publish_events(
        self,
        session: Session,
        events: list[SpatialEvent],
    ) -> None:
        if self._activity_publisher is None:
            return
        for event in events:
            self._activity_publisher.publish_spatial_event(
                session,
                event_id=event.event_id,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
            )

    def _enabled_zones_for_camera(
        self,
        camera_id: str,
        *,
        session: Session,
    ) -> list[ZoneRecord]:
        mono = time.monotonic()
        with self._lock:
            cached = self._zone_cache.get(camera_id)
            cached_at = self._zone_cache_mono.get(camera_id, 0.0)
            if cached is not None and (mono - cached_at) < self._zone_cache_ttl:
                return list(cached)

        zones = self._zones.list_enabled_for_camera(camera_id, session=session)
        # Cap to configured maximum (oldest/first by name order from repo).
        if len(zones) > self.maximum_zones_per_camera:
            zones = zones[: self.maximum_zones_per_camera]

        with self._lock:
            self._zone_cache[camera_id] = list(zones)
            self._zone_cache_mono[camera_id] = mono
        return zones

    @staticmethod
    def _zone_matches_filters(
        zone: ZoneRecord,
        *,
        label: str,
        confidence: float,
    ) -> bool:
        if zone.entity_type_filters:
            allowed = {item.lower() for item in zone.entity_type_filters}
            if label.lower() not in allowed:
                return False
        if zone.min_confidence is not None and confidence < zone.min_confidence:
            return False
        return True
