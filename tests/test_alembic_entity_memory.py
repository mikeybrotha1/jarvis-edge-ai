"""Alembic upgrade/downgrade checks for entity-memory tables."""

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
    # Ensure src/ is importable for alembic/env.py storage imports.
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


def test_alembic_upgrade_and_downgrade_on_sqlite() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "entity_memory.sqlite3"
        url = f"sqlite+pysqlite:///{db_path}"

        # Simulate current schema already present (vision tables).
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

        upgrade = _run_alembic(["upgrade", "head"], url=url)
        assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "vision_runs" in tables
        assert "entities" in tables
        assert "entity_observations" in tables
        assert "entity_snapshots" in tables
        assert "alembic_version" in tables

        # Vision table must remain untouched by entity migration.
        with engine.connect() as connection:
            result = connection.execute(
                text("SELECT COUNT(*) FROM vision_runs")
            )
            assert result.scalar() == 0

        downgrade = _run_alembic(["downgrade", "base"], url=url)
        assert downgrade.returncode == 0, (
            downgrade.stdout + downgrade.stderr
        )

        inspector = inspect(engine)
        tables_after = set(inspector.get_table_names())
        assert "entities" not in tables_after
        assert "entity_observations" not in tables_after
        assert "entity_snapshots" not in tables_after
        assert "vision_runs" in tables_after


if __name__ == "__main__":
    test_alembic_upgrade_and_downgrade_on_sqlite()
    print("Alembic entity memory migration tests passed.")
