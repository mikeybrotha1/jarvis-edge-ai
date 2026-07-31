"""Alembic environment for Jarvis entity-memory migrations.

The vision tables in ``migrations/001_initial_schema.sql`` remain managed by
the existing SQL bootstrap path. Alembic owns the additive entity-memory
tables (``entities``, ``entity_observations``, ``entity_snapshots``).
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from storage.entity_orm import Base
from storage.sqlalchemy_db import _normalise_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    if "url" in x_args and x_args["url"].strip():
        return _normalise_database_url(x_args["url"])

    env_url = os.environ.get("JARVIS_DATABASE_URL", "").strip()
    if env_url:
        return _normalise_database_url(env_url)

    configured = config.get_main_option("sqlalchemy.url", "").strip()
    if configured and not configured.startswith("driver://"):
        return _normalise_database_url(configured)

    raise RuntimeError(
        "No database URL for Alembic. Set JARVIS_DATABASE_URL or pass "
        "-x url=postgresql://... / -x url=sqlite:///..."
    )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""

    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
