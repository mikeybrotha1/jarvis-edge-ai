"""OpenCV renderer for persistent tracked-object identities."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

import cv2
import numpy as np

from tracked_view import TrackedRenderObject


def identity_colour(identity: str) -> tuple[int, int, int]:
    """Return a stable, readable OpenCV BGR colour for an identity."""

    if not identity:
        raise ValueError("identity cannot be empty")

    digest = hashlib.sha256(identity.encode("utf-8")).digest()

    # Keep every channel away from very dark values so boxes and labels remain
    # visible against most camera backgrounds.
    blue = 80 + digest[0] % 176
    green = 80 + digest[1] % 176
    red = 80 + digest[2] % 176

    return blue, green, red


def format_age(age_seconds: float) -> str:
    """Format elapsed tracking time for a compact HUD label."""

    if age_seconds < 0:
        raise ValueError("age_seconds cannot be negative")

    if age_seconds < 10:
        return f"{age_seconds:.1f}s"

    total_seconds = int(age_seconds)

    if total_seconds < 60:
        return f"{total_seconds}s"

    total_minutes, seconds = divmod(total_seconds, 60)

    if total_minutes < 60:
        return f"{total_minutes}m{seconds:02d}s"

    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _contrasting_text_colour(
    background: tuple[int, int, int],
) -> tuple[int, int, int]:
    """Choose black or white text for a BGR background colour."""

    blue, green, red = background
    luminance = (
        0.114 * blue
        + 0.587 * green
        + 0.299 * red
    )

    return (0, 0, 0) if luminance >= 150 else (255, 255, 255)


def _clip_box_to_frame(
    tracked: TrackedRenderObject,
    frame: np.ndarray,
) -> tuple[int, int, int, int] | None:
    """Clip a tracked bounding box to the frame dimensions."""

    frame_height, frame_width = frame.shape[:2]

    x1 = max(0, min(frame_width - 1, tracked.x1))
    y1 = max(0, min(frame_height - 1, tracked.y1))
    x2 = max(0, min(frame_width - 1, tracked.x2))
    y2 = max(0, min(frame_height - 1, tracked.y2))

    if x2 <= x1 or y2 <= y1:
        return None

    return x1, y1, x2, y2


def render_tracked_objects(
    frame: np.ndarray,
    tracked_objects: Iterable[TrackedRenderObject],
    *,
    box_thickness: int = 2,
) -> np.ndarray:
    """Draw persistent identities and memory information on a frame.

    The frame is modified in place and returned for convenient composition.
    """

    if not isinstance(frame, np.ndarray):
        raise TypeError("frame must be a NumPy array")

    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("frame must have shape (height, width, 3)")

    if box_thickness < 1:
        raise ValueError("box_thickness must be at least 1")

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.52
    text_thickness = 1
    padding = 5
    line_gap = 4

    for tracked in tracked_objects:
        clipped_box = _clip_box_to_frame(tracked, frame)

        if clipped_box is None:
            continue

        x1, y1, x2, y2 = clipped_box
        colour = identity_colour(tracked.identity)
        text_colour = _contrasting_text_colour(colour)

        cv2.rectangle(
            frame,
            (x1, y1),
            (x2, y2),
            colour,
            box_thickness,
        )

        title = tracked.identity
        details = (
            f"{tracked.confidence:.0%} | "
            f"seen {format_age(tracked.age_seconds)}"
        )

        title_size, title_baseline = cv2.getTextSize(
            title,
            font,
            font_scale,
            text_thickness,
        )
        details_size, details_baseline = cv2.getTextSize(
            details,
            font,
            font_scale,
            text_thickness,
        )

        title_width, title_height = title_size
        details_width, details_height = details_size

        label_width = max(title_width, details_width) + 2 * padding
        label_height = (
            title_height
            + details_height
            + line_gap
            + 2 * padding
            + max(title_baseline, details_baseline)
        )

        label_x1 = x1
        label_y2 = y1

        if y1 - label_height >= 0:
            label_y1 = y1 - label_height
        else:
            label_y1 = y1
            label_y2 = min(
                frame.shape[0] - 1,
                y1 + label_height,
            )

        label_x2 = min(
            frame.shape[1] - 1,
            label_x1 + label_width,
        )

        cv2.rectangle(
            frame,
            (label_x1, label_y1),
            (label_x2, label_y2),
            colour,
            -1,
        )

        title_origin = (
            label_x1 + padding,
            label_y1 + padding + title_height,
        )

        details_origin = (
            label_x1 + padding,
            title_origin[1] + line_gap + details_height,
        )

        cv2.putText(
            frame,
            title,
            title_origin,
            font,
            font_scale,
            text_colour,
            text_thickness,
            cv2.LINE_AA,
        )

        cv2.putText(
            frame,
            details,
            details_origin,
            font,
            font_scale,
            text_colour,
            text_thickness,
            cv2.LINE_AA,
        )

    return frame
