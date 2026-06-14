"""Database connection factory — SQLite (default) or PostgreSQL via DATABASE_URL."""

from __future__ import annotations

import os
import queue
import sqlite3
import threading
import time
from typing import Any

from app.config import DB_PATH

DB_DRIVER = "sqlite"
_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
_POOL_MAX = int(os.environ.get("DB_POOL_SIZE", "10"))

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
        adapted = _adapt_sql(sql)
        if DB_DRIVER != "sqlite":
            if params is None:
                return self._cursor.execute(adapted)
            return self._cursor.execute(adapted, params)

        for attempt in range(8):
            try:
                if params is None:
                    return self._cursor.execute(adapted)
                return self._cursor.execute(adapted, params)
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt >= 7:
                    raise
                time.sleep(0.025 * (attempt + 1))
        return None

    def executemany(self, sql: str, params_seq):
        return self._cursor.executemany(_adapt_sql(sql), params_seq)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _ConnectionWrapper:
    def __init__(self, conn, *, _release=None):
        self._conn = conn
        self._release = _release
        self._closed = False

    def cursor(self):
        return _CursorWrapper(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        if hasattr(self._conn, "rollback"):
            self._conn.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._release:
            self._release(self._conn)
        else:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class _SqlitePool:
    def __init__(self, path: str, max_size: int = 5):
        self._path = path
        self._max_size = max(1, max_size)
        self._queue: queue.Queue = queue.Queue(maxsize=self._max_size)
        self._lock = threading.Lock()
        self._created = 0

    def _new_conn(self):
        conn = sqlite3.connect(self._path, check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA cache_size = -64000")
        return conn

    def _release(self, conn):
        try:
            self._queue.put_nowait(conn)
        except queue.Full:
            conn.close()

    def getconn(self) -> _ConnectionWrapper:
        try:
            raw = self._queue.get_nowait()
        except queue.Empty:
            with self._lock:
                if self._created < self._max_size:
                    self._created += 1
                    raw = self._new_conn()
                else:
                    raw = self._queue.get()
        return _ConnectionWrapper(raw, _release=self._release)


class _PostgresPool:
    def __init__(self, url: str, max_size: int = 10):
        from psycopg.rows import dict_row

        try:
            from psycopg_pool import ConnectionPool

            self._pool = ConnectionPool(
                url,
                min_size=1,
                max_size=max(1, max_size),
                kwargs={"row_factory": dict_row},
            )
            self._use_pool = True
        except ImportError:
            self._pool = None
            self._use_pool = False
            self._url = url
            self._dict_row = dict_row

    def getconn(self) -> _ConnectionWrapper:
        if self._use_pool and self._pool is not None:
            raw = self._pool.getconn()
            return _ConnectionWrapper(raw, _release=lambda c: self._pool.putconn(c))

        import psycopg

        raw = psycopg.connect(self._url, row_factory=self._dict_row)
        return _ConnectionWrapper(raw)


_pool: _SqlitePool | _PostgresPool | None = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            if DB_DRIVER == "postgres":
                _pool = _PostgresPool(_DATABASE_URL, max_size=_POOL_MAX)
            else:
                _pool = _SqlitePool(DB_PATH, max_size=min(_POOL_MAX, 5))
        return _pool


def get_connection() -> _ConnectionWrapper:
    return _get_pool().getconn()


def is_postgres() -> bool:
    return DB_DRIVER == "postgres"
