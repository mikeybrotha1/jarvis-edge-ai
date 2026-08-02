"""REST routes for notification targets and deliveries (v0.9.0)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from api.notification_schemas import (
    CollectionOut,
    DeliveryAttemptOut,
    NotificationDeliveryOut,
    NotificationTargetOut,
)
from services.notifications.delivery_service import (
    DeliveryNotFoundError,
    NotificationDeliveryQueryService,
)
from services.notifications.target_service import (
    NotificationTargetService,
    TargetNotFoundError,
    TargetValidationError,
)
from storage.notification_repositories import NotificationConflictError

logger = logging.getLogger(__name__)

targets_router = APIRouter(
    prefix="/api/v1/notification-targets", tags=["notifications"]
)
deliveries_router = APIRouter(
    prefix="/api/v1/notification-deliveries", tags=["notifications"]
)
# Nested under alert-rules and alerts — mounted from app or included here.
rule_targets_router = APIRouter(
    prefix="/api/v1/alert-rules", tags=["notifications"]
)
alert_deliveries_router = APIRouter(
    prefix="/api/v1/alerts", tags=["notifications"]
)


def get_target_service(request: Request) -> NotificationTargetService:
    service = getattr(request.app.state, "notification_target_service", None)
    if service is None:
        raise RuntimeError("NotificationTargetService is not configured.")
    return service


def get_delivery_service(request: Request) -> NotificationDeliveryQueryService:
    service = getattr(request.app.state, "notification_delivery_service", None)
    if service is None:
        raise RuntimeError("NotificationDeliveryQueryService is not configured.")
    return service


def _safe_db() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Notification service temporarily unavailable.",
    )


@targets_router.get("", response_model=CollectionOut[NotificationTargetOut])
def list_targets(
    service: Annotated[NotificationTargetService, Depends(get_target_service)],
    enabled: Annotated[bool | None, Query()] = None,
    is_global: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CollectionOut[NotificationTargetOut]:
    try:
        page = service.list_targets(
            enabled=enabled, is_global=is_global, limit=limit, offset=offset
        )
    except Exception:
        logger.exception("list_targets failed")
        raise _safe_db() from None
    return CollectionOut[NotificationTargetOut](
        items=[NotificationTargetOut.from_record(i) for i in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@targets_router.post(
    "",
    response_model=NotificationTargetOut,
    status_code=status.HTTP_201_CREATED,
)
def create_target(
    body: dict[str, Any],
    service: Annotated[NotificationTargetService, Depends(get_target_service)],
) -> NotificationTargetOut:
    try:
        record = service.create(body)
    except TargetValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except NotificationConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        logger.exception("create_target failed")
        raise _safe_db() from None
    return NotificationTargetOut.from_record(record)


@targets_router.get("/{target_id}", response_model=NotificationTargetOut)
def get_target(
    target_id: UUID,
    service: Annotated[NotificationTargetService, Depends(get_target_service)],
) -> NotificationTargetOut:
    try:
        return NotificationTargetOut.from_record(service.get(target_id))
    except TargetNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception:
        logger.exception("get_target failed")
        raise _safe_db() from None


@targets_router.patch("/{target_id}", response_model=NotificationTargetOut)
def patch_target(
    target_id: UUID,
    body: dict[str, Any],
    service: Annotated[NotificationTargetService, Depends(get_target_service)],
) -> NotificationTargetOut:
    try:
        return NotificationTargetOut.from_record(service.update(target_id, body))
    except TargetNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except TargetValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except NotificationConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        logger.exception("patch_target failed")
        raise _safe_db() from None


@rule_targets_router.get(
    "/{rule_id}/notification-targets",
    response_model=CollectionOut[NotificationTargetOut],
)
def list_rule_targets(
    rule_id: UUID,
    service: Annotated[NotificationTargetService, Depends(get_target_service)],
) -> CollectionOut[NotificationTargetOut]:
    try:
        items = service.list_for_rule(rule_id)
    except Exception:
        logger.exception("list_rule_targets failed")
        raise _safe_db() from None
    return CollectionOut[NotificationTargetOut](
        items=[NotificationTargetOut.from_record(i) for i in items],
        total=len(items),
        limit=len(items),
        offset=0,
    )


@rule_targets_router.post(
    "/{rule_id}/notification-targets/{target_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def associate_rule_target(
    rule_id: UUID,
    target_id: UUID,
    service: Annotated[NotificationTargetService, Depends(get_target_service)],
) -> None:
    try:
        service.associate(rule_id, target_id)
    except TargetNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NotificationConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        logger.exception("associate_rule_target failed")
        raise _safe_db() from None


@rule_targets_router.delete(
    "/{rule_id}/notification-targets/{target_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def disassociate_rule_target(
    rule_id: UUID,
    target_id: UUID,
    service: Annotated[NotificationTargetService, Depends(get_target_service)],
) -> None:
    try:
        service.disassociate(rule_id, target_id)
    except TargetNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception:
        logger.exception("disassociate_rule_target failed")
        raise _safe_db() from None


@deliveries_router.get("", response_model=CollectionOut[NotificationDeliveryOut])
def list_deliveries(
    service: Annotated[
        NotificationDeliveryQueryService, Depends(get_delivery_service)
    ],
    status_filter: Annotated[
        str | None, Query(alias="status")
    ] = None,
    alert_id: Annotated[UUID | None, Query()] = None,
    target_id: Annotated[UUID | None, Query()] = None,
    rule_id: Annotated[UUID | None, Query()] = None,
    created_after: Annotated[datetime | None, Query()] = None,
    created_before: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = "desc",
) -> CollectionOut[NotificationDeliveryOut]:
    try:
        page = service.list(
            status=status_filter,
            alert_id=alert_id,
            target_id=target_id,
            rule_id=rule_id,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            offset=offset,
            sort=sort,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception:
        logger.exception("list_deliveries failed")
        raise _safe_db() from None
    return CollectionOut[NotificationDeliveryOut](
        items=[NotificationDeliveryOut.from_record(i) for i in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@deliveries_router.get(
    "/{delivery_id}", response_model=NotificationDeliveryOut
)
def get_delivery(
    delivery_id: UUID,
    service: Annotated[
        NotificationDeliveryQueryService, Depends(get_delivery_service)
    ],
) -> NotificationDeliveryOut:
    try:
        return NotificationDeliveryOut.from_record(service.get(delivery_id))
    except DeliveryNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception:
        logger.exception("get_delivery failed")
        raise _safe_db() from None


@deliveries_router.get(
    "/{delivery_id}/attempts",
    response_model=CollectionOut[DeliveryAttemptOut],
)
def list_delivery_attempts(
    delivery_id: UUID,
    service: Annotated[
        NotificationDeliveryQueryService, Depends(get_delivery_service)
    ],
) -> CollectionOut[DeliveryAttemptOut]:
    try:
        items = service.list_attempts(delivery_id)
    except DeliveryNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception:
        logger.exception("list_delivery_attempts failed")
        raise _safe_db() from None
    return CollectionOut[DeliveryAttemptOut](
        items=[DeliveryAttemptOut.from_record(i) for i in items],
        total=len(items),
        limit=len(items),
        offset=0,
    )


@deliveries_router.post(
    "/{delivery_id}/retry",
    response_model=NotificationDeliveryOut,
)
def retry_delivery(
    delivery_id: UUID,
    service: Annotated[
        NotificationDeliveryQueryService, Depends(get_delivery_service)
    ],
) -> NotificationDeliveryOut:
    try:
        return NotificationDeliveryOut.from_record(
            service.manual_retry(delivery_id)
        )
    except DeliveryNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NotificationConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        logger.exception("retry_delivery failed")
        raise _safe_db() from None


@alert_deliveries_router.get(
    "/{alert_id}/deliveries",
    response_model=CollectionOut[NotificationDeliveryOut],
)
def list_alert_deliveries(
    alert_id: UUID,
    service: Annotated[
        NotificationDeliveryQueryService, Depends(get_delivery_service)
    ],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CollectionOut[NotificationDeliveryOut]:
    try:
        page = service.list(alert_id=alert_id, limit=limit, offset=offset)
    except Exception:
        logger.exception("list_alert_deliveries failed")
        raise _safe_db() from None
    return CollectionOut[NotificationDeliveryOut](
        items=[NotificationDeliveryOut.from_record(i) for i in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
