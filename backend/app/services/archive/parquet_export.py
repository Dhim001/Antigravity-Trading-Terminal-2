"""Export archived bars to Parquet files for offline analytics."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from app.config import ARCHIVE_PARQUET_DIR, TERMINAL_MODE
from app.services.archive.query import query_market_history

logger = logging.getLogger(__name__)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def append_bars_parquet(rows: list[dict[str, Any]]) -> int:
    """Append flushed 1m bar rows to date-partitioned Parquet files."""
    if not rows:
        return 0
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("pyarrow is required for Parquet export") from exc

    from datetime import datetime, timezone

    written = 0
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_symbol.setdefault(row["symbol"], []).append(row)

    for symbol, sym_rows in by_symbol.items():
        sym_dir = os.path.join(ARCHIVE_PARQUET_DIR, symbol.replace("/", "_"))
        _ensure_dir(sym_dir)
        by_date: dict[str, list[dict[str, Any]]] = {}
        for row in sym_rows:
            date_key = datetime.fromtimestamp(int(row["time"]), tz=timezone.utc).strftime("%Y-%m-%d")
            by_date.setdefault(date_key, []).append(row)

        for date_key, date_rows in by_date.items():
            path = os.path.join(sym_dir, f"1m_{date_key}.parquet")
            table = pa.Table.from_pylist(date_rows)
            if os.path.isfile(path):
                existing = pq.read_table(path)
                table = pa.concat_tables([existing, table])
            pq.write_table(table, path, compression="snappy")
            written += len(date_rows)

    return written


def export_bars_to_parquet(
    symbol: str,
    *,
    from_ts: int | None = None,
    to_ts: int | None = None,
    interval: str = "auto",
    out_dir: str | None = None,
) -> dict[str, Any]:
    """Write archived bars for one symbol to a Parquet file."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("pyarrow is required for Parquet export") from exc

    now = int(time.time())
    to_ts = int(to_ts if to_ts is not None else now)
    from_ts = int(from_ts if from_ts is not None else now - 86400 * 90)

    bars = query_market_history(symbol, from_ts, to_ts, interval=interval)
    if not bars:
        return {"symbol": symbol, "rows": 0, "path": None}

    base = out_dir or ARCHIVE_PARQUET_DIR
    sym_dir = os.path.join(base, symbol.replace("/", "_"))
    _ensure_dir(sym_dir)

    fname = f"{symbol}_{interval}_{from_ts}_{to_ts}.parquet".replace("/", "_")
    path = os.path.join(sym_dir, fname)

    table = pa.Table.from_pylist(bars)
    pq.write_table(table, path, compression="snappy")

    return {
        "symbol": symbol,
        "rows": len(bars),
        "path": path,
        "interval": interval,
        "from": from_ts,
        "to": to_ts,
    }


def export_all_symbols(
    symbols: list[str],
    *,
    days: int = 90,
    interval: str = "auto",
) -> dict[str, Any]:
    now = int(time.time())
    from_ts = now - int(days) * 86400
    results = []
    total_rows = 0
    for symbol in symbols:
        try:
            meta = export_bars_to_parquet(
                symbol, from_ts=from_ts, to_ts=now, interval=interval
            )
            total_rows += meta.get("rows", 0)
            results.append(meta)
        except Exception as exc:
            logger.error("Parquet export failed for %s: %s", symbol, exc)
            results.append({"symbol": symbol, "error": str(exc), "rows": 0})

    return {
        "source": TERMINAL_MODE,
        "days": days,
        "interval": interval,
        "total_rows": total_rows,
        "symbols": len(symbols),
        "files": results,
        "out_dir": ARCHIVE_PARQUET_DIR,
    }
