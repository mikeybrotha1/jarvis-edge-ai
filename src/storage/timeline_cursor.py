"""Opaque cursor encoding for timeline pagination.

Cursor payload is base64url JSON containing only:

- ``t``: ISO 8601 UTC timestamp of the last returned event
- ``i``: stable event id of the last returned event

No secrets, database URLs, or internal paths are included.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from storage.timeline_models import TimelineCursor


class CursorError(ValueError):
    """Raised when a client cursor is malformed or invalid."""


def encode_cursor(occurred_at: datetime, event_id: str) -> str:
    """Encode a cursor for the next page."""

    aware = _ensure_utc(occurred_at)
    payload = {
        "t": aware.isoformat().replace("+00:00", "Z"),
        "i": event_id,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str) -> TimelineCursor:
    """Decode and validate an opaque cursor token."""

    if not token or not str(token).strip():
        raise CursorError("cursor cannot be empty.")

    padded = str(token).strip()
    padding = "=" * (-len(padded) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded + padding)
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise CursorError("cursor is malformed.") from error

    if not isinstance(data, dict):
        raise CursorError("cursor is malformed.")

    timestamp = data.get("t")
    event_id = data.get("i")
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise CursorError("cursor is missing occurred_at.")
    if not isinstance(event_id, str) or not event_id.strip():
        raise CursorError("cursor is missing event_id.")

    try:
        occurred_at = _parse_iso_utc(timestamp)
    except ValueError as error:
        raise CursorError("cursor timestamp is invalid.") from error

    return TimelineCursor(occurred_at=occurred_at, event_id=event_id.strip())


def _parse_iso_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return _ensure_utc(parsed)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
