"""Read-only timeline service over entity memory projections."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from services.entity_query_service import EntityNotFoundError
from storage.entity_repository import EntityRepository
from storage.timeline_cursor import CursorError, decode_cursor
from storage.timeline_models import (
    ALL_TIMELINE_EVENT_TYPES,
    DEFAULT_TIMELINE_EVENT_TYPES,
    TimelineEvent,
    TimelineEventType,
    TimelineListFilter,
    TimelinePage,
)
from storage.timeline_repository import TimelineRepository


class TimelineNotFoundError(LookupError):
    """Raised when a timeline event id cannot be resolved."""


class TimelineValidationError(ValueError):
    """Raised when timeline query parameters are invalid."""


@dataclass(frozen=True, slots=True)
class TimelineLimits:
    """Pagination bounds for timeline collections."""

    default_limit: int = 50
    maximum_limit: int = 200


class TimelineService:
    """Validate filters and project timeline pages via TimelineRepository."""

    def __init__(
        self,
        timeline_repository: TimelineRepository,
        entity_repository: EntityRepository,
        *,
        limits: TimelineLimits | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._timeline = timeline_repository
        self._entities = entity_repository
        self._limits = limits or TimelineLimits()
        self._logger = logger or logging.getLogger(__name__)

    def list_timeline(
        self,
        *,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        entity_id: UUID | None = None,
        event_type: list[str] | None = None,
        camera_id: str | None = None,
        entity_type: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        sort: str = "desc",
        require_entity: bool = False,
    ) -> TimelinePage:
        """List timeline events with filters and cursor pagination.

        Default ``event_type`` is lifecycle-only (``entity_created``,
        ``entity_closed``). Observations appear only when
        ``observation_recorded`` is requested explicitly.
        """

        if require_entity and entity_id is not None:
            self._require_entity(entity_id)

        resolved_limit = self._resolve_limit(limit)
        resolved_sort = self._resolve_sort(sort)
        resolved_types = self._resolve_event_types(event_type)
        after = self._normalise_timestamp(occurred_after, field="occurred_after")
        before = self._normalise_timestamp(
            occurred_before,
            field="occurred_before",
        )
        self._validate_date_range(after, before)
        decoded_cursor = self._resolve_cursor(cursor)

        filters = TimelineListFilter(
            event_types=resolved_types,
            occurred_after=after,
            occurred_before=before,
            entity_id=entity_id,
            camera_id=camera_id.strip() if camera_id else None,
            entity_type=entity_type.strip() if entity_type else None,
            limit=resolved_limit,
            cursor=decoded_cursor,
            sort=resolved_sort,
        )

        try:
            return self._timeline.list_events(filters)
        except Exception:
            self._logger.exception("Database failure while listing timeline")
            raise

    def list_entity_timeline(
        self,
        entity_id: UUID,
        **kwargs: object,
    ) -> TimelinePage:
        """Entity-scoped timeline; raises if the entity does not exist."""

        return self.list_timeline(
            entity_id=entity_id,
            require_entity=True,
            **kwargs,  # type: ignore[arg-type]
        )

    def get_event(self, event_id: str) -> TimelineEvent:
        """Return one timeline event or raise TimelineNotFoundError."""

        if not event_id or not str(event_id).strip():
            raise TimelineValidationError("event_id cannot be empty.")

        try:
            event = self._timeline.get_event_by_id(str(event_id).strip())
        except Exception:
            self._logger.exception(
                "Database failure while loading timeline event_id=%s",
                event_id,
            )
            raise

        if event is None:
            raise TimelineNotFoundError(
                f"Timeline event not found: {event_id}"
            )
        return event

    def _require_entity(self, entity_id: UUID) -> None:
        try:
            record = self._entities.get_by_id(entity_id)
        except Exception:
            self._logger.exception(
                "Database failure while checking entity_id=%s",
                entity_id,
            )
            raise
        if record is None:
            raise EntityNotFoundError(f"Entity not found: {entity_id}")

    def _resolve_limit(self, limit: int | None) -> int:
        value = (
            self._limits.default_limit if limit is None else int(limit)
        )
        if value < 1:
            raise TimelineValidationError("limit must be an integer >= 1.")
        if value > self._limits.maximum_limit:
            raise TimelineValidationError(
                f"limit cannot exceed {self._limits.maximum_limit}."
            )
        return value

    @staticmethod
    def _resolve_sort(sort: str) -> str:
        normalised = str(sort).strip().lower()
        if normalised not in {"asc", "desc"}:
            raise TimelineValidationError(
                "sort must be either 'asc' or 'desc'."
            )
        return normalised

    def _resolve_event_types(
        self,
        event_type: list[str] | None,
    ) -> tuple[TimelineEventType, ...]:
        if not event_type:
            return DEFAULT_TIMELINE_EVENT_TYPES

        resolved: list[TimelineEventType] = []
        seen: set[str] = set()
        for raw in event_type:
            text = str(raw).strip().lower()
            if text not in ALL_TIMELINE_EVENT_TYPES:
                allowed = ", ".join(sorted(ALL_TIMELINE_EVENT_TYPES))
                raise TimelineValidationError(
                    f"event_type must be one of: {allowed}."
                )
            if text in seen:
                continue
            seen.add(text)
            resolved.append(TimelineEventType(text))

        if not resolved:
            return DEFAULT_TIMELINE_EVENT_TYPES
        return tuple(resolved)

    def _resolve_cursor(self, cursor: str | None):
        if cursor is None or not str(cursor).strip():
            return None
        try:
            return decode_cursor(cursor)
        except CursorError as error:
            raise TimelineValidationError(str(error)) from error

    @staticmethod
    def _normalise_timestamp(
        value: datetime | None,
        *,
        field: str,
    ) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TimelineValidationError(
                f"{field} must be an ISO 8601 timestamp."
            )
        # Naive timestamps are treated as UTC (documented API convention).
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _validate_date_range(
        occurred_after: datetime | None,
        occurred_before: datetime | None,
    ) -> None:
        if (
            occurred_after is not None
            and occurred_before is not None
            and occurred_after > occurred_before
        ):
            raise TimelineValidationError(
                "occurred_after cannot be later than occurred_before."
            )
