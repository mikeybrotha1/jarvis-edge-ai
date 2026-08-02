"""Enqueue durable notification deliveries for alerts.

Transactional outbox guarantee (v0.9.0 — frozen)
------------------------------------------------
Local outbox persistence is **part of durable alert bookkeeping**.

In the same PostgreSQL (or SQLite test) transaction as the alert state change:

1. Select matching enabled targets (global + rule-associated, de-duplicated).
2. Insert / ensure deterministic ``notification_deliveries`` rows.
3. Register alert ``pg_notify`` (when configured) on that session.
4. Commit atomically.

**No HTTP** runs in this path. Database insert failures for required outbox
rows must **propagate** so the alert transaction rolls back. Callers must not
catch-and-commit around enqueue.

After commit, ``NotificationDeliveryWorker`` claims rows and performs network
I/O. Network failures, retries, and exhaustion never modify or roll back alert
state.

Idempotent re-evaluation reuses the unique ``idempotency_key``
``{alert_id}:{target_id}:{event_type}`` and does not create duplicate
logical deliveries.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from services.notifications.payload import build_alert_payload, idempotency_key
from storage.alert_records import AlertRecord
from storage.notification_records import NotificationDeliveryRecord
from storage.notification_repositories import (
    NotificationDeliveryRepository,
    NotificationTargetRepository,
)


class NotificationEnqueueService:
    def __init__(
        self,
        target_repository: NotificationTargetRepository,
        delivery_repository: NotificationDeliveryRepository,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._targets = target_repository
        self._deliveries = delivery_repository
        self._logger = logger or logging.getLogger(__name__)

    def enqueue_for_alert(
        self,
        alert: AlertRecord,
        *,
        event_type: str,
        session: Session | None = None,
        now: datetime | None = None,
    ) -> list[NotificationDeliveryRecord]:
        """Insert one delivery per matching target in ``session`` (if given).

        When ``session`` is provided, all work uses that session so callers can
        commit alert + outbox atomically. Database errors propagate.
        """

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)

        severity = (
            alert.severity.value
            if hasattr(alert.severity, "value")
            else str(alert.severity)
        )
        targets = self._targets.list_matching_for_alert(
            rule_id=alert.rule_id,
            severity=severity,
            session=session,
        )
        created: list[NotificationDeliveryRecord] = []
        for target in targets:
            key = idempotency_key(alert.id, target.id, event_type)
            delivery_id = uuid.uuid4()
            payload = build_alert_payload(
                alert,
                event_type=event_type,
                delivery_id=delivery_id,
                occurred_at=current,
            )
            row = self._deliveries.create_if_absent(
                alert_id=alert.id,
                target_id=target.id,
                event_type=event_type,
                idempotency_key=key,
                payload=payload,
                next_attempt_at=current,
                delivery_id=delivery_id,
                session=session,
            )
            if row is None:
                # Existing logical delivery (idempotent re-entry) — not an error.
                continue
            created.append(row)
            self._logger.debug(
                "Enqueued notification delivery %s for alert %s target %s",
                row.id,
                alert.id,
                target.id,
            )
        return created
