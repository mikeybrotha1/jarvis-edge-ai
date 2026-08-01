"""Alembic upgrade/downgrade checks for timeline indexes (v0.4.2)."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_alembic(args: list[str], *, url: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["JARVIS_DATABASE_URL"] = url
    pythonpath = env.get("PYTHONPATH", "")
    src = str(PROJECT_ROOT / "src")
    env["PYTHONPATH"] = f"{src}:{pythonpath}" if pythonpath else src
    return subprocess.run(
        [
            str(PROJECT_ROOT / ".venv" / "bin" / "alembic"),
            "-x",
            f"url={url}",
            *args,
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_timeline_index_migration_upgrade_and_downgrade() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp).resolve() / "timeline.sqlite3"
        url = f"sqlite+pysqlite:///{db_path}"

        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE vision_runs (
                        run_id TEXT PRIMARY KEY,
                        hostname TEXT NOT NULL
                    )
                    """
                )
            )
        engine.dispose()

        upgrade = _run_alembic(["upgrade", "head"], url=url)
        assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

        # Re-open after alembic subprocess finished writing the file.
        engine = create_engine(url, future=True)
        inspector = inspect(engine)
        entity_indexes = {
            index["name"] for index in inspector.get_indexes("entities")
        }
        observation_indexes = {
            index["name"]
            for index in inspector.get_indexes("entity_observations")
        }

        assert "ix_entities_first_seen_id" in entity_indexes
        assert "ix_entities_status_last_seen_id" in entity_indexes
        assert "ix_entities_camera_first_seen_id" in entity_indexes
        assert "ix_entities_camera_last_seen_id" in entity_indexes
        assert "ix_entity_observations_observed_at_id" in observation_indexes
        assert (
            "ix_entity_observations_entity_observed_id"
            in observation_indexes
        )
        assert (
            "ix_entity_observations_camera_observed_id"
            in observation_indexes
        )

        # Downgrade one revision (timeline indexes only).
        downgrade = _run_alembic(["downgrade", "20260727_0001"], url=url)
        assert downgrade.returncode == 0, (
            downgrade.stdout + downgrade.stderr
        )

        inspector = inspect(engine)
        entity_indexes = {
            index["name"] for index in inspector.get_indexes("entities")
        }
        observation_indexes = {
            index["name"]
            for index in inspector.get_indexes("entity_observations")
        }
        assert "ix_entities_first_seen_id" not in entity_indexes
        assert "ix_entity_observations_observed_at_id" not in observation_indexes
        # Base entity tables remain.
        assert "entities" in inspector.get_table_names()
        assert "entity_observations" in inspector.get_table_names()
