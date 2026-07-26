"""Tests for persistent tracked-object rendering."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from rendering.tracked_renderer import (
    format_age,
    identity_colour,
    render_tracked_objects,
)
from tracked_view import TrackedRenderObject


def make_tracked_object(
    *,
    identity: str = "person-1",
    confidence: float = 0.94,
    x1: int = 100,
    y1: int = 100,
    x2: int = 420,
    y2: int = 600,
    age_seconds: float = 10.0,
) -> TrackedRenderObject:
    return TrackedRenderObject(
        identity=identity,
        label="person",
        confidence=confidence,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        frames_seen=248,
        missed_frames=0,
        first_seen=datetime(
            2026,
            7,
            26,
            12,
            0,
            tzinfo=timezone.utc,
        ),
        last_seen=datetime(
            2026,
            7,
            26,
            12,
            0,
            10,
            tzinfo=timezone.utc,
        ),
        age_seconds=age_seconds,
    )


def test_identity_colour_is_deterministic() -> None:
    assert (
        identity_colour("person-1")
        == identity_colour("person-1")
    )


def test_different_identities_have_different_colours() -> None:
    assert (
        identity_colour("person-1")
        != identity_colour("person-2")
    )


def test_identity_colour_channels_are_readable() -> None:
    colour = identity_colour("chair-3")

    assert len(colour) == 3
    assert all(80 <= channel <= 255 for channel in colour)


def test_format_age() -> None:
    assert format_age(0.0) == "0.0s"
    assert format_age(3.2) == "3.2s"
    assert format_age(10.8) == "10s"
    assert format_age(59.9) == "59s"
    assert format_age(60) == "1m00s"
    assert format_age(94) == "1m34s"
    assert format_age(3672) == "1h01m"


def test_format_age_rejects_negative_value() -> None:
    try:
        format_age(-1)
    except ValueError as error:
        assert "cannot be negative" in str(error)
    else:
        raise AssertionError("Expected negative age ValueError")


def test_render_empty_collection_does_not_modify_frame() -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    before = frame.copy()

    result = render_tracked_objects(frame, [])

    assert result is frame
    assert np.array_equal(frame, before)


def test_render_modifies_frame_for_tracked_object() -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    before = frame.copy()

    result = render_tracked_objects(
        frame,
        [make_tracked_object()],
    )

    assert result is frame
    assert np.any(frame != before)


def test_render_clips_box_to_frame() -> None:
    frame = np.zeros((240, 320, 3), dtype=np.uint8)

    tracked = make_tracked_object(
        x1=-100,
        y1=-50,
        x2=500,
        y2=400,
    )

    result = render_tracked_objects(frame, [tracked])

    assert result is frame
    assert np.any(frame != 0)


def test_render_skips_box_outside_frame() -> None:
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    before = frame.copy()

    tracked = make_tracked_object(
        x1=500,
        y1=500,
        x2=600,
        y2=600,
    )

    render_tracked_objects(frame, [tracked])

    assert np.array_equal(frame, before)


def test_render_rejects_invalid_frame_shape() -> None:
    frame = np.zeros((720, 1280), dtype=np.uint8)

    try:
        render_tracked_objects(frame, [])
    except ValueError as error:
        assert "shape" in str(error)
    else:
        raise AssertionError("Expected invalid frame shape ValueError")


def test_render_rejects_invalid_box_thickness() -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    try:
        render_tracked_objects(
            frame,
            [],
            box_thickness=0,
        )
    except ValueError as error:
        assert "box_thickness" in str(error)
    else:
        raise AssertionError("Expected box_thickness ValueError")


if __name__ == "__main__":
    test_identity_colour_is_deterministic()
    test_different_identities_have_different_colours()
    test_identity_colour_channels_are_readable()
    test_format_age()
    test_format_age_rejects_negative_value()
    test_render_empty_collection_does_not_modify_frame()
    test_render_modifies_frame_for_tracked_object()
    test_render_clips_box_to_frame()
    test_render_skips_box_outside_frame()
    test_render_rejects_invalid_frame_shape()
    test_render_rejects_invalid_box_thickness()

    print("All tracked renderer tests passed.")
