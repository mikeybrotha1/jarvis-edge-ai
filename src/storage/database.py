from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from .config import DatabaseSettings


class Database:
    def __init__(self, settings: DatabaseSettings) -> None:
        self._settings = settings

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        connection = psycopg.connect(
            self._settings.database_url,
            row_factory=dict_row,
        )
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ping(self) -> bool:
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok")
                row = cursor.fetchone()
        return bool(row and row["ok"] == 1)
