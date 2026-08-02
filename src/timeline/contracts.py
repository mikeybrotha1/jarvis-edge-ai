"""Canonical typed timeline projection contract (v0.6.0 / v0.7.0).

PostgreSQL requires every UNION ALL branch to emit the same SQL type in each
column position. Providers and any multi-branch SELECT must use these helpers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    Integer,
    String,
    cast,
    null,
)
from sqlalchemy.sql import ColumnElement
from sqlalchemy.sql.elements import Label, Null

from storage.timeline_models import TimelineEvent, TimelineEventType

# Canonical SQL types (PostgreSQL + SQLite compatible).
STR = String()
INT = Integer()
BIGINT = BigInteger()
FLOAT = Float()
DT = DateTime(timezone=True)

# Ordered column names for the timeline projection contract.
TIMELINE_UNION_COLUMN_NAMES: tuple[str, ...] = (
    "event_id",
    "event_type",
    "occurred_at",
    "source",
    "entity_id",
    "camera_id",
    "entity_type",
    "identity_key",
    "track_id",
    "status",
    "confidence",
    "frame_number",
    "source_event_type",
    "zone_id",
    "zone_name",
    "session_id",
    "occupancy",
)


def is_sql_null(value: Any) -> bool:
    return isinstance(value, Null)


def typed_null(sql_type: Any, label: str) -> Label[Any]:
    """Typed SQL NULL so PostgreSQL UNION branches share one type."""

    return cast(null(), sql_type).label(label)


def typed_str(value: Any, label: str) -> Label[Any]:
    if is_sql_null(value):
        return cast(null(), STR).label(label)
    return cast(value, STR).label(label)


def typed_int(value: Any, label: str) -> Label[Any]:
    if is_sql_null(value):
        return cast(null(), INT).label(label)
    return cast(value, INT).label(label)


def typed_bigint(value: Any, label: str) -> Label[Any]:
    if is_sql_null(value):
        return cast(null(), BIGINT).label(label)
    return cast(value, BIGINT).label(label)


def typed_float(value: Any, label: str) -> Label[Any]:
    if is_sql_null(value):
        return cast(null(), FLOAT).label(label)
    return cast(value, FLOAT).label(label)


def typed_dt(value: Any, label: str) -> Label[Any]:
    """Label datetime columns; cast only SQL NULL (SQLite result safety)."""

    if is_sql_null(value):
        return cast(null(), DT).label(label)
    return value.label(label)


def projection(
    *,
    event_id: Any,
    event_type: Any,
    occurred_at: Any,
    source: Any,
    entity_id: Any,
    camera_id: Any,
    entity_type: Any,
    identity_key: Any,
    track_id: Any,
    status: Any,
    confidence: Any,
    frame_number: Any,
    source_event_type: Any,
    zone_id: Any,
    zone_name: Any,
    session_id: Any,
    occupancy: Any,
) -> tuple[ColumnElement[Any], ...]:
    """Return the canonical typed column list for one projection branch."""

    return (
        typed_str(event_id, "event_id"),
        typed_str(event_type, "event_type"),
        typed_dt(occurred_at, "occurred_at"),
        typed_str(source, "source"),
        typed_str(entity_id, "entity_id"),
        typed_str(camera_id, "camera_id"),
        typed_str(entity_type, "entity_type"),
        typed_str(identity_key, "identity_key"),
        typed_bigint(track_id, "track_id"),
        typed_str(status, "status"),
        typed_float(confidence, "confidence"),
        typed_bigint(frame_number, "frame_number"),
        typed_str(source_event_type, "source_event_type"),
        typed_str(zone_id, "zone_id"),
        typed_str(zone_name, "zone_name"),
        typed_str(session_id, "session_id"),
        typed_int(occupancy, "occupancy"),
    )


def null_projection_defaults() -> dict[str, Any]:
    """SQL NULL expressions for optional projection columns (cast by projection)."""

    return {
        "identity_key": null(),
        "track_id": null(),
        "status": null(),
        "confidence": null(),
        "frame_number": null(),
        "source_event_type": null(),
        "zone_id": null(),
        "zone_name": null(),
        "session_id": null(),
        "occupancy": null(),
    }


# Backward-compatible private aliases used by older tests/imports.
_projection = projection
_null_projection_defaults = null_projection_defaults
_STR = STR
_INT = INT
_BIGINT = BIGINT
_FLOAT = FLOAT
_DT = DT


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_uuid_suffix(event_id: str, prefix: str) -> UUID | None:
    raw = event_id[len(prefix) :]
    try:
        return UUID(raw)
    except ValueError:
        return None


def row_to_timeline_event(row: Any) -> TimelineEvent:
    """Convert a canonical projection row mapping into TimelineEvent."""

    event_type = TimelineEventType(str(row["event_type"]))
    entity_type = str(row["entity_type"] or "unknown")
    camera_id = row["camera_id"]
    camera_display = camera_id or "unknown"
    title = f"{entity_type[:1].upper()}{entity_type[1:]}"

    if event_type is TimelineEventType.ENTITY_CREATED:
        summary = f"{title} appeared on {camera_display}"
        payload = {
            "identity_key": row["identity_key"],
            "track_id": row["track_id"],
            "status": "active",
        }
    elif event_type is TimelineEventType.ENTITY_CLOSED:
        summary = f"{title} left {camera_display}"
        payload = {
            "identity_key": row["identity_key"],
            "track_id": row["track_id"],
            "status": "closed",
        }
    elif event_type is TimelineEventType.OBSERVATION_RECORDED:
        summary = f"{title} observed on {camera_display}"
        payload = {
            "confidence": row["confidence"],
            "frame_number": row["frame_number"],
            "track_id": row["track_id"],
            "source_event_type": row["source_event_type"],
        }
    elif event_type is TimelineEventType.ZONE_ENTERED:
        zone_name = row["zone_name"] or "zone"
        summary = f"{title} entered {zone_name}"
        payload = {
            "zone_id": row["zone_id"],
            "zone_name": zone_name,
            "session_id": row["session_id"],
            "occupancy": row["occupancy"],
        }
    elif event_type is TimelineEventType.ZONE_EXITED:
        zone_name = row["zone_name"] or "zone"
        summary = f"{title} exited {zone_name}"
        payload = {
            "zone_id": row["zone_id"],
            "zone_name": zone_name,
            "session_id": row["session_id"],
            "occupancy": row["occupancy"],
        }
    else:
        zone_name = row["zone_name"] or "zone"
        occupancy = row["occupancy"]
        summary = f"{zone_name} occupancy is now {occupancy}"
        cause = row["status"]
        payload = {
            "zone_id": row["zone_id"],
            "zone_name": zone_name,
            "session_id": row["session_id"],
            "occupancy": occupancy,
            "cause": cause,
        }

    occurred_at = row["occurred_at"]
    if isinstance(occurred_at, str):
        occurred_at = datetime.fromisoformat(
            occurred_at.replace("Z", "+00:00")
        )
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)

    return TimelineEvent(
        id=str(row["event_id"]),
        event_type=event_type,
        occurred_at=occurred_at,
        source=str(row["source"]),
        entity_id=UUID(str(row["entity_id"])),
        camera_id=camera_id,
        entity_type=entity_type,
        summary=summary,
        payload=payload,
    )


def event_ordering_key(event: TimelineEvent) -> tuple[datetime, str]:
    """Deterministic ordering key: occurred_at then stable event id."""

    return (event.occurred_at, event.id)


def compare_events(
    left: TimelineEvent,
    right: TimelineEvent,
    *,
    sort: str,
) -> int:
    """Return negative if left should appear before right for the given sort."""

    reverse = sort == "desc"
    if left.occurred_at != right.occurred_at:
        if reverse:
            return -1 if left.occurred_at > right.occurred_at else 1
        return -1 if left.occurred_at < right.occurred_at else 1
    if left.id == right.id:
        return 0
    if reverse:
        return -1 if left.id > right.id else 1
    return -1 if left.id < right.id else 1
