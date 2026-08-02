#!/usr/bin/env python3
"""Create a temporary PostgreSQL database, run retention e2e, drop it.

Never targets the operator production database (jarvis_vision).

Usage::

    # Admin DSN must be able to CREATE/DROP DATABASE (superuser or CREATEDB).
    export JARVIS_PG_ADMIN_URL=postgresql://admin:...@127.0.0.1:5432/postgres
    # App role used as owner and for migrations/tests.
    export JARVIS_PG_APP_URL=postgresql://jarvis_app:...@127.0.0.1:5432/postgres

    python scripts/retention_pg_e2e_demo.py

Optional::

    JARVIS_RETENTION_E2E_DB_NAME=jarvis_retention_e2e_tmp_XXXX
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {"jarvis_vision", "teslamate", "postgres", "template0", "template1"}


def _db_name(url: str) -> str:
    path = urlparse(url).path or ""
    return path.lstrip("/").split("?")[0]


def _with_db(url: str, name: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=f"/{name}"))


def _connect_admin(url: str):
    from sqlalchemy import create_engine, text
    from storage.sqlalchemy_db import _normalise_database_url

    engine = create_engine(
        _normalise_database_url(url),
        isolation_level="AUTOCOMMIT",
        future=True,
    )
    return engine, text


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))

    admin_url = os.environ.get("JARVIS_PG_ADMIN_URL", "").strip()
    app_url = os.environ.get("JARVIS_PG_APP_URL", "").strip()
    if not admin_url or not app_url:
        print(
            "Set JARVIS_PG_ADMIN_URL and JARVIS_PG_APP_URL "
            "(admin creates/drops DB; app owns and runs migrations).",
            file=sys.stderr,
        )
        return 2

    app_user = urlparse(app_url).username or "jarvis_app"
    db_name = os.environ.get(
        "JARVIS_RETENTION_E2E_DB_NAME",
        f"jarvis_retention_e2e_tmp_{uuid.uuid4().hex[:8]}",
    )
    if db_name in FORBIDDEN or not re.fullmatch(r"[a-z][a-z0-9_]{2,62}", db_name):
        print(f"Refusing unsafe database name: {db_name!r}", file=sys.stderr)
        return 2

    e2e_app_url = _with_db(app_url, db_name)
    print(f"Creating temporary database {db_name!r} owned by {app_user!r} ...")
    engine, text = _connect_admin(admin_url)
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            conn.execute(
                text(f'CREATE DATABASE "{db_name}" OWNER "{app_user}"')
            )
            # On PostgreSQL 15+, schema public privileges may need grant.
            try:
                conn.execute(
                    text(
                        f'GRANT ALL PRIVILEGES ON DATABASE "{db_name}" '
                        f'TO "{app_user}"'
                    )
                )
            except Exception as exc:  # noqa: BLE001
                print(f"note: grant privileges: {exc}")
    finally:
        engine.dispose()

    env = os.environ.copy()
    env["JARVIS_RETENTION_PG_E2E_URL"] = e2e_app_url
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    # Never leak full DSN in success summary — only db name.
    print(f"Running pytest retention PG e2e against {db_name!r} ...")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_retention_pg_e2e.py",
            "-v",
            "--tb=short",
        ],
        cwd=str(ROOT),
        env=env,
        check=False,
    )
    rc = result.returncode

    print(f"Dropping temporary database {db_name!r} ...")
    engine, text = _connect_admin(admin_url)
    try:
        with engine.connect() as conn:
            # Terminate leftover connections then drop.
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": db_name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    finally:
        engine.dispose()

    if rc == 0:
        print("retention_pg_e2e_demo: PASS")
    else:
        print(f"retention_pg_e2e_demo: FAIL (pytest exit {rc})", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
