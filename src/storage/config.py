from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    database_url: str


def load_database_settings() -> DatabaseSettings:
    database_url = os.environ.get("JARVIS_DATABASE_URL", "").strip()

    if not database_url:
        raise RuntimeError(
            "JARVIS_DATABASE_URL is not set. "
            "Run: set -a && source .env.jarvis && set +a"
        )

    if not database_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("JARVIS_DATABASE_URL must be a PostgreSQL URL.")

    return DatabaseSettings(database_url=database_url)
