"""FastAPI dependency helpers for the entity query API."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Request

from services.entity_query_service import EntityQueryService


def get_query_service(request: Request) -> EntityQueryService:
    """Return the application-scoped query service."""

    service = getattr(request.app.state, "query_service", None)
    if service is None:
        raise RuntimeError(
            "EntityQueryService is not configured on the application."
        )
    return service


def get_query_service_generator(
    request: Request,
) -> Generator[EntityQueryService, None, None]:
    """Yield the query service (hook for per-request cleanup if needed)."""

    yield get_query_service(request)
