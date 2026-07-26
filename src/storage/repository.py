from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from .database import Database
from .models import IdentityEventRecord, IdentitySessionRecord, VisionRunRecord


class VisionRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create_run(self, record: VisionRunRecord) -> None:
        with self._database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO vision_runs (
                        run_id, hostname, camera_source, started_at, metadata
                    )
                    VALUES (
                        %(run_id)s, %(hostname)s, %(camera_source)s,
                        COALESCE(%(started_at)s, NOW()), %(metadata)s::jsonb
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
                    raise LookupError(f"Vision run not found: {run_id}")

    def append_identity_event(self, record: IdentityEventRecord) -> int:
        with self._database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO identity_events (
                        run_id, identity, track_id, label, event_type,
                        observed_at, confidence, frames_seen, bounding_box, payload
                    )
                    VALUES (
                        %(run_id)s, %(identity)s, %(track_id)s, %(label)s,
                        %(event_type)s, %(observed_at)s, %(confidence)s,
                        %(frames_seen)s, %(bounding_box)s::jsonb, %(payload)s::jsonb
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
                        "bounding_box": json.dumps(record.bounding_box),
                        "payload": json.dumps(record.payload),
                    },
                )
                row = cursor.fetchone()

        if row is None:
            raise RuntimeError("PostgreSQL did not return an event_id.")
        return int(row["event_id"])

    def table_counts(self) -> dict[str, int]:
        tables = ("vision_runs", "identity_sessions", "identity_events", "frame_metrics")
        counts: dict[str, int] = {}
        with self._database.connection() as connection:
            with connection.cursor() as cursor:
                for table in tables:
                    cursor.execute(f"SELECT COUNT(*) AS count FROM {table}")
                    counts[table] = int(cursor.fetchone()["count"])
        return counts
