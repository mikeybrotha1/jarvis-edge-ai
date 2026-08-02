"""SQLAlchemy engine and session factory for entity memory.

Purpose
-------
Provide a small, injectable session boundary for entity repositories without
replacing the existing psycopg ``Database`` / ``VisionRepository`` stack.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .entity_orm import Base

# Register spatial, alert, and notification tables on the shared declarative
# Base so create_entity_schema() creates them for tests/bootstrap.
from . import zone_orm as _zone_orm  # noqa: F401
from . import alert_orm as _alert_orm  # noqa: F401
from . import notification_orm as _notification_orm  # noqa: F401


def create_entity_engine(
    database_url: str,
    *,
    echo: bool = False,
) -> Engine:
    """Create a SQLAlchemy engine for entity-memory tables.

    PostgreSQL URLs are accepted as ``postgresql://…``. SQLAlchemy prefers
    the ``postgresql+psycopg://`` driver for psycopg3; plain ``postgresql://``
    is rewritten when the psycopg dialect is available.

    In-memory SQLite uses :class:`StaticPool` so schema is shared across
    threads and connections (required by the background worker).
    """

    url = _normalise_database_url(database_url)
    connect_args: dict[str, object] = {}
    engine_kwargs: dict[str, object] = {
        "echo": echo,
        "future": True,
    }

    if url.startswith("sqlite"):
        # Required for SQLite + multi-thread test / worker usage.
        connect_args["check_same_thread"] = False
        engine_kwargs["connect_args"] = connect_args

        if ":memory:" in url:
            # Keep a single shared connection for in-memory databases.
            engine_kwargs["poolclass"] = StaticPool
            url = "sqlite+pysqlite://"
    else:
        if connect_args:
            engine_kwargs["connect_args"] = connect_args

    engine = create_engine(url, **engine_kwargs)

    if url.startswith("sqlite"):
        _enable_sqlite_foreign_keys(engine)

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to ``engine``."""

    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


def create_entity_schema(engine: Engine) -> None:
    """Create entity-memory tables (useful for tests and bootstrap)."""

    Base.metadata.create_all(engine)


@contextmanager
def session_scope(
    session_factory: sessionmaker[Session],
) -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""

    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _normalise_database_url(database_url: str) -> str:
    url = database_url.strip()

    if url.startswith("postgresql+psycopg://"):
        return url

    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]

    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]

    return url


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
