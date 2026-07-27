from __future__ import annotations

from pathlib import Path

from .database import Database


def default_migration_path() -> Path:
    return Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql"


def apply_initial_schema(database: Database, migration_path: Path | None = None) -> None:
    path = migration_path or default_migration_path()
    if not path.exists():
        raise FileNotFoundError(f"Migration file not found: {path}")

    with database.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(path.read_text(encoding="utf-8"))
