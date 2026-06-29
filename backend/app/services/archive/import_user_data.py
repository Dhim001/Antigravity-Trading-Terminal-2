"""User CSV/Parquet import into market_bars_1m."""

from __future__ import annotations

import logging
import os
from typing import Any

from app.config import ARCHIVE_ENABLED, BASE_DIR
from app.services.archive.backfill import symbol_has_archive, _dataframe_to_bars
from app.services.archive.writer import _upsert_1m_rows, align_bar_time

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(BASE_DIR)
_IMPORT_ROOTS = (
    os.path.normpath(os.path.join(BASE_DIR, "app", "data")),
    os.path.normpath(os.path.join(_REPO_ROOT, "data", "import")),
)


def _resolve_import_path(path: str) -> str:
    raw = os.path.normpath(path.strip())
    if os.path.isabs(raw):
        candidate = raw
    else:
        candidate = None
        for root in _IMPORT_ROOTS:
            probe = os.path.normpath(os.path.join(root, raw))
            if os.path.isfile(probe):
                candidate = probe
                break
        if candidate is None:
            candidate = os.path.normpath(os.path.join(_IMPORT_ROOTS[0], raw))

    for root in _IMPORT_ROOTS:
        root_norm = os.path.normpath(root)
        if candidate == root_norm or candidate.startswith(root_norm + os.sep):
            if os.path.isfile(candidate):
                return candidate
    raise ValueError(f"Import path not allowed or missing: {path}")


def _bars_from_csv(path: str, symbol: str, source: str) -> list[dict[str, Any]]:
    import pandas as pd

    df = pd.read_csv(path)
    if df.empty:
        return []
    cols = {c.lower(): c for c in df.columns}
    time_col = cols.get("time") or cols.get("timestamp") or cols.get("datetime") or cols.get("date")
    if not time_col:
        raise ValueError("CSV must include time/timestamp/datetime/date column")
    o = cols.get("open") or cols.get("o")
    h = cols.get("high") or cols.get("h")
    l = cols.get("low") or cols.get("l")
    c = cols.get("close") or cols.get("c")
    if not all([o, h, l, c]):
        raise ValueError("CSV must include open/high/low/close columns")
    vol_col = cols.get("volume") or cols.get("v")

    bars: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        ts = pd.to_datetime(row[time_col], utc=True, errors="coerce")
        if pd.isna(ts):
            continue
        t = align_bar_time(int(ts.timestamp()))
        bars.append({
            "symbol": symbol,
            "time": t,
            "open": float(row[o]),
            "high": float(row[h]),
            "low": float(row[l]),
            "close": float(row[c]),
            "volume": float(row[vol_col] or 0) if vol_col else 0.0,
            "source": source,
        })
    return bars


def import_user_file(
    symbol: str,
    path: str,
    *,
    fmt: str = "auto",
    source: str = "USER_IMPORT",
    skip_existing: bool = True,
) -> dict[str, Any]:
    if not ARCHIVE_ENABLED:
        return {"enabled": False, "rows_written": 0}
    if skip_existing and symbol_has_archive(symbol):
        return {"symbol": symbol, "skipped": True, "rows_written": 0}

    resolved = _resolve_import_path(path)
    ext = os.path.splitext(resolved)[1].lower()
    file_fmt = fmt if fmt != "auto" else ("parquet" if ext in (".parquet", ".pq") else "csv")

    if file_fmt == "parquet":
        import pandas as pd

        df = pd.read_parquet(resolved)
        bars = _dataframe_to_bars(symbol, df)
        for bar in bars:
            bar["source"] = source
    elif file_fmt == "csv":
        bars = _bars_from_csv(resolved, symbol, source)
    else:
        raise ValueError(f"Unsupported format: {file_fmt}")

    written = _upsert_1m_rows(bars) if bars else 0
    return {"symbol": symbol, "path": resolved, "format": file_fmt, "rows_written": written}


def run_user_import(
    imports: list[dict[str, Any]],
    *,
    source: str = "USER_IMPORT",
    skip_existing: bool = True,
) -> dict[str, Any]:
    total = 0
    details: list[dict[str, Any]] = []
    for item in imports:
        sym = item.get("symbol")
        path = item.get("path") or item.get("file")
        if not sym or not path:
            details.append({"error": "symbol and path required", "item": item})
            continue
        try:
            result = import_user_file(
                sym,
                path,
                fmt=str(item.get("format") or "auto"),
                source=source,
                skip_existing=skip_existing and not bool(item.get("force")),
            )
            total += int(result.get("rows_written") or 0)
            details.append(result)
        except Exception as exc:
            details.append({"symbol": sym, "path": path, "error": str(exc)})
    return {"rows_written": total, "imports": details}
