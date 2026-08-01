"""Read-only timeline HTTP routes."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.deps import get_query_service, get_timeline_service
from api.timeline_schemas import TimelineEventOut, TimelinePageOut
from services.entity_query_service import (
    EntityNotFoundError,
    EntityQueryService,
)
from services.timeline_service import (
    TimelineNotFoundError,
    TimelineService,
    TimelineValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/timeline", tags=["timeline"])
entity_timeline_router = APIRouter(
    prefix="/api/v1/entities",
    tags=["timeline"],
)


def _validation_error(error: TimelineValidationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(error),
    )


def _not_found(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=detail,
    )


def _safe_db_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Timeline service temporarily unavailable.",
    )


@router.get(
    "",
    response_model=TimelinePageOut,
    summary="List activity timeline events",
)
def list_timeline(
    service: Annotated[TimelineService, Depends(get_timeline_service)],
    occurred_after: Annotated[datetime | None, Query()] = None,
    occurred_before: Annotated[datetime | None, Query()] = None,
    entity_id: Annotated[UUID | None, Query()] = None,
    event_type: Annotated[
        list[str] | None,
        Query(
            description=(
                "Event types to include. Default: entity_created, "
                "entity_closed, zone_entered, zone_exited, "
                "zone_occupancy_changed. Repeat to pass multiple values. "
                "Include observation_recorded to project observations."
            ),
        ),
    ] = None,
    camera_id: Annotated[str | None, Query()] = None,
    entity_type: Annotated[str | None, Query()] = None,
    zone_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query(description="asc or desc")] = "desc",
) -> TimelinePageOut:
    try:
        page = service.list_timeline(
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            entity_id=entity_id,
            event_type=event_type,
            camera_id=camera_id,
            entity_type=entity_type,
            zone_id=zone_id,
            limit=limit,
            cursor=cursor,
            sort=sort,
        )
    except TimelineValidationError as error:
        raise _validation_error(error) from error
    except Exception:
        logger.exception("list_timeline failed")
        raise _safe_db_error() from None

    return TimelinePageOut.from_page(page)


@router.get(
    "/{event_id:path}",
    response_model=TimelineEventOut,
    summary="Get one timeline event by stable id",
)
def get_timeline_event(
    event_id: str,
    service: Annotated[TimelineService, Depends(get_timeline_service)],
) -> TimelineEventOut:
    try:
        event = service.get_event(event_id)
    except TimelineValidationError as error:
        raise _validation_error(error) from error
    except TimelineNotFoundError as error:
        raise _not_found(str(error)) from error
    except Exception:
        logger.exception("get_timeline_event failed event_id=%s", event_id)
        raise _safe_db_error() from None

    return TimelineEventOut.from_event(event)


@entity_timeline_router.get(
    "/{entity_id}/timeline",
    response_model=TimelinePageOut,
    summary="List timeline events for one entity",
)
def list_entity_timeline(
    entity_id: UUID,
    service: Annotated[TimelineService, Depends(get_timeline_service)],
    _: Annotated[EntityQueryService, Depends(get_query_service)],
    occurred_after: Annotated[datetime | None, Query()] = None,
    occurred_before: Annotated[datetime | None, Query()] = None,
    event_type: Annotated[list[str] | None, Query()] = None,
    camera_id: Annotated[str | None, Query()] = None,
    entity_type: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query()] = "desc",
) -> TimelinePageOut:
    try:
        page = service.list_entity_timeline(
            entity_id,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            event_type=event_type,
            camera_id=camera_id,
            entity_type=entity_type,
            limit=limit,
            cursor=cursor,
            sort=sort,
        )
    except EntityNotFoundError as error:
        raise _not_found(str(error)) from error
    except TimelineValidationError as error:
        raise _validation_error(error) from error
    except Exception:
        logger.exception(
            "list_entity_timeline failed entity_id=%s",
            entity_id,
        )
        raise _safe_db_error() from None

    return TimelinePageOut.from_page(page)
