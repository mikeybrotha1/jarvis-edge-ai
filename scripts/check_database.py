from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from storage import Database, VisionRepository, load_database_settings


def main() -> None:
    database = Database(load_database_settings())
    if not database.ping():
        raise SystemExit("Database ping failed.")

    print("Database connection: OK")
    print("Table counts:")
    for table, count in VisionRepository(database).table_counts().items():
        print(f"  {table}: {count}")


if __name__ == "__main__":
    main()
