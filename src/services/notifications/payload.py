"""Stable webhook payload builders (schema_version 1)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from storage.alert_records import AlertRecord


PAYLOAD_SCHEMA_VERSION = "1"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def build_alert_payload(
    alert: AlertRecord,
    *,
    event_type: str,
    delivery_id: UUID | str,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a versioned webhook body for alert_triggered / alert_resolved."""

    occurred = occurred_at
    if occurred is None:
        if event_type == "alert_resolved" and alert.resolved_at is not None:
            occurred = alert.resolved_at
        else:
            occurred = alert.triggered_at
    alert_body: dict[str, Any] = {
        "id": str(alert.id),
        "rule_id": str(alert.rule_id),
        "status": alert.status.value if hasattr(alert.status, "value") else str(alert.status),
        "severity": (
            alert.severity.value
            if hasattr(alert.severity, "value")
            else str(alert.severity)
        ),
        "entity_id": str(alert.entity_id),
        "zone_id": str(alert.zone_id) if alert.zone_id else None,
        "camera_id": alert.camera_id,
        "summary": alert.summary,
        "payload": dict(alert.payload or {}),
        "triggered_at": _iso(alert.triggered_at),
        "resolved_at": _iso(alert.resolved_at),
        "acknowledged_at": _iso(alert.acknowledged_at),
        "subject_key": alert.subject_key,
        "source_event_id": alert.source_event_id,
    }
    if alert.rule_name:
        alert_body["rule_name"] = alert.rule_name
    return {
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "delivery_id": str(delivery_id),
        "event_type": event_type,
        "occurred_at": _iso(occurred),
        "alert": alert_body,
    }


def idempotency_key(alert_id: UUID, target_id: UUID, event_type: str) -> str:
    return f"{alert_id}:{target_id}:{event_type}"
