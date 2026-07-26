"""Long-term in-memory identity history for Jarvis Edge AI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import RLock
from typing import Any

from core.event_bus import EventBus
from core.events import EventType, JarvisEvent


@dataclass(slots=True)
class IdentityHistory:
    """Historical summary for one tracked identity."""

    identity: str
    label: str
    track_id: int
    first_seen: str
    last_seen: str
    appearance_count: int
    total_frames_seen: int
    highest_confidence: float
    last_confidence: float
    active: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a serialisable snapshot of this history record."""

        return asdict(self)


class IdentityHistoryService:
    """Build identity history from object lifecycle events.

    The service subscribes to:

    - OBJECT_ENTERED
    - OBJECT_UPDATED
    - OBJECT_EXITED

    History remains available after an object exits. This initial version is
    stored in memory only and does not persist across application restarts.
    """

    _EVENT_TYPES = (
        EventType.OBJECT_ENTERED,
        EventType.OBJECT_UPDATED,
        EventType.OBJECT_EXITED,
    )

    def __init__(self, event_bus: EventBus) -> None:
        self.event_bus = event_bus

        self._histories: dict[str, IdentityHistory] = {}

        # Latest cumulative frames_seen value for the current appearance.
        # This lets the service add frame deltas without double-counting.
        self._session_frames: dict[str, int] = {}

        self._lock = RLock()
        self._running = False

    def start(self) -> None:
        """Subscribe to object lifecycle events."""

        with self._lock:
            if self._running:
                return

            for event_type in self._EVENT_TYPES:
                self.event_bus.subscribe(
                    event_type,
                    self.handle_object_event,
                )

            self._running = True

    def stop(self) -> None:
        """Unsubscribe without deleting accumulated history."""

        with self._lock:
            if not self._running:
                return

            for event_type in self._EVENT_TYPES:
                self.event_bus.unsubscribe(
                    event_type,
                    self.handle_object_event,
                )

            self._running = False

    def handle_object_event(self, event: JarvisEvent) -> None:
        """Update identity history from one lifecycle event."""

        if event.event_type not in self._EVENT_TYPES:
            return

        identity = self._required_string(event, "identity")
        label = self._required_string(event, "label")
        track_id = self._required_int(event, "track_id")
        confidence = self._required_float(event, "confidence")
        frames_seen = self._required_int(event, "frames_seen")

        if frames_seen < 0:
            raise ValueError("frames_seen cannot be negative")

        first_seen = str(event.data.get("first_seen", event.timestamp))
        last_seen = str(event.data.get("last_seen", event.timestamp))

        with self._lock:
            history = self._histories.get(identity)

            if event.event_type is EventType.OBJECT_ENTERED:
                self._handle_entered(
                    identity=identity,
                    label=label,
                    track_id=track_id,
                    confidence=confidence,
                    frames_seen=frames_seen,
                    first_seen=first_seen,
                    last_seen=last_seen,
                    history=history,
                )
                return

            if history is None:
                # Be resilient if the service starts after an object has
                # already entered and first sees an update or exit event.
                history = IdentityHistory(
                    identity=identity,
                    label=label,
                    track_id=track_id,
                    first_seen=first_seen,
                    last_seen=last_seen,
                    appearance_count=1,
                    total_frames_seen=frames_seen,
                    highest_confidence=confidence,
                    last_confidence=confidence,
                    active=event.event_type is EventType.OBJECT_UPDATED,
                )
                self._histories[identity] = history
                self._session_frames[identity] = frames_seen
            else:
                self._update_existing(
                    history,
                    confidence=confidence,
                    frames_seen=frames_seen,
                    last_seen=last_seen,
                )

            if event.event_type is EventType.OBJECT_EXITED:
                history.active = False
                self._session_frames.pop(identity, None)
            else:
                history.active = True

    def history_for(
        self,
        identity: str,
    ) -> dict[str, Any] | None:
        """Return one history snapshot, or None when unknown."""

        with self._lock:
            history = self._histories.get(identity)

            if history is None:
                return None

            return history.to_dict()

    def all_histories(self) -> list[dict[str, Any]]:
        """Return all identity histories ordered by track ID."""

        with self._lock:
            return [
                history.to_dict()
                for history in sorted(
                    self._histories.values(),
                    key=lambda item: item.track_id,
                )
            ]

    def active_histories(self) -> list[dict[str, Any]]:
        """Return histories for identities that are currently active."""

        with self._lock:
            return [
                history.to_dict()
                for history in sorted(
                    self._histories.values(),
                    key=lambda item: item.track_id,
                )
                if history.active
            ]

    def history_count(
        self,
        label: str | None = None,
    ) -> int:
        """Return the number of known identities, optionally by label."""

        with self._lock:
            if label is None:
                return len(self._histories)

            return sum(
                1
                for history in self._histories.values()
                if history.label == label
            )

    def clear(self) -> None:
        """Delete all accumulated identity history."""

        with self._lock:
            self._histories.clear()
            self._session_frames.clear()

    def _handle_entered(
        self,
        *,
        identity: str,
        label: str,
        track_id: int,
        confidence: float,
        frames_seen: int,
        first_seen: str,
        last_seen: str,
        history: IdentityHistory | None,
    ) -> None:
        if history is None:
            self._histories[identity] = IdentityHistory(
                identity=identity,
                label=label,
                track_id=track_id,
                first_seen=first_seen,
                last_seen=last_seen,
                appearance_count=1,
                total_frames_seen=frames_seen,
                highest_confidence=confidence,
                last_confidence=confidence,
                active=True,
            )
        else:
            history.label = label
            history.track_id = track_id
            history.last_seen = last_seen
            history.appearance_count += 1
            history.total_frames_seen += frames_seen
            history.highest_confidence = max(
                history.highest_confidence,
                confidence,
            )
            history.last_confidence = confidence
            history.active = True

        self._session_frames[identity] = frames_seen

    def _update_existing(
        self,
        history: IdentityHistory,
        *,
        confidence: float,
        frames_seen: int,
        last_seen: str,
    ) -> None:
        previous_frames = self._session_frames.get(
            history.identity,
            0,
        )
        frame_delta = max(0, frames_seen - previous_frames)

        history.total_frames_seen += frame_delta
        history.last_seen = last_seen
        history.highest_confidence = max(
            history.highest_confidence,
            confidence,
        )
        history.last_confidence = confidence

        self._session_frames[history.identity] = frames_seen

    @staticmethod
    def _required_string(
        event: JarvisEvent,
        field_name: str,
    ) -> str:
        value = event.data.get(field_name)

        if value is None:
            raise ValueError(
                f"Object event is missing {field_name!r}"
            )

        return str(value)

    @staticmethod
    def _required_int(
        event: JarvisEvent,
        field_name: str,
    ) -> int:
        value = event.data.get(field_name)

        if value is None:
            raise ValueError(
                f"Object event is missing {field_name!r}"
            )

        return int(value)

    @staticmethod
    def _required_float(
        event: JarvisEvent,
        field_name: str,
    ) -> float:
        value = event.data.get(field_name)

        if value is None:
            raise ValueError(
                f"Object event is missing {field_name!r}"
            )

        return float(value)
