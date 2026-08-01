"""Read/write service for spatial zones and occupancy queries (v0.6.0)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from storage.entity_records import PageResult
from storage.entity_repository import EntityRepository
from storage.entity_zone_session_repository import EntityZoneSessionRepository
from storage.spatial_geometry import (
    GeometryError,
    rectangle_vertices,
    validate_rectangle_vertices,
)
from storage.zone_orm import ZoneSessionStatus
from storage.zone_records import (
    EntityZoneSessionRecord,
    SessionListFilter,
    ZoneCreate,
    ZoneListFilter,
    ZoneOccupancy,
    ZoneOccupancyEntity,
    ZoneRecord,
    ZoneUpdate,
)
from storage.zone_repository import ZoneConflictError, ZoneRepository


class ZoneNotFoundError(LookupError):
    """Raised when a zone id does not exist."""


class ZoneQueryValidationError(ValueError):
    """Raised when zone query/mutation parameters are invalid."""


class ZoneConflictServiceError(LookupError):
    """Raised when a zone name conflicts for a camera."""


@dataclass(frozen=True, slots=True)
class ZoneQueryLimits:
    """Pagination bounds for zone collections."""

    default_limit: int = 50
    maximum_limit: int = 200
    maximum_zones_per_camera: int = 10


class ZoneQueryService:
    """Validate inputs and orchestrate zone/session repositories."""

    def __init__(
        self,
        zone_repository: ZoneRepository,
        session_repository: EntityZoneSessionRepository,
        entity_repository: EntityRepository,
        *,
        limits: ZoneQueryLimits | None = None,
        spatial_service: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._zones = zone_repository
        self._sessions = session_repository
        self._entities = entity_repository
        self._limits = limits or ZoneQueryLimits()
        self._spatial = spatial_service
        self._logger = logger or logging.getLogger(__name__)

    def list_zones(
        self,
        *,
        camera_id: str | None = None,
        enabled: bool | None = None,
        limit: int | None = None,
        offset: int = 0,
        sort: str = "asc",
    ) -> PageResult:
        resolved_limit = self._resolve_limit(limit)
        if offset < 0:
            raise ZoneQueryValidationError("offset must be >= 0.")
        sort_norm = str(sort).strip().lower()
        if sort_norm not in {"asc", "desc"}:
            raise ZoneQueryValidationError("sort must be 'asc' or 'desc'.")

        return self._zones.list_zones(
            ZoneListFilter(
                camera_id=camera_id.strip() if camera_id else None,
                enabled=enabled,
                limit=resolved_limit,
                offset=offset,
                sort=sort_norm,
            )
        )

    def get_zone(self, zone_id: UUID) -> ZoneRecord:
        record = self._zones.get_by_id(zone_id)
        if record is None:
            raise ZoneNotFoundError(f"Zone not found: {zone_id}")
        return record

    def create_zone(
        self,
        *,
        name: str,
        camera_id: str,
        x_min: float | None = None,
        y_min: float | None = None,
        x_max: float | None = None,
        y_max: float | None = None,
        vertices: list[dict[str, float]] | None = None,
        enabled: bool = True,
        entity_type_filters: list[str] | None = None,
        min_confidence: float | None = None,
        position_strategy: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ZoneRecord:
        camera = str(camera_id).strip()
        if not camera:
            raise ZoneQueryValidationError("camera_id is required.")

        count = self._zones.count_for_camera(camera)
        if count >= self._limits.maximum_zones_per_camera:
            raise ZoneQueryValidationError(
                f"maximum zones per camera is "
                f"{self._limits.maximum_zones_per_camera}."
            )

        try:
            resolved_vertices = self._resolve_vertices(
                x_min=x_min,
                y_min=y_min,
                x_max=x_max,
                y_max=y_max,
                vertices=vertices,
            )
        except GeometryError as error:
            raise ZoneQueryValidationError(str(error)) from error

        try:
            record = self._zones.create(
                ZoneCreate(
                    name=name,
                    camera_id=camera,
                    vertices=resolved_vertices,
                    enabled=enabled,
                    entity_type_filters=list(entity_type_filters or []),
                    min_confidence=min_confidence,
                    position_strategy=position_strategy,
                    metadata=dict(metadata or {}),
                )
            )
        except ZoneConflictError as error:
            raise ZoneConflictServiceError(str(error)) from error
        except (ValueError, GeometryError) as error:
            raise ZoneQueryValidationError(str(error)) from error

        self._invalidate_spatial_cache(camera)
        return record

    def update_zone(
        self,
        zone_id: UUID,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        entity_type_filters: list[str] | None = None,
        min_confidence: float | None = None,
        clear_min_confidence: bool = False,
        position_strategy: str | None = None,
        clear_position_strategy: bool = False,
        x_min: float | None = None,
        y_min: float | None = None,
        x_max: float | None = None,
        y_max: float | None = None,
        vertices: list[dict[str, float]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ZoneRecord:
        existing = self.get_zone(zone_id)

        resolved_vertices: list[dict[str, float]] | None = None
        if any(v is not None for v in (x_min, y_min, x_max, y_max)) or vertices:
            try:
                resolved_vertices = self._resolve_vertices(
                    x_min=x_min,
                    y_min=y_min,
                    x_max=x_max,
                    y_max=y_max,
                    vertices=vertices,
                )
            except GeometryError as error:
                raise ZoneQueryValidationError(str(error)) from error

        try:
            record = self._zones.update(
                zone_id,
                ZoneUpdate(
                    name=name,
                    enabled=enabled,
                    entity_type_filters=entity_type_filters,
                    min_confidence=min_confidence,
                    clear_min_confidence=clear_min_confidence,
                    position_strategy=position_strategy,
                    clear_position_strategy=clear_position_strategy,
                    vertices=resolved_vertices,
                    metadata=metadata,
                ),
            )
        except LookupError as error:
            raise ZoneNotFoundError(str(error)) from error
        except ZoneConflictError as error:
            raise ZoneConflictServiceError(str(error)) from error
        except (ValueError, GeometryError) as error:
            raise ZoneQueryValidationError(str(error)) from error

        self._invalidate_spatial_cache(existing.camera_id)
        if record.camera_id != existing.camera_id:
            self._invalidate_spatial_cache(record.camera_id)
        return record

    def get_occupancy(self, zone_id: UUID) -> ZoneOccupancy:
        zone = self.get_zone(zone_id)
        open_sessions = self._sessions.list_open_for_zone(zone_id)
        now = datetime.now(timezone.utc)
        entities: list[ZoneOccupancyEntity] = []
        for sess in open_sessions:
            entity = self._entities.get_by_id(sess.entity_id)
            label = entity.label if entity is not None else "unknown"
            entities.append(
                ZoneOccupancyEntity(
                    entity_id=sess.entity_id,
                    entity_type=label,
                    label=label,
                    camera_id=entity.camera_id if entity else sess.camera_id,
                    status=entity.status.value if entity else "unknown",
                    session_id=sess.id,
                    entered_at=sess.entered_at,
                    last_seen_at=sess.last_seen_at,
                    dwell_seconds=sess.dwell_seconds(now=now),
                    average_confidence=(
                        entity.average_confidence if entity else None
                    ),
                    track_id=entity.track_id if entity else None,
                )
            )

        updated_at = now
        if open_sessions:
            updated_at = max(
                (s.last_seen_at for s in open_sessions),
                default=now,
            )
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)

        return ZoneOccupancy(
            zone_id=zone.id,
            zone_name=zone.name,
            camera_id=zone.camera_id,
            occupancy=len(entities),
            entities=entities,
            updated_at=updated_at,
        )

    def list_zone_entities(
        self,
        zone_id: UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> PageResult:
        """Entities currently in the zone (open sessions)."""

        occupancy = self.get_occupancy(zone_id)
        resolved_limit = self._resolve_limit(limit)
        if offset < 0:
            raise ZoneQueryValidationError("offset must be >= 0.")
        items = occupancy.entities[offset : offset + resolved_limit]
        return PageResult(
            items=items,
            total=len(occupancy.entities),
            limit=resolved_limit,
            offset=offset,
        )

    def list_zone_sessions(
        self,
        zone_id: UUID,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        sort: str = "desc",
    ) -> PageResult:
        self.get_zone(zone_id)
        return self._list_sessions(
            zone_id=zone_id,
            status=status,
            limit=limit,
            offset=offset,
            sort=sort,
        )

    def list_entity_zones(
        self,
        entity_id: UUID,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        sort: str = "desc",
    ) -> PageResult:
        entity = self._entities.get_by_id(entity_id)
        if entity is None:
            raise LookupError(f"Entity not found: {entity_id}")
        return self._list_sessions(
            entity_id=entity_id,
            status=status,
            limit=limit,
            offset=offset,
            sort=sort,
        )

    def _list_sessions(
        self,
        *,
        zone_id: UUID | None = None,
        entity_id: UUID | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        sort: str = "desc",
    ) -> PageResult:
        resolved_limit = self._resolve_limit(limit)
        if offset < 0:
            raise ZoneQueryValidationError("offset must be >= 0.")
        sort_norm = str(sort).strip().lower()
        if sort_norm not in {"asc", "desc"}:
            raise ZoneQueryValidationError("sort must be 'asc' or 'desc'.")

        status_enum: ZoneSessionStatus | None = None
        if status is not None and str(status).strip():
            text = str(status).strip().lower()
            if text not in {"open", "closed"}:
                raise ZoneQueryValidationError(
                    "status must be 'open' or 'closed'."
                )
            status_enum = ZoneSessionStatus(text)

        return self._sessions.list_sessions(
            SessionListFilter(
                zone_id=zone_id,
                entity_id=entity_id,
                status=status_enum,
                limit=resolved_limit,
                offset=offset,
                sort=sort_norm,
            )
        )

    def _resolve_limit(self, limit: int | None) -> int:
        value = self._limits.default_limit if limit is None else int(limit)
        if value < 1:
            raise ZoneQueryValidationError("limit must be an integer >= 1.")
        if value > self._limits.maximum_limit:
            raise ZoneQueryValidationError(
                f"limit cannot exceed {self._limits.maximum_limit}."
            )
        return value

    @staticmethod
    def _resolve_vertices(
        *,
        x_min: float | None,
        y_min: float | None,
        x_max: float | None,
        y_max: float | None,
        vertices: list[dict[str, float]] | None,
    ) -> list[dict[str, float]]:
        if vertices is not None:
            return validate_rectangle_vertices(vertices)
        if None in (x_min, y_min, x_max, y_max):
            raise GeometryError(
                "provide either vertices or x_min, y_min, x_max, y_max"
            )
        return rectangle_vertices(
            float(x_min),
            float(y_min),
            float(x_max),
            float(y_max),
        )

    def _invalidate_spatial_cache(self, camera_id: str) -> None:
        if self._spatial is not None and hasattr(
            self._spatial, "invalidate_zone_cache"
        ):
            self._spatial.invalidate_zone_cache(camera_id)
