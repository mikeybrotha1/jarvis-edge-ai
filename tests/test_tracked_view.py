"""Tests for presentation-friendly tracked-object views."""

from __future__ import annotations

from datetime import datetime, timezone


from tracked_view import (
    TrackedRenderObject,
    build_tracked_view,
    parse_timestamp,
    tracked_object_from_snapshot,
)


FIXED_NOW = datetime(
    2026,
    7,
    26,
    12,
    0,
    10,
    tzinfo=timezone.utc,
)


def make_snapshot(
    *,
    identity: str = "person-1",
    label: str = "person",
    confidence: float = 0.94,
    x1: int = 100,
    y1: int = 80,
    x2: int = 420,
    y2: int = 690,
    frames_seen: int = 248,
    missed_frames: int = 0,
) -> dict:
    return {
        "track_id": 1,
        "identity": identity,
        "label": label,
        "confidence": confidence,
        "bounding_box": {
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
        },
        "first_seen": "2026-07-26T12:00:00+00:00",
        "last_seen": "2026-07-26T12:00:08+00:00",
        "frames_seen": frames_seen,
        "missed_frames": missed_frames,
    }


def test_builds_render_object_from_snapshot() -> None:
    tracked = tracked_object_from_snapshot(
        make_snapshot(),
        now=FIXED_NOW,
    )

    assert isinstance(tracked, TrackedRenderObject)
    assert tracked.identity == "person-1"
    assert tracked.label == "person"
    assert tracked.confidence == 0.94
    assert tracked.bounding_box == (100, 80, 420, 690)
    assert tracked.frames_seen == 248
    assert tracked.missed_frames == 0
    assert tracked.age_seconds == 10.0
    assert tracked.is_currently_visible is True


def test_build_view_excludes_temporarily_missing_tracks() -> None:
    snapshots = [
        make_snapshot(identity="person-1"),
        make_snapshot(
            identity="chair-2",
            label="chair",
            missed_frames=1,
        ),
    ]

    tracked = build_tracked_view(
        snapshots,
        now=FIXED_NOW,
    )

    assert [item.identity for item in tracked] == ["person-1"]


def test_build_view_can_include_missing_tracks() -> None:
    snapshots = [
        make_snapshot(identity="person-1"),
        make_snapshot(
            identity="chair-2",
            label="chair",
            missed_frames=1,
        ),
    ]

    tracked = build_tracked_view(
        snapshots,
        now=FIXED_NOW,
        include_missing=True,
    )

    assert [item.identity for item in tracked] == [
        "chair-2",
        "person-1",
    ]
    assert tracked[0].is_currently_visible is False


def test_parse_timestamp_accepts_z_suffix() -> None:
    parsed = parse_timestamp("2026-07-26T12:00:00Z")

    assert parsed == datetime(
        2026,
        7,
        26,
        12,
        0,
        tzinfo=timezone.utc,
    )


def test_rejects_invalid_bounding_box() -> None:
    snapshot = make_snapshot(
        x1=420,
        x2=100,
    )

    try:
        tracked_object_from_snapshot(
            snapshot,
            now=FIXED_NOW,
        )
    except ValueError as error:
        assert "invalid bounding box" in str(error)
    else:
        raise AssertionError("Expected invalid bounding box ValueError")


def test_rejects_invalid_confidence() -> None:
    snapshot = make_snapshot(confidence=1.5)

    try:
        tracked_object_from_snapshot(
            snapshot,
            now=FIXED_NOW,
        )
    except ValueError as error:
        assert "confidence" in str(error)
    else:
        raise AssertionError("Expected confidence ValueError")


def test_rejects_negative_missed_frames() -> None:
    snapshot = make_snapshot(missed_frames=-1)

    try:
        tracked_object_from_snapshot(
            snapshot,
            now=FIXED_NOW,
        )
    except ValueError as error:
        assert "missed_frames" in str(error)
    else:
        raise AssertionError("Expected missed_frames ValueError")


if __name__ == "__main__":
    test_builds_render_object_from_snapshot()
    test_build_view_excludes_temporarily_missing_tracks()
    test_build_view_can_include_missing_tracks()
    test_parse_timestamp_accepts_z_suffix()
    test_rejects_invalid_bounding_box()
    test_rejects_invalid_confidence()
    test_rejects_negative_missed_frames()

    print("All tracked view tests passed.")
