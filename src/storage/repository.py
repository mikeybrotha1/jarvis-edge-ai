"""PostgreSQL repository for Jarvis persistent vision data.

Purpose
-------
Keep every SQL operation behind one repository boundary.

Responsibilities
----------------
- Create and finish vision runs.
- Append immutable identity lifecycle events.
- Create or update identity-session summaries.
- Close identity sessions.
- Persist frame-level metrics.

Non-responsibilities
--------------------
- Object detection.
- Identity assignment.
- Event subscription.
- Application lifecycle orchestration.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from .database import Database
from .models import (
    FrameMetricRecord,
    IdentityEventRecord,
    IdentitySessionRecord,
    VisionRunRecord,
)


class VisionRepository:
    """Read and write structured vision data in PostgreSQL."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create_run(self, record: VisionRunRecord) -> None:
        """Create one running vision-run record."""

        with self._database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO vision_runs (
                        run_id,
                        hostname,
                        camera_source,
                        started_at,
                        metadata
                    )
                    VALUES (
                        %(run_id)s,
                        %(hostname)s,
                        %(camera_source)s,
                        COALESCE(%(started_at)s, NOW()),
                        %(metadata)s::jsonb
                    )
                    """,
                    {
                        "run_id": record.run_id,
                        "hostname": record.hostname,
                        "camera_source": record.camera_source,
                        "started_at": record.started_at,
                        "metadata": json.dumps(record.metadata),
                    },
                )

    def finish_run(
        self,
        run_id: UUID,
        *,
        frames_processed: int,
        status: str = "completed",
        stopped_at: datetime | None = None,
    ) -> None:
        """Mark one vision run as completed or failed."""

        if frames_processed < 0:
            raise ValueError("frames_processed cannot be negative.")

        if status not in {"completed", "failed"}:
            raise ValueError(
                "status must be either 'completed' or 'failed'."
            )

        with self._database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE vision_runs
                    SET stopped_at = COALESCE(%(stopped_at)s, NOW()),
                        frames_processed = %(frames_processed)s,
                        status = %(status)s
                    WHERE run_id = %(run_id)s
                    """,
                    {
                        "run_id": run_id,
                        "stopped_at": stopped_at,
                        "frames_processed": frames_processed,
                        "status": status,
                    },
                )

                if cursor.rowcount != 1:
                    raise LookupError(
                        f"Vision run not found: {run_id}"
                    )

    def append_identity_event(
        self,
        record: IdentityEventRecord,
    ) -> int:
        """Append one immutable identity lifecycle event."""

        allowed_event_types = {
            "object_entered",
            "object_updated",
            "object_exited",
        }

        if record.event_type not in allowed_event_types:
            raise ValueError(
                "event_type must be object_entered, "
                "object_updated, or object_exited."
            )

        if record.frames_seen < 0:
            raise ValueError("frames_seen cannot be negative.")

        self._validate_confidence(
            record.confidence,
            field_name="confidence",
        )

        with self._database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO identity_events (
                        run_id,
                        identity,
                        track_id,
                        label,
                        event_type,
                        observed_at,
                        confidence,
                        frames_seen,
                        bounding_box,
                        payload
                    )
                    VALUES (
                        %(run_id)s,
                        %(identity)s,
                        %(track_id)s,
                        %(label)s,
                        %(event_type)s,
                        %(observed_at)s,
                        %(confidence)s,
                        %(frames_seen)s,
                        %(bounding_box)s::jsonb,
                        %(payload)s::jsonb
                    )
                    RETURNING event_id
                    """,
                    {
                        "run_id": record.run_id,
                        "identity": record.identity,
                        "track_id": record.track_id,
                        "label": record.label,
                        "event_type": record.event_type,
                        "observed_at": record.observed_at,
                        "confidence": record.confidence,
                        "frames_seen": record.frames_seen,
                        "bounding_box": json.dumps(
                            record.bounding_box
                        ),
                        "payload": json.dumps(record.payload),
                    },
                )

                row = cursor.fetchone()

        if row is None:
            raise RuntimeError(
                "PostgreSQL did not return an event_id."
            )

        return int(row["event_id"])

    def upsert_identity_session(
        self,
        record: IdentitySessionRecord,
    ) -> None:
        """Create or replace the accumulated state of an identity.

        The service layer calculates cumulative values. The repository treats
        the supplied record as the authoritative current state.
        """

        self._validate_identity_session(record)

        with self._database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO identity_sessions (
                        run_id,
                        identity,
                        track_id,
                        label,
                        first_seen,
                        last_seen,
                        appearance_count,
                        total_frames_seen,
                        highest_confidence,
                        last_confidence,
                        active,
                        last_bounding_box
                    )
                    VALUES (
                        %(run_id)s,
                        %(identity)s,
                        %(track_id)s,
                        %(label)s,
                        %(first_seen)s,
                        %(last_seen)s,
                        %(appearance_count)s,
                        %(total_frames_seen)s,
                        %(highest_confidence)s,
                        %(last_confidence)s,
                        %(active)s,
                        %(last_bounding_box)s::jsonb
                    )
                    ON CONFLICT (run_id, identity)
                    DO UPDATE SET
                        track_id = EXCLUDED.track_id,
                        label = EXCLUDED.label,
                        first_seen = LEAST(
                            identity_sessions.first_seen,
                            EXCLUDED.first_seen
                        ),
                        last_seen = GREATEST(
                            identity_sessions.last_seen,
                            EXCLUDED.last_seen
                        ),
                        appearance_count = EXCLUDED.appearance_count,
                        total_frames_seen = EXCLUDED.total_frames_seen,
                        highest_confidence = GREATEST(
                            identity_sessions.highest_confidence,
                            EXCLUDED.highest_confidence
                        ),
                        last_confidence = EXCLUDED.last_confidence,
                        active = EXCLUDED.active,
                        last_bounding_box =
                            EXCLUDED.last_bounding_box
                    """,
                    {
                        "run_id": record.run_id,
                        "identity": record.identity,
                        "track_id": record.track_id,
                        "label": record.label,
                        "first_seen": record.first_seen,
                        "last_seen": record.last_seen,
                        "appearance_count": record.appearance_count,
                        "total_frames_seen": record.total_frames_seen,
                        "highest_confidence": (
                            record.highest_confidence
                        ),
                        "last_confidence": record.last_confidence,
                        "active": record.active,
                        "last_bounding_box": json.dumps(
                            record.last_bounding_box
                        ),
                    },
                )

    def finish_identity_session(
        self,
        run_id: UUID,
        identity: str,
        *,
        last_seen: datetime,
        total_frames_seen: int,
        highest_confidence: float,
        last_confidence: float,
        last_bounding_box: dict[str, object] | None = None,
    ) -> None:
        """Mark one identity session inactive."""

        if not identity.strip():
            raise ValueError("identity cannot be empty.")

        if total_frames_seen < 0:
            raise ValueError(
                "total_frames_seen cannot be negative."
            )

        self._validate_confidence(
            highest_confidence,
            field_name="highest_confidence",
        )
        self._validate_confidence(
            last_confidence,
            field_name="last_confidence",
        )

        with self._database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE identity_sessions
                    SET last_seen = GREATEST(
                            last_seen,
                            %(last_seen)s
                        ),
                        total_frames_seen = GREATEST(
                            total_frames_seen,
                            %(total_frames_seen)s
                        ),
                        highest_confidence = GREATEST(
                            highest_confidence,
                            %(highest_confidence)s
                        ),
                        last_confidence = %(last_confidence)s,
                        active = FALSE,
                        last_bounding_box =
                            %(last_bounding_box)s::jsonb
                    WHERE run_id = %(run_id)s
                      AND identity = %(identity)s
                    """,
                    {
                        "run_id": run_id,
                        "identity": identity,
                        "last_seen": last_seen,
                        "total_frames_seen": total_frames_seen,
                        "highest_confidence": highest_confidence,
                        "last_confidence": last_confidence,
                        "last_bounding_box": json.dumps(
                            last_bounding_box
                        ),
                    },
                )

                if cursor.rowcount != 1:
                    raise LookupError(
                        "Identity session not found: "
                        f"{run_id}/{identity}"
                    )

    def record_frame_metrics(
        self,
        record: FrameMetricRecord,
    ) -> None:
        """Create or update metrics for one processed frame."""

        if record.frame_id < 0:
            raise ValueError("frame_id cannot be negative.")

        if record.detection_count < 0:
            raise ValueError(
                "detection_count cannot be negative."
            )

        if record.fps is not None and record.fps < 0:
            raise ValueError("fps cannot be negative.")

        if (
            record.processing_ms is not None
            and record.processing_ms < 0
        ):
            raise ValueError(
                "processing_ms cannot be negative."
            )

        with self._database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO frame_metrics (
                        run_id,
                        frame_id,
                        observed_at,
                        fps,
                        detection_count,
                        processing_ms
                    )
                    VALUES (
                        %(run_id)s,
                        %(frame_id)s,
                        %(observed_at)s,
                        %(fps)s,
                        %(detection_count)s,
                        %(processing_ms)s
                    )
                    ON CONFLICT (run_id, frame_id)
                    DO UPDATE SET
                        observed_at = EXCLUDED.observed_at,
                        fps = EXCLUDED.fps,
                        detection_count =
                            EXCLUDED.detection_count,
                        processing_ms = EXCLUDED.processing_ms
                    """,
                    {
                        "run_id": record.run_id,
                        "frame_id": record.frame_id,
                        "observed_at": record.observed_at,
                        "fps": record.fps,
                        "detection_count": (
                            record.detection_count
                        ),
                        "processing_ms": record.processing_ms,
                    },
                )

    def table_counts(self) -> dict[str, int]:
        """Return row counts for the persistent-vision tables."""

        tables = (
            "vision_runs",
            "identity_sessions",
            "identity_events",
            "frame_metrics",
        )
        counts: dict[str, int] = {}

        with self._database.connection() as connection:
            with connection.cursor() as cursor:
                for table in tables:
                    cursor.execute(
                        f"SELECT COUNT(*) AS count FROM {table}"
                    )
                    row = cursor.fetchone()

                    if row is None:
                        raise RuntimeError(
                            f"PostgreSQL returned no count for {table}."
                        )

                    counts[table] = int(row["count"])

        return counts

    @classmethod
    def _validate_identity_session(
        cls,
        record: IdentitySessionRecord,
    ) -> None:
        if not record.identity.strip():
            raise ValueError("identity cannot be empty.")

        if not record.label.strip():
            raise ValueError("label cannot be empty.")

        if record.appearance_count < 1:
            raise ValueError(
                "appearance_count must be at least 1."
            )

        if record.total_frames_seen < 0:
            raise ValueError(
                "total_frames_seen cannot be negative."
            )

        if record.last_seen < record.first_seen:
            raise ValueError(
                "last_seen cannot be earlier than first_seen."
            )

        cls._validate_confidence(
            record.highest_confidence,
            field_name="highest_confidence",
        )
        cls._validate_confidence(
            record.last_confidence,
            field_name="last_confidence",
        )

    @staticmethod
    def _validate_confidence(
        value: float,
        *,
        field_name: str,
    ) -> None:
        if not 0.0 <= value <= 1.0:
            raise ValueError(
                f"{field_name} must be between 0 and 1."
            )
