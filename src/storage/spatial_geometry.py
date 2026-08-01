"""Pure-Python spatial geometry helpers (v0.6.0).

No Shapely or OpenCV. Zones use normalised coordinates in [0.0, 1.0].
"""

from __future__ import annotations

import math
from typing import Any, Mapping

SUPPORTED_POSITION_STRATEGIES: frozenset[str] = frozenset(
    {"bottom_center", "center"}
)
SUPPORTED_GEOMETRY_TYPES: frozenset[str] = frozenset({"rectangle"})

MAX_ZONE_NAME_LENGTH = 128
MAX_METADATA_BYTES = 4096
MAX_ENTITY_TYPE_FILTERS = 32


class GeometryError(ValueError):
    """Raised when zone geometry or coordinates are invalid."""


def rectangle_vertices(
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
) -> list[dict[str, float]]:
    """Build four ordered normalised vertices for a rectangle."""

    _validate_finite(x_min, "x_min")
    _validate_finite(y_min, "y_min")
    _validate_finite(x_max, "x_max")
    _validate_finite(y_max, "y_max")

    if not (0.0 <= x_min <= 1.0 and 0.0 <= x_max <= 1.0):
        raise GeometryError("x coordinates must be between 0.0 and 1.0")
    if not (0.0 <= y_min <= 1.0 and 0.0 <= y_max <= 1.0):
        raise GeometryError("y coordinates must be between 0.0 and 1.0")
    if x_max <= x_min:
        raise GeometryError("rectangle must have non-zero width (x_max > x_min)")
    if y_max <= y_min:
        raise GeometryError(
            "rectangle must have non-zero height (y_max > y_min)"
        )

    return [
        {"x": float(x_min), "y": float(y_min)},
        {"x": float(x_max), "y": float(y_min)},
        {"x": float(x_max), "y": float(y_max)},
        {"x": float(x_min), "y": float(y_max)},
    ]


def validate_rectangle_vertices(
    vertices: Any,
) -> list[dict[str, float]]:
    """Validate and normalise a four-vertex rectangle list."""

    if not isinstance(vertices, list) or len(vertices) != 4:
        raise GeometryError("rectangle vertices must be a list of exactly 4 points")

    points: list[dict[str, float]] = []
    for index, raw in enumerate(vertices):
        if not isinstance(raw, Mapping):
            raise GeometryError(f"vertex {index} must be an object with x and y")
        if "x" not in raw or "y" not in raw:
            raise GeometryError(f"vertex {index} must include x and y")
        x = float(raw["x"])
        y = float(raw["y"])
        _validate_finite(x, f"vertex[{index}].x")
        _validate_finite(y, f"vertex[{index}].y")
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise GeometryError(
                f"vertex[{index}] coordinates must be between 0.0 and 1.0"
            )
        points.append({"x": x, "y": y})

    xs = [p["x"] for p in points]
    ys = [p["y"] for p in points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    if x_max <= x_min or y_max <= y_min:
        raise GeometryError("rectangle must have non-zero width and height")

    # Accept any ordering that forms a non-zero axis-aligned rectangle.
    # Canonicalise to the approved four-corner order for storage.
    return rectangle_vertices(x_min, y_min, x_max, y_max)


def rectangle_bounds(
    vertices: list[dict[str, float]],
) -> tuple[float, float, float, float]:
    """Return (x_min, y_min, x_max, y_max) for validated rectangle vertices."""

    xs = [float(p["x"]) for p in vertices]
    ys = [float(p["y"]) for p in vertices]
    return min(xs), min(ys), max(xs), max(ys)


def point_in_rectangle(
    x: float,
    y: float,
    vertices: list[dict[str, float]],
    *,
    inclusive: bool = True,
) -> bool:
    """Return True when the normalised point is inside the rectangle.

    Boundaries are inclusive by default so an entity whose anchor sits exactly
    on the zone edge is treated as inside.
    """

    x_min, y_min, x_max, y_max = rectangle_bounds(vertices)
    if inclusive:
        return x_min <= x <= x_max and y_min <= y <= y_max
    return x_min < x < x_max and y_min < y < y_max


def normalize_bbox_point(
    bounding_box: Mapping[str, Any],
    *,
    camera_width: int,
    camera_height: int,
    strategy: str,
) -> tuple[float, float]:
    """Convert a pixel bounding box to a normalised (x, y) anchor point.

    Bounding boxes use ``{x1, y1, x2, y2}`` in pixel coordinates.
    """

    if camera_width <= 0 or camera_height <= 0:
        raise GeometryError("camera_width and camera_height must be positive")

    strategy_norm = str(strategy).strip().lower()
    if strategy_norm not in SUPPORTED_POSITION_STRATEGIES:
        raise GeometryError(
            "position_strategy must be one of: "
            + ", ".join(sorted(SUPPORTED_POSITION_STRATEGIES))
        )

    try:
        x1 = float(bounding_box["x1"])
        y1 = float(bounding_box["y1"])
        x2 = float(bounding_box["x2"])
        y2 = float(bounding_box["y2"])
    except (KeyError, TypeError, ValueError) as error:
        raise GeometryError(
            "bounding_box must include finite x1, y1, x2, y2"
        ) from error

    for name, value in (
        ("x1", x1),
        ("y1", y1),
        ("x2", x2),
        ("y2", y2),
    ):
        _validate_finite(value, name)

    center_x = (x1 + x2) / 2.0
    if strategy_norm == "bottom_center":
        anchor_y = max(y1, y2)
    else:
        anchor_y = (y1 + y2) / 2.0

    nx = center_x / float(camera_width)
    ny = anchor_y / float(camera_height)
    # Clamp slightly out-of-frame anchors so edge detections remain usable.
    nx = min(1.0, max(0.0, nx))
    ny = min(1.0, max(0.0, ny))
    return nx, ny


def default_strategy_for_label(
    label: str,
    *,
    global_default: str = "bottom_center",
) -> str:
    """Choose a position strategy when a zone does not override it.

    Persons use bottom-center (feet); other labels use center unless the
    global default is more specific.
    """

    label_norm = str(label).strip().lower()
    if label_norm == "person":
        return "bottom_center"
    if global_default in SUPPORTED_POSITION_STRATEGIES:
        if label_norm == "person":
            return "bottom_center"
        # Non-person labels: center is preferred when global is bottom_center.
        if global_default == "bottom_center":
            return "center"
        return global_default
    return "center"


def resolve_position_strategy(
    *,
    label: str,
    zone_override: str | None,
    global_default: str = "bottom_center",
) -> str:
    """Resolve the effective position strategy for a zone match."""

    if zone_override is not None and str(zone_override).strip():
        strategy = str(zone_override).strip().lower()
        if strategy not in SUPPORTED_POSITION_STRATEGIES:
            raise GeometryError(
                "position_strategy must be one of: "
                + ", ".join(sorted(SUPPORTED_POSITION_STRATEGIES))
            )
        return strategy
    return default_strategy_for_label(label, global_default=global_default)


def _validate_finite(value: float, name: str) -> None:
    if not math.isfinite(value):
        raise GeometryError(f"{name} must be a finite number")
