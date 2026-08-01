"""PostgreSQL LISTEN/NOTIFY publisher for live activity stream events.

Notifications are emitted on the same SQLAlchemy ``Session`` that performs
durable entity/observation writes so PostgreSQL only delivers them when that
transaction commits. A rollback emits nothing.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from storage.timeline_models import TimelineEventType

_CHANNEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class ActivityNotificationPublisher:
    """Register minimal activity notifications inside an open transaction."""

    def __init__(
        self,
        *,
        channel: str = "jarvis_activity",
        observation_notifications_enabled: bool = False,
        observation_min_interval_seconds: float = 1.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self.channel = validate_notify_channel(channel)
        self.observation_notifications_enabled = bool(
            observation_notifications_enabled
        )
        if observation_min_interval_seconds < 0:
            raise ValueError(
                "observation_min_interval_seconds cannot be negative"
            )
        self.observation_min_interval_seconds = float(
            observation_min_interval_seconds
        )
        self._logger = logger or logging.getLogger(__name__)
        self._lock = RLock()
        self._last_observation_notify: dict[UUID, float] = {}
        # Captured notifications for SQLite/unit tests (never sent over PG).
        self.captured: list[dict[str, Any]] = []

    def publish_entity_created(
        self,
        session: Session,
        *,
        entity_id: UUID,
        occurred_at: datetime,
    ) -> None:
        self._notify(
            session,
            event_id=f"entity-created:{entity_id}",
            event_type=TimelineEventType.ENTITY_CREATED.value,
            occurred_at=occurred_at,
        )

    def publish_entity_closed(
        self,
        session: Session,
        *,
        entity_id: UUID,
        occurred_at: datetime,
    ) -> None:
        self._notify(
            session,
            event_id=f"entity-closed:{entity_id}",
            event_type=TimelineEventType.ENTITY_CLOSED.value,
            occurred_at=occurred_at,
        )

    def publish_observation_recorded(
        self,
        session: Session,
        *,
        observation_id: UUID,
        entity_id: UUID,
        occurred_at: datetime,
    ) -> bool:
        """Publish observation notification if enabled and not throttled.

        Returns True when a notification was registered on the session.
        """

        if not self.observation_notifications_enabled:
            return False

        now = time.monotonic()
        with self._lock:
            previous = self._last_observation_notify.get(entity_id)
            if (
                previous is not None
                and (now - previous) < self.observation_min_interval_seconds
            ):
                return False
            self._last_observation_notify[entity_id] = now

        self._notify(
            session,
            event_id=f"observation:{observation_id}",
            event_type=TimelineEventType.OBSERVATION_RECORDED.value,
            occurred_at=occurred_at,
        )
        return True

    def publish_spatial_event(
        self,
        session: Session,
        *,
        event_id: str,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        """Register a spatial timeline notification (same transaction)."""

        self._notify(
            session,
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
        )

    def _notify(
        self,
        session: Session,
        *,
        event_id: str,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        payload = {
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": _iso_utc(occurred_at),
        }
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)

        bind = session.get_bind()
        dialect = bind.dialect.name if bind is not None else ""

        if dialect == "sqlite":
            # Persistence tests use SQLite; capture for assertions.
            self.captured.append(dict(payload))
            self._logger.debug(
                "Captured activity notify (sqlite) channel=%s payload=%s",
                self.channel,
                payload_json,
            )
            return

        # Same transaction as the durable write: NOTIFY is released on commit.
        session.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {
                "channel": self.channel,
                "payload": payload_json,
            },
        )
        self._logger.debug(
            "Registered pg_notify channel=%s event_id=%s",
            self.channel,
            event_id,
        )


def validate_notify_channel(channel: str) -> str:
    """Validate a PostgreSQL NOTIFY channel name conservatively."""

    name = str(channel).strip()
    if not _CHANNEL_RE.fullmatch(name):
        raise ValueError(
            "activity_stream.notify_channel must match "
            r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"
        )
    return name


def parse_notification_payload(raw: str | bytes | None) -> dict[str, str]:
    """Parse and validate a minimal NOTIFY payload."""

    if raw is None:
        raise ValueError("notification payload is empty")
    if isinstance(raw, bytes):
        text_value = raw.decode("utf-8")
    else:
        text_value = str(raw)
    try:
        data = json.loads(text_value)
    except json.JSONDecodeError as error:
        raise ValueError("notification payload is not valid JSON") from error

    if not isinstance(data, dict):
        raise ValueError("notification payload must be an object")

    event_id = data.get("event_id")
    event_type = data.get("event_type")
    occurred_at = data.get("occurred_at")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("notification payload missing event_id")
    if not isinstance(event_type, str) or not event_type.strip():
        raise ValueError("notification payload missing event_type")
    if not isinstance(occurred_at, str) or not occurred_at.strip():
        raise ValueError("notification payload missing occurred_at")

    allowed = {
        TimelineEventType.ENTITY_CREATED.value,
        TimelineEventType.ENTITY_CLOSED.value,
        TimelineEventType.OBSERVATION_RECORDED.value,
        TimelineEventType.ZONE_ENTERED.value,
        TimelineEventType.ZONE_EXITED.value,
        TimelineEventType.ZONE_OCCUPANCY_CHANGED.value,
    }
    if event_type not in allowed:
        raise ValueError(f"unsupported event_type: {event_type}")

    return {
        "event_id": event_id.strip(),
        "event_type": event_type.strip(),
        "occurred_at": occurred_at.strip(),
    }


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")
