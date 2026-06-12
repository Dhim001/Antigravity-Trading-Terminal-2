"""Database connection factory — SQLite (default) or PostgreSQL via DATABASE_URL."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from app.config import DB_PATH

DB_DRIVER = "sqlite"
_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if _DATABASE_URL.startswith(("postgres://", "postgresql://")):
    DB_DRIVER = "postgres"


def _adapt_sql(sql: str) -> str:
    if DB_DRIVER == "postgres":
        return sql.replace("?", "%s")
    return sql


class _CursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql: str, params: tuple | list | None = None):
        if params is None:
            return self._cursor.execute(_adapt_sql(sql))
        return self._cursor.execute(_adapt_sql(sql), params)

    def executemany(self, sql: str, params_seq):
        return self._cursor.executemany(_adapt_sql(sql), params_seq)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _ConnectionWrapper:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return _CursorWrapper(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def get_connection():
    if DB_DRIVER == "postgres":
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(_DATABASE_URL, row_factory=dict_row)
        return _ConnectionWrapper(conn)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return _ConnectionWrapper(conn)


def is_postgres() -> bool:
    return DB_DRIVER == "postgres"
