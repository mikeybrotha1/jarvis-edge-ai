"""Alembic upgrade/downgrade checks for spatial tables (v0.6.0)."""

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


def test_spatial_migration_upgrade_and_downgrade() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp).resolve() / "spatial.sqlite3"
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

        engine = create_engine(url, future=True)
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "zones" in tables
        assert "entity_zone_sessions" in tables

        zone_indexes = {
            index["name"] for index in inspector.get_indexes("zones")
        }
        assert "ix_zones_camera_id" in zone_indexes
        assert "ix_zones_camera_id_enabled" in zone_indexes

        session_indexes = {
            index["name"]
            for index in inspector.get_indexes("entity_zone_sessions")
        }
        assert "ix_ezs_zone_id_status" in session_indexes
        assert "uq_ezs_open_zone_entity" in session_indexes

        downgrade = _run_alembic(["downgrade", "20260728_0002"], url=url)
        assert downgrade.returncode == 0, (
            downgrade.stdout + downgrade.stderr
        )

        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "zones" not in tables
        assert "entity_zone_sessions" not in tables
        assert "entities" in tables
