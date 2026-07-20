"""In-memory write buffer with periodic batch flush to DB."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import (
    ARCHIVE_BACKEND,
    ARCHIVE_BUFFER_MAX_ROWS,
    ARCHIVE_ENABLED,
    ARCHIVE_FLUSH_INTERVAL,
    ARCHIVE_PARQUET_ENABLED,
)
from app.db.connection import get_connection, is_postgres

logger = logging.getLogger(__name__)

_writer: "ArchiveWriter | None" = None


def align_bar_time(t: int) -> int:
    return (int(t) // 60) * 60


def _bar_row(symbol: str, bar: dict, source: str) -> dict[str, Any]:
    t = align_bar_time(bar["time"])
    return {
        "symbol": symbol,
        "time": t,
        "open": float(bar["open"]),
        "high": float(bar["high"]),
        "low": float(bar["low"]),
        "close": float(bar["close"]),
        "volume": float(bar.get("volume") or 0),
        "source": source,
    }


class ArchiveWriter:
    def __init__(self) -> None:
        self._buffer: dict[tuple[str, int], dict[str, Any]] = {}
        self._last_flush = 0.0
        self._total_flushed = 0
        self._dropped = 0

    @property
    def pending_count(self) -> int:
        return len(self._buffer)

    @property
    def total_flushed(self) -> int:
        return self._total_flushed

    @property
    def total_dropped(self) -> int:
        return self._dropped

    def _trim_buffer(self) -> None:
        """Drop oldest bars when over ARCHIVE_BUFFER_MAX_ROWS (leak-on-failure guard)."""
        max_rows = max(1, int(ARCHIVE_BUFFER_MAX_ROWS))
        excess = len(self._buffer) - max_rows
        if excess <= 0:
            return
        # Keys are (symbol, time) — sort by time then drop oldest.
        oldest = sorted(self._buffer.keys(), key=lambda k: (k[1], k[0]))[:excess]
        for key in oldest:
            del self._buffer[key]
        self._dropped += excess
        logger.warning(
            "Archive buffer over cap (%d) — dropped %d oldest row(s) (total dropped=%d)",
            max_rows,
            excess,
            self._dropped,
        )

    def record_bar(self, symbol: str, bar: dict | None, source: str) -> None:
        if not ARCHIVE_ENABLED or not symbol or not bar or bar.get("time") is None:
            return
        row = _bar_row(symbol, bar, source)
        self._buffer[(row["symbol"], row["time"])] = row
        self._trim_buffer()
        try:
            from app.services.data_quality import registry

            registry.note_bar(symbol, int(row["time"]))
        except Exception:
            pass

    def maybe_flush(self) -> int:
        if not ARCHIVE_ENABLED or not self._buffer:
            return 0
        if time.time() - self._last_flush < ARCHIVE_FLUSH_INTERVAL:
            return 0
        return self.flush()

    def flush(self) -> int:
        if not self._buffer:
            return 0

        rows = list(self._buffer.values())
        last_error: Exception | None = None

        for attempt in range(3):
            try:
                written = _upsert_1m_rows(rows)
                if ARCHIVE_BACKEND in ("both", "parquet") and ARCHIVE_PARQUET_ENABLED:
                    try:
                        from app.services.archive.parquet_export import append_bars_parquet

                        append_bars_parquet(rows)
                    except Exception as exc:
                        logger.warning("Parquet append on flush failed: %s", exc)
                self._buffer.clear()
                self._last_flush = time.time()
                self._total_flushed += written
                return written
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.25 * (attempt + 1))

        from app.services.archive.wal import append_wal_rows

        # Durability: rows are on disk WAL — clear RAM buffer so a stuck DB
        # cannot grow the process heap without bound (MEMORY_CENTRIC_REVIEW #1).
        append_wal_rows(rows)
        self._buffer.clear()
        self._last_flush = time.time()
        logger.warning(
            "Archive flush failed after retries (%d rows → WAL): %s",
            len(rows),
            last_error,
        )
        return 0


def _upsert_1m_rows(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if is_postgres():
            cursor.executemany(
                """
                INSERT INTO market_bars_1m
                    (symbol, time, open, high, low, close, volume, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (symbol, time) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    source = EXCLUDED.source
                """,
                [
                    (
                        r["symbol"],
                        r["time"],
                        r["open"],
                        r["high"],
                        r["low"],
                        r["close"],
                        r["volume"],
                        r["source"],
                    )
                    for r in rows
                ],
            )
        else:
            cursor.executemany(
                """
                INSERT OR REPLACE INTO market_bars_1m
                    (symbol, time, open, high, low, close, volume, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r["symbol"],
                        r["time"],
                        r["open"],
                        r["high"],
                        r["low"],
                        r["close"],
                        r["volume"],
                        r["source"],
                    )
                    for r in rows
                ],
            )
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_archive_writer() -> ArchiveWriter:
    global _writer
    if _writer is None:
        from app.services.archive.wal import replay_wal

        replay_wal(_upsert_1m_rows)
        _writer = ArchiveWriter()
    return _writer
