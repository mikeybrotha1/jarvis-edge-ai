"""Operational readiness, status, and retention control routes (v0.10.0)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from api.ops_schemas import (
    OpsStatusOut,
    ReadyOut,
    RetentionDomainResultOut,
    RetentionManualTriggerOut,
    RetentionRunSummaryOut,
    RetentionStatusOut,
)
from services.ops.retention_control import RetentionControlService
from services.ops.retention_worker import (
    RetentionGuardError,
    RetentionRunSummary,
)
from services.ops.status import OpsStatusCollector

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ops"])


def get_ops_collector(request: Request) -> OpsStatusCollector:
    collector = getattr(request.app.state, "ops_status_collector", None)
    if collector is None:
        # Lazy construct for tests that did not wire the collector.
        collector = OpsStatusCollector(
            session_factory=getattr(request.app.state, "session_factory", None),
        )
        request.app.state.ops_status_collector = collector
    return collector


def get_retention_control(request: Request) -> RetentionControlService:
    control = getattr(request.app.state, "retention_control", None)
    if control is None:
        control = RetentionControlService(
            getattr(request.app.state, "retention_worker", None),
            metrics=getattr(request.app.state, "ops_metrics", None),
        )
        request.app.state.retention_control = control
    return control


@router.get("/ready", response_model=ReadyOut, tags=["system"])
def readiness(
    request: Request,
    response: Response,
    collector: Annotated[OpsStatusCollector, Depends(get_ops_collector)],
) -> ReadyOut:
    """Readiness probe: process can serve traffic when the database is healthy."""

    body = collector.readiness(request.app.state)
    if not body.get("ready"):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyOut(**body)


@router.get("/api/v1/ops/status", response_model=OpsStatusOut, tags=["ops"])
def ops_status(
    request: Request,
    collector: Annotated[OpsStatusCollector, Depends(get_ops_collector)],
) -> OpsStatusOut:
    """Bounded operational status and in-process metrics (JSON)."""

    try:
        body = collector.collect(request.app.state)
    except Exception:
        logger.exception("ops status collection failed")
        # Never leak exceptions; return a sanitized unavailable document.
        body = {
            "status": "unavailable",
            "service": "jarvis-entity-query-api",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {},
            "metrics": {},
        }
    return OpsStatusOut(**body)


@router.get(
    "/api/v1/ops/retention",
    response_model=RetentionStatusOut,
    tags=["ops"],
)
def retention_status(
    control: Annotated[RetentionControlService, Depends(get_retention_control)],
) -> RetentionStatusOut:
    """Retention policy, worker state, and manual-trigger guard surface."""

    try:
        return RetentionStatusOut(**control.status_document())
    except Exception:
        logger.exception("retention status failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Retention status temporarily unavailable.",
        ) from None


@router.post(
    "/api/v1/ops/retention/dry-run",
    response_model=RetentionManualTriggerOut,
    tags=["ops"],
)
async def retention_dry_run(
    control: Annotated[RetentionControlService, Depends(get_retention_control)],
) -> RetentionManualTriggerOut:
    """Run one bounded non-destructive retention cycle (forces dry-run)."""

    try:
        summary = await control.manual_dry_run()
    except RetentionGuardError as error:
        raise HTTPException(
            status_code=error.http_status,
            detail=error.message,
        ) from None
    except Exception:
        logger.exception("retention dry-run endpoint failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Retention dry-run temporarily unavailable.",
        ) from None
    return RetentionManualTriggerOut(
        accepted=True,
        trigger="dry_run",
        summary=_summary_out(summary),
    )


@router.post(
    "/api/v1/ops/retention/run",
    response_model=RetentionManualTriggerOut,
    tags=["ops"],
)
async def retention_run(
    control: Annotated[RetentionControlService, Depends(get_retention_control)],
) -> RetentionManualTriggerOut:
    """Run one bounded destructive retention cycle (heavily guarded)."""

    try:
        summary = await control.manual_run()
    except RetentionGuardError as error:
        raise HTTPException(
            status_code=error.http_status,
            detail=error.message,
        ) from None
    except Exception:
        logger.exception("retention run endpoint failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Retention run temporarily unavailable.",
        ) from None
    return RetentionManualTriggerOut(
        accepted=True,
        trigger="run",
        summary=_summary_out(summary),
    )


def _summary_out(summary: RetentionRunSummary) -> RetentionRunSummaryOut:
    domains = [
        RetentionDomainResultOut(
            domain=d.domain,
            dry_run=d.dry_run,
            cutoff=d.cutoff,
            eligible_total=d.eligible_total,
            batches=d.batches,
            rows_examined=d.rows_examined,
            rows_deleted=d.rows_deleted,
            rows_skipped=d.rows_skipped,
            duration_ms=d.duration_ms,
            status=d.status,
            error=d.error,
        )
        for d in summary.domains
    ]
    return RetentionRunSummaryOut(
        dry_run=summary.dry_run,
        started_at=summary.started_at,
        completed_at=summary.completed_at,
        duration_ms=summary.duration_ms,
        rows_examined=summary.rows_examined,
        rows_deleted=summary.rows_deleted,
        rows_skipped=summary.rows_skipped,
        status=summary.status,
        error=summary.error,
        domains=domains,
    )
