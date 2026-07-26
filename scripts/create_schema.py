from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from storage import Database, load_database_settings
from storage.schema import apply_initial_schema


def main() -> None:
    database = Database(load_database_settings())
    if not database.ping():
        raise SystemExit("Database ping failed.")
    apply_initial_schema(database)
    print("Jarvis vision schema created successfully.")


if __name__ == "__main__":
    main()
