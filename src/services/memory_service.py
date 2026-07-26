"""Short-term object memory and identity tracking for Jarvis Edge AI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import RLock
from typing import Any

from core.event_bus import EventBus
from core.events import EventType, JarvisEvent


@dataclass(slots=True)
class BoundingBox:
    """Pixel-space object bounding box."""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def area(self) -> int:
        width = max(0, self.x2 - self.x1)
        height = max(0, self.y2 - self.y1)
        return width * height


@dataclass(slots=True)
class TrackedObject:
    """Current state of an object remembered across frames."""

    track_id: int
    label: str
    confidence: float
    bounding_box: BoundingBox
    first_seen: str
    last_seen: str
    frames_seen: int = 1
    missed_frames: int = 0

    @property
    def identity(self) -> str:
        return f"{self.label}-{self.track_id}"

    def to_dict(self) -> dict[str, Any]:
        record = asdict(self)
        record["identity"] = self.identity
        return record


class MemoryService:
    """Maintain persistent identities for detected objects.

    The service subscribes to FRAME_PROCESSED events containing a ``detections``
    list. Objects are matched by label and intersection-over-union (IoU).

    Expected event data:

        {
            "frame_id": 42,
            "detections": [
                {
                    "label": "person",
                    "confidence": 0.94,
                    "bounding_box": {
                        "x1": 100,
                        "y1": 80,
                        "x2": 420,
                        "y2": 690
                    }
                }
            ]
        }
    """

    def __init__(
        self,
        event_bus: EventBus,
        *,
        source: str = "memory_service",
        iou_threshold: float = 0.30,
        max_missed_frames: int = 8,
    ) -> None:
        if not 0.0 <= iou_threshold <= 1.0:
            raise ValueError("iou_threshold must be between 0 and 1")

        if max_missed_frames < 0:
            raise ValueError("max_missed_frames cannot be negative")

        self.event_bus = event_bus
        self.source = source
        self.iou_threshold = iou_threshold
        self.max_missed_frames = max_missed_frames

        self._tracks: dict[int, TrackedObject] = {}
        self._next_track_id = 1
        self._lock = RLock()
        self._running = False

    def start(self) -> None:
        """Subscribe to vision frame events."""

        with self._lock:
            if self._running:
                return

            self.event_bus.subscribe(
                EventType.FRAME_PROCESSED,
                self.handle_frame,
            )
            self._running = True

    def stop(self) -> None:
        """Unsubscribe without deleting remembered state."""

        with self._lock:
            if not self._running:
                return

            self.event_bus.unsubscribe(
                EventType.FRAME_PROCESSED,
                self.handle_frame,
            )
            self._running = False

    def handle_frame(self, event: JarvisEvent) -> None:
        """Update tracked-object memory from one processed frame."""

        raw_detections = event.data.get("detections")

        # FRAME_PROCESSED may also be used for performance-only events.
        if raw_detections is None:
            return

        if not isinstance(raw_detections, list):
            raise TypeError("event.data['detections'] must be a list")

        detections = [
            self._normalise_detection(item)
            for item in raw_detections
        ]

        with self._lock:
            matched_track_ids: set[int] = set()
            matched_detection_indexes: set[int] = set()

            candidates: list[tuple[float, int, int]] = []

            for track_id, track in self._tracks.items():
                for detection_index, detection in enumerate(detections):
                    if track.label != detection["label"]:
                        continue

                    overlap = self._intersection_over_union(
                        track.bounding_box,
                        detection["bounding_box"],
                    )

                    if overlap >= self.iou_threshold:
                        candidates.append(
                            (overlap, track_id, detection_index)
                        )

            # Greedy best-overlap matching.
            candidates.sort(reverse=True, key=lambda item: item[0])

            for _, track_id, detection_index in candidates:
                if track_id in matched_track_ids:
                    continue

                if detection_index in matched_detection_indexes:
                    continue

                detection = detections[detection_index]
                track = self._tracks[track_id]

                track.confidence = detection["confidence"]
                track.bounding_box = detection["bounding_box"]
                track.last_seen = event.timestamp
                track.frames_seen += 1
                track.missed_frames = 0

                matched_track_ids.add(track_id)
                matched_detection_indexes.add(detection_index)

                self._publish_track_event(
                    EventType.OBJECT_UPDATED,
                    track,
                    parent_event=event,
                )

            # Existing tracks not observed in this frame become temporarily
            # missing. They are retained for max_missed_frames.
            expired_track_ids: list[int] = []

            for track_id, track in self._tracks.items():
                if track_id in matched_track_ids:
                    continue

                track.missed_frames += 1

                if track.missed_frames > self.max_missed_frames:
                    expired_track_ids.append(track_id)

            for track_id in expired_track_ids:
                track = self._tracks.pop(track_id)

                self._publish_track_event(
                    EventType.OBJECT_EXITED,
                    track,
                    parent_event=event,
                )

            # Any unmatched detection represents a new object.
            for detection_index, detection in enumerate(detections):
                if detection_index in matched_detection_indexes:
                    continue

                track = TrackedObject(
                    track_id=self._next_track_id,
                    label=detection["label"],
                    confidence=detection["confidence"],
                    bounding_box=detection["bounding_box"],
                    first_seen=event.timestamp,
                    last_seen=event.timestamp,
                )

                self._tracks[track.track_id] = track
                self._next_track_id += 1

                self._publish_track_event(
                    EventType.OBJECT_ENTERED,
                    track,
                    parent_event=event,
                )

    def active_objects(self) -> list[dict[str, Any]]:
        """Return a serialisable snapshot of remembered active objects."""

        with self._lock:
            return [
                track.to_dict()
                for track in sorted(
                    self._tracks.values(),
                    key=lambda item: item.track_id,
                )
            ]

    def object_count(self, label: str | None = None) -> int:
        """Return the number of active objects, optionally filtered by label."""

        with self._lock:
            if label is None:
                return len(self._tracks)

            return sum(
                1
                for track in self._tracks.values()
                if track.label == label
            )

    def clear(self) -> None:
        """Delete all short-term object memory."""

        with self._lock:
            self._tracks.clear()

    def _publish_track_event(
        self,
        event_type: EventType,
        track: TrackedObject,
        *,
        parent_event: JarvisEvent,
    ) -> None:
        self.event_bus.publish(
            JarvisEvent.create(
                event_type,
                source=self.source,
                frame_source=parent_event.source,
                parent_event_id=parent_event.event_id,
                frame_id=parent_event.data.get("frame_id"),
                **track.to_dict(),
            )
        )

    @staticmethod
    def _normalise_detection(
        detection: Any,
    ) -> dict[str, Any]:
        if not isinstance(detection, dict):
            raise TypeError("Each detection must be a dictionary")

        label = str(detection["label"])
        confidence = float(detection["confidence"])

        raw_box = detection["bounding_box"]

        if not isinstance(raw_box, dict):
            raise TypeError("bounding_box must be a dictionary")

        box = BoundingBox(
            x1=int(raw_box["x1"]),
            y1=int(raw_box["y1"]),
            x2=int(raw_box["x2"]),
            y2=int(raw_box["y2"]),
        )

        if box.x2 <= box.x1 or box.y2 <= box.y1:
            raise ValueError("Detection bounding box has invalid dimensions")

        return {
            "label": label,
            "confidence": confidence,
            "bounding_box": box,
        }

    @staticmethod
    def _intersection_over_union(
        first: BoundingBox,
        second: BoundingBox,
    ) -> float:
        intersection_x1 = max(first.x1, second.x1)
        intersection_y1 = max(first.y1, second.y1)
        intersection_x2 = min(first.x2, second.x2)
        intersection_y2 = min(first.y2, second.y2)

        intersection_width = max(
            0,
            intersection_x2 - intersection_x1,
        )
        intersection_height = max(
            0,
            intersection_y2 - intersection_y1,
        )
        intersection_area = intersection_width * intersection_height

        union_area = first.area + second.area - intersection_area

        if union_area <= 0:
            return 0.0

        return intersection_area / union_area
