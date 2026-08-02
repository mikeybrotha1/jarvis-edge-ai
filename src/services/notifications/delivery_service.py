"""Query and manual-retry service for notification deliveries."""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from storage.entity_records import PageResult
from storage.notification_orm import DeliveryStatus
from storage.notification_records import (
    DeliveryAttemptRecord,
    DeliveryListFilter,
    NotificationDeliveryRecord,
)
from storage.notification_repositories import (
    NotificationConflictError,
    NotificationDeliveryRepository,
)


class DeliveryNotFoundError(LookupError):
    pass


class NotificationDeliveryQueryService:
    def __init__(
        self,
        delivery_repository: NotificationDeliveryRepository,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._deliveries = delivery_repository
        self._logger = logger or logging.getLogger(__name__)

    def get(self, delivery_id: UUID) -> NotificationDeliveryRecord:
        row = self._deliveries.get_by_id(delivery_id)
        if row is None:
            raise DeliveryNotFoundError(
                f"Notification delivery not found: {delivery_id}"
            )
        return row

    def list(
        self,
        *,
        status: str | None = None,
        alert_id: UUID | None = None,
        target_id: UUID | None = None,
        rule_id: UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
        sort: str = "desc",
    ) -> PageResult:
        status_enum = None
        if status is not None:
            try:
                status_enum = DeliveryStatus(status)
            except ValueError as error:
                raise ValueError(f"Invalid status: {status}") from error
        filters = DeliveryListFilter(
            status=status_enum,
            alert_id=alert_id,
            target_id=target_id,
            rule_id=rule_id,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            offset=offset,
            sort=sort,
        )
        return self._deliveries.list_deliveries(filters)

    def list_attempts(self, delivery_id: UUID) -> list[DeliveryAttemptRecord]:
        self.get(delivery_id)
        return self._deliveries.list_attempts(delivery_id)

    def manual_retry(self, delivery_id: UUID) -> NotificationDeliveryRecord:
        try:
            return self._deliveries.schedule_manual_retry(delivery_id)
        except LookupError as error:
            raise DeliveryNotFoundError(str(error)) from error
        except NotificationConflictError:
            raise
