"""Database connection factory — SQLite (default) or PostgreSQL via DATABASE_URL."""

from __future__ import annotations

import logging
import os
import queue
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from app.config import DB_PATH

logger = logging.getLogger(__name__)

DB_DRIVER = "sqlite"
_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
_POOL_MAX = int(os.environ.get("DB_POOL_SIZE", "10"))
_POOL_MIN = int(os.environ.get("DB_POOL_MIN", "1"))
_POOL_TIMEOUT = float(os.environ.get("DB_POOL_TIMEOUT", "30"))
_SQLITE_POOL_MAX = int(os.environ.get("DB_SQLITE_POOL_SIZE", str(min(_POOL_MAX, 5))))
_DB_REQUIRE_POOL = os.environ.get("DB_REQUIRE_POOL", "").lower() in ("1", "true", "yes")
_LOCK_RETRIES = int(os.environ.get("DB_LOCK_RETRIES", "8"))
_LOCK_RETRY_BASE_SEC = float(os.environ.get("DB_LOCK_RETRY_BASE_SEC", "0.025"))

if _DATABASE_URL.startswith(("postgres://", "postgresql://")):
    DB_DRIVER = "postgres"

# Observability counters (best-effort, thread-safe).
_stats_lock = threading.Lock()
_lock_retry_total = 0


def _inc_lock_retries(n: int = 1) -> None:
    global _lock_retry_total
    with _stats_lock:
        _lock_retry_total += n


def lock_retry_total() -> int:
    with _stats_lock:
        return _lock_retry_total


def configure_sqlite_connection(conn: sqlite3.Connection) -> None:
    """Apply standard SQLite PRAGMAs for WAL concurrency (single source of truth)."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -64000")


def _adapt_sql(sql: str) -> str:
    if DB_DRIVER == "postgres":
        return sql.replace("?", "%s")
    return sql


def _is_sqlite_locked(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


def _is_transient_postgres_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in ("connection", "timeout", "closed unexpectedly", "terminating", "broken pipe")
    )


class _CursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql: str, params: tuple | list | None = None):
        adapted = _adapt_sql(sql)
        max_attempts = _LOCK_RETRIES

        for attempt in range(max_attempts):
            try:
                if params is None:
                    return self._cursor.execute(adapted)
                return self._cursor.execute(adapted, params)
            except sqlite3.OperationalError as exc:
                if not _is_sqlite_locked(exc) or attempt >= max_attempts - 1:
                    raise
                _inc_lock_retries()
                # PRAGMA busy_timeout already blocks inside SQLite — avoid stacking
                # time.sleep() on the asyncio event loop during lock storms.
                continue
            except Exception as exc:
                if DB_DRIVER != "postgres" or not _is_transient_postgres_error(exc):
                    raise
                if attempt >= max_attempts - 1:
                    raise
                _inc_lock_retries()
                time.sleep(_LOCK_RETRY_BASE_SEC * (2 ** attempt))
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
        configure_sqlite_connection(conn)
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
                    try:
                        raw = self._queue.get(timeout=_POOL_TIMEOUT)
                    except queue.Empty as exc:
                        raise RuntimeError(
                            f"SQLite connection pool exhausted (max={self._max_size})"
                        ) from exc
        return _ConnectionWrapper(raw, _release=self._release)

    def stats(self) -> dict[str, Any]:
        return {
            "driver": "sqlite",
            "max_size": self._max_size,
            "created": self._created,
            "idle": self._queue.qsize(),
        }


class _PostgresPool:
    def __init__(
        self,
        url: str,
        *,
        max_size: int = 10,
        min_size: int = 1,
        timeout: float = 30.0,
    ):
        from psycopg.rows import dict_row

        self._pool = None
        self._use_pool = False
        self._url = url
        self._dict_row = dict_row
        self._max_size = max(1, max_size)
        self._min_size = max(1, min_size)

        try:
            from psycopg_pool import ConnectionPool

            self._pool = ConnectionPool(
                url,
                min_size=self._min_size,
                max_size=self._max_size,
                timeout=timeout,
                kwargs={"row_factory": dict_row},
            )
            self._use_pool = True
        except ImportError:
            if _DB_REQUIRE_POOL:
                raise RuntimeError(
                    "DB_REQUIRE_POOL=1 but psycopg_pool is not installed "
                    "(install psycopg[binary,pool])"
                ) from None
            logger.warning("psycopg_pool unavailable — using non-pooled Postgres connections")

    def getconn(self) -> _ConnectionWrapper:
        if self._use_pool and self._pool is not None:
            raw = self._pool.getconn()
            return _ConnectionWrapper(raw, _release=lambda c: self._pool.putconn(c))

        import psycopg

        raw = psycopg.connect(self._url, row_factory=self._dict_row)
        return _ConnectionWrapper(raw)

    def stats(self) -> dict[str, Any]:
        base = {
            "driver": "postgres",
            "pooled": self._use_pool,
            "max_size": self._max_size,
            "min_size": self._min_size,
        }
        if self._use_pool and self._pool is not None and hasattr(self._pool, "get_stats"):
            try:
                base["pool_stats"] = self._pool.get_stats()
            except Exception:
                pass
        return base


_pool: _SqlitePool | _PostgresPool | None = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            if DB_DRIVER == "postgres":
                _pool = _PostgresPool(
                    _DATABASE_URL,
                    max_size=_POOL_MAX,
                    min_size=_POOL_MIN,
                    timeout=_POOL_TIMEOUT,
                )
            else:
                _pool = _SqlitePool(DB_PATH, max_size=_SQLITE_POOL_MAX)
        return _pool


def get_connection() -> _ConnectionWrapper:
    return _get_pool().getconn()


@contextmanager
def db_session(*, commit: bool = True) -> Iterator[_ConnectionWrapper]:
    """Acquire a pooled connection; commit/rollback and return to pool on exit."""
    conn = get_connection()
    try:
        yield conn
        if commit:
            conn.commit()
        else:
            # End read-only transaction so pooled SQLite connections are clean.
            conn.rollback()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def pool_stats() -> dict[str, Any]:
    pool = _pool
    if pool is None:
        return {"initialized": False, "driver": DB_DRIVER}
    stats = pool.stats()
    stats["initialized"] = True
    stats["lock_retries"] = lock_retry_total()
    return stats


def check_db_health() -> dict[str, Any]:
    """Lightweight connectivity probe for /health and startup."""
    start = time.perf_counter()
    conn = get_connection()
    journal_mode = None
    ok = False
    try:
        cur = conn.cursor()
        if DB_DRIVER == "sqlite":
            cur.execute("PRAGMA quick_check(1)")
            row = cur.fetchone()
            ok = bool(row and (row[0] if not isinstance(row, dict) else list(row.values())[0]) == "ok")
            cur.execute("PRAGMA journal_mode")
            jrow = cur.fetchone()
            journal_mode = jrow[0] if jrow and not isinstance(jrow, dict) else (
                list(jrow.values())[0] if isinstance(jrow, dict) else None
            )
        else:
            cur.execute("SELECT 1")
            cur.fetchone()
            ok = True
    finally:
        conn.close()

    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    return {
        "ok": ok,
        "driver": DB_DRIVER,
        "latency_ms": latency_ms,
        "journal_mode": journal_mode,
        "pool": pool_stats(),
    }


def warm_pool(
    *,
    max_attempts: int | None = None,
    base_delay_sec: float | None = None,
) -> dict[str, Any]:
    """Initialize the pool and verify DB connectivity (startup retry with backoff)."""
    attempts = max_attempts if max_attempts is not None else int(os.environ.get("DB_STARTUP_RETRIES", "5"))
    base_delay = base_delay_sec if base_delay_sec is not None else float(
        os.environ.get("DB_STARTUP_RETRY_BASE_SEC", "0.5")
    )
    last_exc: Exception | None = None

    for attempt in range(max(1, attempts)):
        try:
            _get_pool()
            health = check_db_health()
            if not health.get("ok"):
                raise RuntimeError("database health check failed")
            if attempt:
                logger.info("Database available after %s attempt(s)", attempt + 1)
            return health
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "Database unavailable (attempt %s/%s): %s — retry in %.1fs",
                    attempt + 1,
                    attempts,
                    exc,
                    delay,
                )
                time.sleep(delay)

    raise RuntimeError(f"Database unavailable after {attempts} attempts: {last_exc}") from last_exc


def is_postgres() -> bool:
    return DB_DRIVER == "postgres"
