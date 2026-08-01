"""Unit tests for pure-Python spatial geometry (v0.6.0)."""

from __future__ import annotations

import pytest

from storage.spatial_geometry import (
    GeometryError,
    default_strategy_for_label,
    normalize_bbox_point,
    point_in_rectangle,
    rectangle_vertices,
    resolve_position_strategy,
    validate_rectangle_vertices,
)


def test_rectangle_vertices_and_bounds() -> None:
    verts = rectangle_vertices(0.1, 0.2, 0.5, 0.8)
    assert len(verts) == 4
    assert verts[0] == {"x": 0.1, "y": 0.2}
    assert verts[2] == {"x": 0.5, "y": 0.8}


def test_invalid_rectangle_zero_width() -> None:
    with pytest.raises(GeometryError):
        rectangle_vertices(0.5, 0.1, 0.5, 0.9)


def test_invalid_coordinates_out_of_range() -> None:
    with pytest.raises(GeometryError):
        rectangle_vertices(-0.1, 0.0, 0.5, 0.5)
    with pytest.raises(GeometryError):
        rectangle_vertices(0.0, 0.0, 1.5, 0.5)


def test_validate_rectangle_vertices_reorders() -> None:
    raw = [
        {"x": 0.8, "y": 0.9},
        {"x": 0.2, "y": 0.1},
        {"x": 0.8, "y": 0.1},
        {"x": 0.2, "y": 0.9},
    ]
    verts = validate_rectangle_vertices(raw)
    assert verts[0]["x"] == 0.2
    assert verts[0]["y"] == 0.1
    assert verts[2]["x"] == 0.8
    assert verts[2]["y"] == 0.9


def test_point_in_rectangle_inclusive_boundaries() -> None:
    verts = rectangle_vertices(0.2, 0.2, 0.8, 0.8)
    assert point_in_rectangle(0.2, 0.2, verts, inclusive=True)
    assert point_in_rectangle(0.8, 0.8, verts, inclusive=True)
    assert point_in_rectangle(0.5, 0.5, verts, inclusive=True)
    assert not point_in_rectangle(0.199, 0.5, verts, inclusive=True)
    assert not point_in_rectangle(0.2, 0.2, verts, inclusive=False)


def test_normalize_bottom_center_and_center() -> None:
    box = {"x1": 100, "y1": 50, "x2": 300, "y2": 250}
    # width=1000 height=500
    nx, ny = normalize_bbox_point(
        box,
        camera_width=1000,
        camera_height=500,
        strategy="bottom_center",
    )
    assert nx == pytest.approx(0.2)
    assert ny == pytest.approx(0.5)

    cx, cy = normalize_bbox_point(
        box,
        camera_width=1000,
        camera_height=500,
        strategy="center",
    )
    assert cx == pytest.approx(0.2)
    assert cy == pytest.approx(0.3)


def test_default_strategy_for_label() -> None:
    assert default_strategy_for_label("person") == "bottom_center"
    assert default_strategy_for_label("car") == "center"
    assert (
        resolve_position_strategy(
            label="person",
            zone_override="center",
            global_default="bottom_center",
        )
        == "center"
    )
