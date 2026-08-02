"""REST routes for alert rules and alerts (v0.8.0)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from api.alert_schemas import AlertOut, AlertRuleOut, CollectionOut
from services.alerts.rule_service import (
    AlertNotFoundError,
    AlertQueryService,
    AlertRuleService,
)
from services.alerts.rule_validation import RuleValidationError
from storage.alert_repositories import AlertConflictError

logger = logging.getLogger(__name__)

rules_router = APIRouter(prefix="/api/v1/alert-rules", tags=["alerts"])
alerts_router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])



def get_rule_service(request: Request) -> AlertRuleService:
    service = getattr(request.app.state, "alert_rule_service", None)
    if service is None:
        raise RuntimeError("AlertRuleService is not configured.")
    return service


def get_alert_service(request: Request) -> AlertQueryService:
    service = getattr(request.app.state, "alert_query_service", None)
    if service is None:
        raise RuntimeError("AlertQueryService is not configured.")
    return service


def _safe_db() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Alert service temporarily unavailable.",
    )


@rules_router.get("", response_model=CollectionOut[AlertRuleOut])
def list_rules(
    service: Annotated[AlertRuleService, Depends(get_rule_service)],
    enabled: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CollectionOut[AlertRuleOut]:
    try:
        page = service.list_rules(enabled=enabled, limit=limit, offset=offset)
    except Exception:
        logger.exception("list_rules failed")
        raise _safe_db() from None
    return CollectionOut[AlertRuleOut](
        items=[AlertRuleOut.from_record(i) for i in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@rules_router.post(
    "",
    response_model=AlertRuleOut,
    status_code=status.HTTP_201_CREATED,
)
def create_rule(
    body: dict[str, Any],
    service: Annotated[AlertRuleService, Depends(get_rule_service)],
) -> AlertRuleOut:
    try:
        record = service.create_rule(body)
    except RuleValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except AlertConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        logger.exception("create_rule failed")
        raise _safe_db() from None
    return AlertRuleOut.from_record(record)


@rules_router.get("/{rule_id}", response_model=AlertRuleOut)
def get_rule(
    rule_id: UUID,
    service: Annotated[AlertRuleService, Depends(get_rule_service)],
) -> AlertRuleOut:
    try:
        return AlertRuleOut.from_record(service.get_rule(rule_id))
    except AlertNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception:
        logger.exception("get_rule failed")
        raise _safe_db() from None


@rules_router.patch("/{rule_id}", response_model=AlertRuleOut)
def patch_rule(
    rule_id: UUID,
    body: dict[str, Any],
    service: Annotated[AlertRuleService, Depends(get_rule_service)],
) -> AlertRuleOut:
    try:
        return AlertRuleOut.from_record(service.update_rule(rule_id, body))
    except AlertNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuleValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except AlertConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        logger.exception("patch_rule failed")
        raise _safe_db() from None


@alerts_router.get("", response_model=CollectionOut[AlertOut])
def list_alerts(
    service: Annotated[AlertQueryService, Depends(get_alert_service)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    rule_id: Annotated[UUID | None, Query()] = None,
    severity: Annotated[str | None, Query()] = None,
    entity_id: Annotated[UUID | None, Query()] = None,
    zone_id: Annotated[UUID | None, Query()] = None,
    camera_id: Annotated[str | None, Query()] = None,
    triggered_after: Annotated[datetime | None, Query()] = None,
    triggered_before: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = "desc",
) -> CollectionOut[AlertOut]:
    try:
        page = service.list_alerts(
            status=status_filter,
            rule_id=rule_id,
            severity=severity,
            entity_id=entity_id,
            zone_id=zone_id,
            camera_id=camera_id,
            triggered_after=triggered_after,
            triggered_before=triggered_before,
            limit=limit,
            offset=offset,
            sort=sort,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception:
        logger.exception("list_alerts failed")
        raise _safe_db() from None
    return CollectionOut[AlertOut](
        items=[AlertOut.from_record(i) for i in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@alerts_router.get("/{alert_id}", response_model=AlertOut)
def get_alert(
    alert_id: UUID,
    service: Annotated[AlertQueryService, Depends(get_alert_service)],
) -> AlertOut:
    try:
        return AlertOut.from_record(service.get_alert(alert_id))
    except AlertNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception:
        logger.exception("get_alert failed")
        raise _safe_db() from None


@alerts_router.post("/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge_alert(
    alert_id: UUID,
    service: Annotated[AlertQueryService, Depends(get_alert_service)],
) -> AlertOut:
    try:
        return AlertOut.from_record(service.acknowledge(alert_id))
    except AlertNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception:
        logger.exception("acknowledge_alert failed")
        raise _safe_db() from None


@alerts_router.post("/{alert_id}/resolve", response_model=AlertOut)
def resolve_alert(
    alert_id: UUID,
    service: Annotated[AlertQueryService, Depends(get_alert_service)],
) -> AlertOut:
    try:
        return AlertOut.from_record(service.resolve(alert_id))
    except AlertNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception:
        logger.exception("resolve_alert failed")
        raise _safe_db() from None
