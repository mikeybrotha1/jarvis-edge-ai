"""Read-only query service for persistent entity memory.

Purpose
-------
Expose filtered, paginated access to entities and observations via the
existing repositories. No SQLAlchemy usage outside the repository layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from storage.entity_orm import EntityStatus
from storage.entity_records import (
    EntityListFilter,
    EntityRecord,
    ObservationListFilter,
    ObservationRecord,
    PageResult,
)
from storage.entity_repository import EntityRepository
from storage.observation_repository import ObservationRepository


class EntityNotFoundError(LookupError):
    """Raised when an entity ID does not exist."""


class QueryValidationError(ValueError):
    """Raised when query parameters are invalid."""


@dataclass(frozen=True, slots=True)
class QueryLimits:
    """Pagination bounds for entity and observation collections."""

    entity_default_limit: int = 50
    entity_maximum_limit: int = 200
    observation_default_limit: int = 100
    observation_maximum_limit: int = 500


class EntityQueryService:
    """Read-only facade over entity and observation repositories."""

    def __init__(
        self,
        entity_repository: EntityRepository,
        observation_repository: ObservationRepository,
        *,
        limits: QueryLimits | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._entities = entity_repository
        self._observations = observation_repository
        self._limits = limits or QueryLimits()
        self._logger = logger or logging.getLogger(__name__)

    def get_entity(self, entity_id: UUID) -> EntityRecord:
        """Return one entity or raise :class:`EntityNotFoundError`."""

        try:
            record = self._entities.get_by_id(entity_id)
        except Exception:
            self._logger.exception(
                "Database failure while loading entity_id=%s",
                entity_id,
            )
            raise

        if record is None:
            raise EntityNotFoundError(f"Entity not found: {entity_id}")
        return record

    def list_entities(
        self,
        *,
        status: str | EntityStatus | None = None,
        entity_type: str | None = None,
        camera_id: str | None = None,
        seen_after: datetime | None = None,
        seen_before: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
        sort: str = "desc",
    ) -> PageResult:
        """List entities with filters, SQL pagination, and total count."""

        resolved_limit = self._resolve_limit(
            limit,
            default=self._limits.entity_default_limit,
            maximum=self._limits.entity_maximum_limit,
            field_name="limit",
        )
        resolved_offset = self._resolve_offset(offset)
        resolved_sort = self._resolve_sort(sort)
        resolved_status = self._resolve_status(status)
        self._validate_date_range(seen_after, seen_before)

        filters = EntityListFilter(
            status=resolved_status,
            entity_type=entity_type.strip() if entity_type else None,
            camera_id=camera_id.strip() if camera_id else None,
            seen_after=seen_after,
            seen_before=seen_before,
            limit=resolved_limit,
            offset=resolved_offset,
            sort=resolved_sort,
        )

        try:
            return self._entities.list_entities(filters)
        except Exception:
            self._logger.exception("Database failure while listing entities")
            raise

    def list_active_entities(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        sort: str = "desc",
    ) -> PageResult:
        """List entities with status active."""

        return self.list_entities(
            status=EntityStatus.ACTIVE,
            limit=limit,
            offset=offset,
            sort=sort,
        )

    def list_recent_entities(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> PageResult:
        """List entities ordered by most recent last_seen."""

        return self.list_entities(
            limit=limit,
            offset=offset,
            sort="desc",
        )

    def list_observations(
        self,
        entity_id: UUID,
        *,
        seen_after: datetime | None = None,
        seen_before: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
        sort: str = "desc",
    ) -> PageResult:
        """List observations for one entity (404 semantics via get_entity)."""

        # Ensure the entity exists before querying observations.
        self.get_entity(entity_id)

        resolved_limit = self._resolve_limit(
            limit,
            default=self._limits.observation_default_limit,
            maximum=self._limits.observation_maximum_limit,
            field_name="limit",
        )
        resolved_offset = self._resolve_offset(offset)
        resolved_sort = self._resolve_sort(sort)
        self._validate_date_range(seen_after, seen_before)

        filters = ObservationListFilter(
            entity_id=entity_id,
            seen_after=seen_after,
            seen_before=seen_before,
            limit=resolved_limit,
            offset=resolved_offset,
            sort=resolved_sort,
        )

        try:
            return self._observations.list_observations(filters)
        except Exception:
            self._logger.exception(
                "Database failure while listing observations entity_id=%s",
                entity_id,
            )
            raise

    def _resolve_limit(
        self,
        limit: int | None,
        *,
        default: int,
        maximum: int,
        field_name: str,
    ) -> int:
        value = default if limit is None else int(limit)
        if value < 1:
            raise QueryValidationError(
                f"{field_name} must be an integer >= 1."
            )
        if value > maximum:
            raise QueryValidationError(
                f"{field_name} cannot exceed {maximum}."
            )
        return value

    @staticmethod
    def _resolve_offset(offset: int) -> int:
        value = int(offset)
        if value < 0:
            raise QueryValidationError("offset must be an integer >= 0.")
        return value

    @staticmethod
    def _resolve_sort(sort: str) -> str:
        normalised = str(sort).strip().lower()
        if normalised not in {"asc", "desc"}:
            raise QueryValidationError(
                "sort must be either 'asc' or 'desc'."
            )
        return normalised

    @staticmethod
    def _resolve_status(
        status: str | EntityStatus | None,
    ) -> EntityStatus | None:
        if status is None:
            return None
        if isinstance(status, EntityStatus):
            return status

        text = str(status).strip().lower()
        try:
            return EntityStatus(text)
        except ValueError as error:
            allowed = ", ".join(item.value for item in EntityStatus)
            raise QueryValidationError(
                f"status must be one of: {allowed}."
            ) from error

    @staticmethod
    def _validate_date_range(
        seen_after: datetime | None,
        seen_before: datetime | None,
    ) -> None:
        if seen_after is not None and seen_before is not None:
            if seen_after > seen_before:
                raise QueryValidationError(
                    "seen_after cannot be later than seen_before."
                )


# Re-export record types for convenience in API layer typing.
__all__ = [
    "EntityNotFoundError",
    "EntityQueryService",
    "ObservationRecord",
    "QueryLimits",
    "QueryValidationError",
]
