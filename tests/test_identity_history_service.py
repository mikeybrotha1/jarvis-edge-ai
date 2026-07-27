"""Tests for Jarvis identity history."""

from __future__ import annotations

from core.event_bus import EventBus
from core.events import EventType, JarvisEvent
from services.identity_history_service import IdentityHistoryService


def object_event(
    event_type: EventType,
    *,
    identity: str = "person-1",
    label: str = "person",
    track_id: int = 1,
    confidence: float = 0.80,
    frames_seen: int = 1,
    first_seen: str = "2026-07-26T15:00:00+00:00",
    last_seen: str = "2026-07-26T15:00:00+00:00",
) -> JarvisEvent:
    return JarvisEvent.create(
        event_type,
        source="memory_service",
        identity=identity,
        label=label,
        track_id=track_id,
        confidence=confidence,
        frames_seen=frames_seen,
        first_seen=first_seen,
        last_seen=last_seen,
        bounding_box={
            "x1": 100,
            "y1": 100,
            "x2": 300,
            "y2": 500,
        },
        missed_frames=0,
    )


def test_entered_event_creates_active_history() -> None:
    bus = EventBus()
    history = IdentityHistoryService(bus)
    history.start()

    bus.publish(
        object_event(
            EventType.OBJECT_ENTERED,
            confidence=0.87,
        )
    )

    record = history.history_for("person-1")

    assert record is not None
    assert record["identity"] == "person-1"
    assert record["label"] == "person"
    assert record["track_id"] == 1
    assert record["appearance_count"] == 1
    assert record["total_frames_seen"] == 1
    assert record["highest_confidence"] == 0.87
    assert record["last_confidence"] == 0.87
    assert record["active"] is True


def test_updates_do_not_double_count_cumulative_frames() -> None:
    bus = EventBus()
    history = IdentityHistoryService(bus)
    history.start()

    bus.publish(
        object_event(
            EventType.OBJECT_ENTERED,
            frames_seen=1,
        )
    )
    bus.publish(
        object_event(
            EventType.OBJECT_UPDATED,
            confidence=0.91,
            frames_seen=2,
            last_seen="2026-07-26T15:00:01+00:00",
        )
    )
    bus.publish(
        object_event(
            EventType.OBJECT_UPDATED,
            confidence=0.88,
            frames_seen=5,
            last_seen="2026-07-26T15:00:04+00:00",
        )
    )

    record = history.history_for("person-1")

    assert record is not None
    assert record["total_frames_seen"] == 5
    assert record["highest_confidence"] == 0.91
    assert record["last_confidence"] == 0.88
    assert record["last_seen"] == "2026-07-26T15:00:04+00:00"
    assert record["active"] is True


def test_exit_marks_history_inactive_and_retains_record() -> None:
    bus = EventBus()
    history = IdentityHistoryService(bus)
    history.start()

    bus.publish(
        object_event(
            EventType.OBJECT_ENTERED,
            frames_seen=1,
        )
    )
    bus.publish(
        object_event(
            EventType.OBJECT_EXITED,
            confidence=0.84,
            frames_seen=12,
            last_seen="2026-07-26T15:00:11+00:00",
        )
    )

    record = history.history_for("person-1")

    assert record is not None
    assert record["total_frames_seen"] == 12
    assert record["last_confidence"] == 0.84
    assert record["active"] is False
    assert history.active_histories() == []


def test_multiple_identities_are_ordered_by_track_id() -> None:
    bus = EventBus()
    history = IdentityHistoryService(bus)
    history.start()

    bus.publish(
        object_event(
            EventType.OBJECT_ENTERED,
            identity="bottle-2",
            label="bottle",
            track_id=2,
        )
    )
    bus.publish(
        object_event(
            EventType.OBJECT_ENTERED,
            identity="person-1",
            label="person",
            track_id=1,
        )
    )

    records = history.all_histories()

    assert [
        record["identity"]
        for record in records
    ] == [
        "person-1",
        "bottle-2",
    ]

    assert history.history_count() == 2
    assert history.history_count("person") == 1
    assert history.history_count("bottle") == 1


def test_repeated_enter_increments_appearance_count() -> None:
    bus = EventBus()
    history = IdentityHistoryService(bus)
    history.start()

    bus.publish(
        object_event(
            EventType.OBJECT_ENTERED,
            frames_seen=1,
        )
    )
    bus.publish(
        object_event(
            EventType.OBJECT_EXITED,
            frames_seen=4,
        )
    )
    bus.publish(
        object_event(
            EventType.OBJECT_ENTERED,
            frames_seen=1,
            last_seen="2026-07-26T16:00:00+00:00",
        )
    )
    bus.publish(
        object_event(
            EventType.OBJECT_UPDATED,
            frames_seen=3,
            last_seen="2026-07-26T16:00:02+00:00",
        )
    )

    record = history.history_for("person-1")

    assert record is not None
    assert record["appearance_count"] == 2
    assert record["total_frames_seen"] == 7
    assert record["active"] is True


def test_update_without_enter_creates_resilient_history() -> None:
    bus = EventBus()
    history = IdentityHistoryService(bus)
    history.start()

    bus.publish(
        object_event(
            EventType.OBJECT_UPDATED,
            frames_seen=8,
        )
    )

    record = history.history_for("person-1")

    assert record is not None
    assert record["appearance_count"] == 1
    assert record["total_frames_seen"] == 8
    assert record["active"] is True


def test_stop_unsubscribes_without_deleting_history() -> None:
    bus = EventBus()
    history = IdentityHistoryService(bus)
    history.start()

    bus.publish(
        object_event(EventType.OBJECT_ENTERED)
    )

    history.stop()

    bus.publish(
        object_event(
            EventType.OBJECT_UPDATED,
            frames_seen=10,
        )
    )

    record = history.history_for("person-1")

    assert record is not None
    assert record["total_frames_seen"] == 1


def test_clear_deletes_all_history() -> None:
    bus = EventBus()
    history = IdentityHistoryService(bus)
    history.start()

    bus.publish(
        object_event(EventType.OBJECT_ENTERED)
    )

    history.clear()

    assert history.history_for("person-1") is None
    assert history.all_histories() == []
    assert history.history_count() == 0


def test_missing_required_field_is_rejected() -> None:
    bus = EventBus()
    history = IdentityHistoryService(bus)

    invalid_event = JarvisEvent.create(
        EventType.OBJECT_ENTERED,
        source="memory_service",
        label="person",
        track_id=1,
        confidence=0.90,
        frames_seen=1,
    )

    try:
        history.handle_object_event(invalid_event)
    except ValueError as error:
        assert "identity" in str(error)
    else:
        raise AssertionError("Expected ValueError")


if __name__ == "__main__":
    test_entered_event_creates_active_history()
    test_updates_do_not_double_count_cumulative_frames()
    test_exit_marks_history_inactive_and_retains_record()
    test_multiple_identities_are_ordered_by_track_id()
    test_repeated_enter_increments_appearance_count()
    test_update_without_enter_creates_resilient_history()
    test_stop_unsubscribes_without_deleting_history()
    test_clear_deletes_all_history()
    test_missing_required_field_is_rejected()

    print("All identity history service tests passed.")
