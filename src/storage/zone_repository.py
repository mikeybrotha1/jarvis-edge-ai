"""Repository for spatial zone definitions (v0.6.0)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from storage.entity_records import PageResult
from storage.spatial_geometry import (
    MAX_ENTITY_TYPE_FILTERS,
    MAX_METADATA_BYTES,
    MAX_ZONE_NAME_LENGTH,
    SUPPORTED_GEOMETRY_TYPES,
    SUPPORTED_POSITION_STRATEGIES,
    GeometryError,
    validate_rectangle_vertices,
)
from storage.sqlalchemy_db import session_scope
from storage.zone_orm import Zone
from storage.zone_records import (
    ZoneCreate,
    ZoneListFilter,
    ZoneRecord,
    ZoneUpdate,
)

T = TypeVar("T")


class ZoneConflictError(LookupError):
    """Raised when a zone name conflicts for a camera."""


class ZoneRepository:
    """Read and write zone definition rows."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        data: ZoneCreate,
        *,
        session: Session | None = None,
    ) -> ZoneRecord:
        return self._with_session(session, lambda active: self._create(active, data))

    def get_by_id(
        self,
        zone_id: UUID,
        *,
        session: Session | None = None,
    ) -> ZoneRecord | None:
        def _read(active: Session) -> ZoneRecord | None:
            row = active.get(Zone, zone_id)
            if row is None:
                return None
            return self._to_record(row)

        return self._with_session(session, _read)

    def update(
        self,
        zone_id: UUID,
        data: ZoneUpdate,
        *,
        session: Session | None = None,
    ) -> ZoneRecord:
        def _write(active: Session) -> ZoneRecord:
            row = active.get(Zone, zone_id)
            if row is None:
                raise LookupError(f"Zone not found: {zone_id}")

            if data.name is not None:
                row.name = self._validate_name(data.name)
            if data.enabled is not None:
                row.enabled = bool(data.enabled)
            if data.entity_type_filters is not None:
                row.entity_type_filters = self._validate_filters(
                    data.entity_type_filters
                )
            if data.clear_min_confidence:
                row.min_confidence = None
            elif data.min_confidence is not None:
                row.min_confidence = self._validate_confidence(data.min_confidence)
            if data.clear_position_strategy:
                row.position_strategy = None
            elif data.position_strategy is not None:
                row.position_strategy = self._validate_strategy(
                    data.position_strategy
                )
            if data.vertices is not None:
                row.vertices = validate_rectangle_vertices(data.vertices)
                row.geometry_type = "rectangle"
            if data.metadata is not None:
                row.extra = self._validate_metadata(data.metadata)

            try:
                active.flush()
            except IntegrityError as error:
                raise ZoneConflictError(
                    f"Zone name already exists for camera {row.camera_id!r}"
                ) from error
            return self._to_record(row)

        return self._with_session(session, _write)

    def list_zones(
        self,
        filters: ZoneListFilter,
        *,
        session: Session | None = None,
    ) -> PageResult:
        def _read(active: Session) -> PageResult:
            conditions: list[Any] = []
            if filters.camera_id is not None:
                conditions.append(Zone.camera_id == filters.camera_id)
            if filters.enabled is not None:
                conditions.append(Zone.enabled == filters.enabled)

            count_statement = select(func.count()).select_from(Zone)
            list_statement = select(Zone)
            for condition in conditions:
                count_statement = count_statement.where(condition)
                list_statement = list_statement.where(condition)

            if filters.sort == "desc":
                list_statement = list_statement.order_by(
                    Zone.name.desc(),
                    Zone.id.desc(),
                )
            else:
                list_statement = list_statement.order_by(
                    Zone.name.asc(),
                    Zone.id.asc(),
                )

            list_statement = list_statement.offset(filters.offset).limit(
                filters.limit
            )
            total = int(active.scalar(count_statement) or 0)
            items = [
                self._to_record(row)
                for row in active.scalars(list_statement).all()
            ]
            return PageResult(
                items=items,
                total=total,
                limit=filters.limit,
                offset=filters.offset,
            )

        return self._with_session(session, _read)

    def list_enabled_for_camera(
        self,
        camera_id: str,
        *,
        session: Session | None = None,
    ) -> list[ZoneRecord]:
        def _read(active: Session) -> list[ZoneRecord]:
            statement = (
                select(Zone)
                .where(Zone.camera_id == camera_id)
                .where(Zone.enabled.is_(True))
                .order_by(Zone.name.asc())
            )
            return [self._to_record(row) for row in active.scalars(statement).all()]

        return self._with_session(session, _read)

    def count_for_camera(
        self,
        camera_id: str,
        *,
        session: Session | None = None,
    ) -> int:
        def _read(active: Session) -> int:
            statement = (
                select(func.count())
                .select_from(Zone)
                .where(Zone.camera_id == camera_id)
            )
            return int(active.scalar(statement) or 0)

        return self._with_session(session, _read)

    def _create(self, session: Session, data: ZoneCreate) -> ZoneRecord:
        name = self._validate_name(data.name)
        camera_id = str(data.camera_id).strip()
        if not camera_id:
            raise ValueError("camera_id cannot be empty.")

        geometry_type = str(data.geometry_type).strip().lower()
        if geometry_type not in SUPPORTED_GEOMETRY_TYPES:
            raise GeometryError(
                "geometry_type must be one of: "
                + ", ".join(sorted(SUPPORTED_GEOMETRY_TYPES))
            )

        vertices = validate_rectangle_vertices(data.vertices)
        filters = self._validate_filters(data.entity_type_filters)
        min_confidence = (
            self._validate_confidence(data.min_confidence)
            if data.min_confidence is not None
            else None
        )
        strategy = (
            self._validate_strategy(data.position_strategy)
            if data.position_strategy is not None
            else None
        )
        metadata = self._validate_metadata(data.metadata)

        row = Zone(
            id=uuid.uuid4(),
            name=name,
            camera_id=camera_id,
            geometry_type=geometry_type,
            vertices=vertices,
            enabled=bool(data.enabled),
            entity_type_filters=filters,
            min_confidence=min_confidence,
            position_strategy=strategy,
            extra=metadata,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as error:
            raise ZoneConflictError(
                f"Zone name already exists for camera {camera_id!r}"
            ) from error
        return self._to_record(row)

    def _with_session(
        self,
        session: Session | None,
        operation: Callable[[Session], T],
    ) -> T:
        if session is not None:
            return operation(session)
        with session_scope(self._session_factory) as owned:
            return operation(owned)

    @staticmethod
    def _to_record(row: Zone) -> ZoneRecord:
        vertices = row.vertices or []
        if isinstance(vertices, list):
            verts = [
                {"x": float(p.get("x", 0)), "y": float(p.get("y", 0))}
                if isinstance(p, dict)
                else {"x": 0.0, "y": 0.0}
                for p in vertices
            ]
        else:
            verts = []
        filters = row.entity_type_filters or []
        if not isinstance(filters, list):
            filters = []
        return ZoneRecord(
            id=row.id,
            name=row.name,
            camera_id=row.camera_id,
            geometry_type=row.geometry_type,
            vertices=verts,
            enabled=bool(row.enabled),
            entity_type_filters=[str(item) for item in filters],
            min_confidence=row.min_confidence,
            position_strategy=row.position_strategy,
            metadata=dict(row.extra or {}),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _validate_name(name: str) -> str:
        text = str(name).strip()
        if not text:
            raise ValueError("name is required and cannot be empty.")
        if len(text) > MAX_ZONE_NAME_LENGTH:
            raise ValueError(
                f"name cannot exceed {MAX_ZONE_NAME_LENGTH} characters."
            )
        return text

    @staticmethod
    def _validate_filters(filters: list[str]) -> list[str]:
        if not isinstance(filters, list):
            raise ValueError("entity_type_filters must be a list.")
        if len(filters) > MAX_ENTITY_TYPE_FILTERS:
            raise ValueError(
                f"entity_type_filters cannot exceed {MAX_ENTITY_TYPE_FILTERS}."
            )
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in filters:
            text = str(item).strip()
            if not text:
                raise ValueError("entity_type_filters entries must be non-empty.")
            if len(text) > 128:
                raise ValueError("entity_type_filters entries are too long.")
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
        return cleaned

    @staticmethod
    def _validate_confidence(value: float) -> float:
        conf = float(value)
        if not 0.0 <= conf <= 1.0:
            raise ValueError("min_confidence must be between 0.0 and 1.0.")
        return conf

    @staticmethod
    def _validate_strategy(value: str) -> str:
        strategy = str(value).strip().lower()
        if strategy not in SUPPORTED_POSITION_STRATEGIES:
            raise ValueError(
                "position_strategy must be one of: "
                + ", ".join(sorted(SUPPORTED_POSITION_STRATEGIES))
            )
        return strategy

    @staticmethod
    def _validate_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a JSON object.")
        try:
            encoded = json.dumps(metadata, default=str)
        except (TypeError, ValueError) as error:
            raise ValueError("metadata must be JSON-serialisable.") from error
        if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
            raise ValueError(
                f"metadata cannot exceed {MAX_METADATA_BYTES} bytes."
            )
        return dict(metadata)
