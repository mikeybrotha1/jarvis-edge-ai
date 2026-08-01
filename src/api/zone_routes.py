"""Spatial zone REST routes (v0.6.0)."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.deps import get_zone_query_service
from api.zone_schemas import (
    CollectionOut,
    ZoneCreateIn,
    ZoneOccupancyEntityOut,
    ZoneOccupancyOut,
    ZoneOut,
    ZonePatchIn,
    ZoneSessionOut,
)
from services.zone_query_service import (
    ZoneConflictServiceError,
    ZoneNotFoundError,
    ZoneQueryService,
    ZoneQueryValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/zones", tags=["zones"])
entity_zones_router = APIRouter(prefix="/api/v1/entities", tags=["zones"])


def _validation_error(error: ZoneQueryValidationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(error),
    )


def _not_found(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=detail,
    )


def _conflict(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=detail,
    )


def _safe_db_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Zone service temporarily unavailable.",
    )


@router.get(
    "",
    response_model=CollectionOut[ZoneOut],
    summary="List zones",
)
def list_zones(
    service: Annotated[ZoneQueryService, Depends(get_zone_query_service)],
    camera_id: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = "asc",
) -> CollectionOut[ZoneOut]:
    try:
        page = service.list_zones(
            camera_id=camera_id,
            enabled=enabled,
            limit=limit,
            offset=offset,
            sort=sort,
        )
    except ZoneQueryValidationError as error:
        raise _validation_error(error) from error
    except Exception:
        logger.exception("list_zones failed")
        raise _safe_db_error() from None

    return CollectionOut[ZoneOut](
        items=[ZoneOut.from_record(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    response_model=ZoneOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a camera-specific zone",
)
def create_zone(
    body: ZoneCreateIn,
    service: Annotated[ZoneQueryService, Depends(get_zone_query_service)],
) -> ZoneOut:
    try:
        record = service.create_zone(
            name=body.name,
            camera_id=body.camera_id,
            x_min=body.x_min,
            y_min=body.y_min,
            x_max=body.x_max,
            y_max=body.y_max,
            vertices=body.vertices,
            enabled=body.enabled,
            entity_type_filters=body.entity_type_filters,
            min_confidence=body.min_confidence,
            position_strategy=body.position_strategy,
            metadata=body.metadata,
        )
    except ZoneQueryValidationError as error:
        raise _validation_error(error) from error
    except ZoneConflictServiceError as error:
        raise _conflict(str(error)) from error
    except Exception:
        logger.exception("create_zone failed")
        raise _safe_db_error() from None

    return ZoneOut.from_record(record)


@router.get(
    "/{zone_id}",
    response_model=ZoneOut,
    summary="Get one zone",
)
def get_zone(
    zone_id: UUID,
    service: Annotated[ZoneQueryService, Depends(get_zone_query_service)],
) -> ZoneOut:
    try:
        record = service.get_zone(zone_id)
    except ZoneNotFoundError as error:
        raise _not_found(str(error)) from error
    except Exception:
        logger.exception("get_zone failed")
        raise _safe_db_error() from None
    return ZoneOut.from_record(record)


@router.patch(
    "/{zone_id}",
    response_model=ZoneOut,
    summary="Update or disable a zone",
)
def patch_zone(
    zone_id: UUID,
    body: ZonePatchIn,
    service: Annotated[ZoneQueryService, Depends(get_zone_query_service)],
) -> ZoneOut:
    try:
        record = service.update_zone(
            zone_id,
            name=body.name,
            enabled=body.enabled,
            entity_type_filters=body.entity_type_filters,
            min_confidence=body.min_confidence,
            clear_min_confidence=body.clear_min_confidence,
            position_strategy=body.position_strategy,
            clear_position_strategy=body.clear_position_strategy,
            x_min=body.x_min,
            y_min=body.y_min,
            x_max=body.x_max,
            y_max=body.y_max,
            vertices=body.vertices,
            metadata=body.metadata,
        )
    except ZoneNotFoundError as error:
        raise _not_found(str(error)) from error
    except ZoneQueryValidationError as error:
        raise _validation_error(error) from error
    except ZoneConflictServiceError as error:
        raise _conflict(str(error)) from error
    except Exception:
        logger.exception("patch_zone failed")
        raise _safe_db_error() from None
    return ZoneOut.from_record(record)


@router.get(
    "/{zone_id}/occupancy",
    response_model=ZoneOccupancyOut,
    summary="Current zone occupancy",
)
def get_zone_occupancy(
    zone_id: UUID,
    service: Annotated[ZoneQueryService, Depends(get_zone_query_service)],
) -> ZoneOccupancyOut:
    try:
        record = service.get_occupancy(zone_id)
    except ZoneNotFoundError as error:
        raise _not_found(str(error)) from error
    except Exception:
        logger.exception("get_zone_occupancy failed")
        raise _safe_db_error() from None
    return ZoneOccupancyOut.from_record(record)


@router.get(
    "/{zone_id}/entities",
    response_model=CollectionOut[ZoneOccupancyEntityOut],
    summary="Entities currently in a zone",
)
def list_zone_entities(
    zone_id: UUID,
    service: Annotated[ZoneQueryService, Depends(get_zone_query_service)],
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CollectionOut[ZoneOccupancyEntityOut]:
    try:
        page = service.list_zone_entities(
            zone_id,
            limit=limit,
            offset=offset,
        )
    except ZoneNotFoundError as error:
        raise _not_found(str(error)) from error
    except ZoneQueryValidationError as error:
        raise _validation_error(error) from error
    except Exception:
        logger.exception("list_zone_entities failed")
        raise _safe_db_error() from None

    return CollectionOut[ZoneOccupancyEntityOut](
        items=[ZoneOccupancyEntityOut.from_record(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{zone_id}/sessions",
    response_model=CollectionOut[ZoneSessionOut],
    summary="Historical entity-zone sessions for a zone",
)
def list_zone_sessions(
    zone_id: UUID,
    service: Annotated[ZoneQueryService, Depends(get_zone_query_service)],
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="open or closed"),
    ] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = "desc",
) -> CollectionOut[ZoneSessionOut]:
    try:
        page = service.list_zone_sessions(
            zone_id,
            status=status_filter,
            limit=limit,
            offset=offset,
            sort=sort,
        )
    except ZoneNotFoundError as error:
        raise _not_found(str(error)) from error
    except ZoneQueryValidationError as error:
        raise _validation_error(error) from error
    except Exception:
        logger.exception("list_zone_sessions failed")
        raise _safe_db_error() from None

    return CollectionOut[ZoneSessionOut](
        items=[ZoneSessionOut.from_record(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@entity_zones_router.get(
    "/{entity_id}/zones",
    response_model=CollectionOut[ZoneSessionOut],
    summary="Zones visited by an entity (session history)",
)
def list_entity_zones(
    entity_id: UUID,
    service: Annotated[ZoneQueryService, Depends(get_zone_query_service)],
    status_filter: Annotated[
        str | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = "desc",
) -> CollectionOut[ZoneSessionOut]:
    try:
        page = service.list_entity_zones(
            entity_id,
            status=status_filter,
            limit=limit,
            offset=offset,
            sort=sort,
        )
    except LookupError as error:
        raise _not_found(str(error)) from error
    except ZoneQueryValidationError as error:
        raise _validation_error(error) from error
    except Exception:
        logger.exception("list_entity_zones failed")
        raise _safe_db_error() from None

    return CollectionOut[ZoneSessionOut](
        items=[ZoneSessionOut.from_record(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
