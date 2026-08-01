"""FastAPI dependency helpers for the entity query and timeline APIs."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Request

from services.entity_query_service import EntityQueryService
from services.timeline_service import TimelineService
from services.zone_query_service import ZoneQueryService


def get_query_service(request: Request) -> EntityQueryService:
    """Return the application-scoped query service."""

    service = getattr(request.app.state, "query_service", None)
    if service is None:
        raise RuntimeError(
            "EntityQueryService is not configured on the application."
        )
    return service


def get_timeline_service(request: Request) -> TimelineService:
    """Return the application-scoped timeline service."""

    service = getattr(request.app.state, "timeline_service", None)
    if service is None:
        raise RuntimeError(
            "TimelineService is not configured on the application."
        )
    return service


def get_zone_query_service(request: Request) -> ZoneQueryService:
    """Return the application-scoped zone query service."""

    service = getattr(request.app.state, "zone_query_service", None)
    if service is None:
        raise RuntimeError(
            "ZoneQueryService is not configured on the application."
        )
    return service


def get_query_service_generator(
    request: Request,
) -> Generator[EntityQueryService, None, None]:
    """Yield the query service (hook for per-request cleanup if needed)."""

    yield get_query_service(request)
