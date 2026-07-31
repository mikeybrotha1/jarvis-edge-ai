"""Read-only entity query routes."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.deps import get_query_service
from api.schemas import CollectionOut, EntityOut, ObservationOut
from services.entity_query_service import (
    EntityNotFoundError,
    EntityQueryService,
    QueryValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities", tags=["entities"])


def _http_error_from_validation(error: QueryValidationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(error),
    )


def _http_error_from_not_found(error: EntityNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=str(error),
    )


def _safe_database_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Entity query service temporarily unavailable.",
    )


@router.get(
    "",
    response_model=CollectionOut[EntityOut],
    summary="List entities",
)
def list_entities(
    service: Annotated[EntityQueryService, Depends(get_query_service)],
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="active or closed"),
    ] = None,
    entity_type: Annotated[
        str | None,
        Query(description="Detector label (maps to entity label)"),
    ] = None,
    camera_id: Annotated[str | None, Query()] = None,
    seen_after: Annotated[datetime | None, Query()] = None,
    seen_before: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query(description="asc or desc by last_seen")] = (
        "desc"
    ),
) -> CollectionOut[EntityOut]:
    try:
        page = service.list_entities(
            status=status_filter,
            entity_type=entity_type,
            camera_id=camera_id,
            seen_after=seen_after,
            seen_before=seen_before,
            limit=limit,
            offset=offset,
            sort=sort,
        )
    except QueryValidationError as error:
        raise _http_error_from_validation(error) from error
    except Exception:
        logger.exception("list_entities failed")
        raise _safe_database_error() from None

    return CollectionOut[EntityOut].from_page(
        page,
        map_item=EntityOut.from_record,
    )


@router.get(
    "/active",
    response_model=CollectionOut[EntityOut],
    summary="List active entities",
)
def list_active_entities(
    service: Annotated[EntityQueryService, Depends(get_query_service)],
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = "desc",
) -> CollectionOut[EntityOut]:
    try:
        page = service.list_active_entities(
            limit=limit,
            offset=offset,
            sort=sort,
        )
    except QueryValidationError as error:
        raise _http_error_from_validation(error) from error
    except Exception:
        logger.exception("list_active_entities failed")
        raise _safe_database_error() from None

    return CollectionOut[EntityOut].from_page(
        page,
        map_item=EntityOut.from_record,
    )


@router.get(
    "/recent",
    response_model=CollectionOut[EntityOut],
    summary="List recently seen entities",
)
def list_recent_entities(
    service: Annotated[EntityQueryService, Depends(get_query_service)],
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CollectionOut[EntityOut]:
    try:
        page = service.list_recent_entities(limit=limit, offset=offset)
    except QueryValidationError as error:
        raise _http_error_from_validation(error) from error
    except Exception:
        logger.exception("list_recent_entities failed")
        raise _safe_database_error() from None

    return CollectionOut[EntityOut].from_page(
        page,
        map_item=EntityOut.from_record,
    )


@router.get(
    "/{entity_id}",
    response_model=EntityOut,
    summary="Get one entity by ID",
)
def get_entity(
    entity_id: UUID,
    service: Annotated[EntityQueryService, Depends(get_query_service)],
) -> EntityOut:
    try:
        record = service.get_entity(entity_id)
    except EntityNotFoundError as error:
        raise _http_error_from_not_found(error) from error
    except Exception:
        logger.exception("get_entity failed entity_id=%s", entity_id)
        raise _safe_database_error() from None

    return EntityOut.from_record(record)


@router.get(
    "/{entity_id}/observations",
    response_model=CollectionOut[ObservationOut],
    summary="List observations for one entity",
)
def list_entity_observations(
    entity_id: UUID,
    service: Annotated[EntityQueryService, Depends(get_query_service)],
    seen_after: Annotated[datetime | None, Query()] = None,
    seen_before: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = "desc",
) -> CollectionOut[ObservationOut]:
    try:
        page = service.list_observations(
            entity_id,
            seen_after=seen_after,
            seen_before=seen_before,
            limit=limit,
            offset=offset,
            sort=sort,
        )
    except EntityNotFoundError as error:
        raise _http_error_from_not_found(error) from error
    except QueryValidationError as error:
        raise _http_error_from_validation(error) from error
    except Exception:
        logger.exception(
            "list_entity_observations failed entity_id=%s",
            entity_id,
        )
        raise _safe_database_error() from None

    return CollectionOut[ObservationOut].from_page(
        page,
        map_item=ObservationOut.from_record,
    )
