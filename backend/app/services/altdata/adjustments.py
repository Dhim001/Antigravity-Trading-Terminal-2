"""Corporate-action price adjustments for backtest/archive reads."""

from __future__ import annotations

import json
from typing import Any

from app.db.connection import db_session


def _parse_split_mult(metadata: dict[str, Any]) -> float | None:
    """Backward price multiplier for a split (pre-split bars × mult)."""
    for from_key, to_key in (
        ("split_from", "split_to"),
        ("from", "to"),
        ("old_shares", "new_shares"),
    ):
        try:
            split_from = float(metadata.get(from_key) or 0)
            split_to = float(metadata.get(to_key) or 0)
        except (TypeError, ValueError):
            continue
        if split_from > 0 and split_to > 0:
            return split_from / split_to
    return None


def _parse_div_mult(metadata: dict[str, Any], prev_close: float | None) -> float | None:
    try:
        cash = float(metadata.get("cash_amount") or metadata.get("rate") or 0)
    except (TypeError, ValueError):
        return None
    if cash <= 0 or not prev_close or prev_close <= 0:
        return None
    return max(0.0, 1.0 - (cash / prev_close))


def load_corporate_actions(symbol: str) -> list[dict[str, Any]]:
    sym = str(symbol or "").upper()
    if not sym:
        return []
    rows: list[dict[str, Any]] = []
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT event_type, event_date, title, metadata_json
                FROM corporate_events
                WHERE symbol = ?
                ORDER BY event_date ASC
                """,
                (sym,),
            )
            raw = cursor.fetchall()
        except Exception:
            return []
    for row in raw:
        if isinstance(row, dict):
            item = dict(row)
            meta_raw = item.get("metadata_json")
        else:
            item = {
                "event_type": row[0],
                "event_date": row[1],
                "title": row[2],
                "metadata_json": row[3],
            }
            meta_raw = row[3]
        meta: dict[str, Any] = {}
        if meta_raw:
            try:
                meta = json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        date_str = str(item.get("event_date") or "")[:10]
        if not date_str:
            continue
        try:
            parts = [int(x) for x in date_str.split("-")]
            effective_ts = int(datetime_utc(parts[0], parts[1], parts[2]))
        except (ValueError, IndexError):
            continue
        rows.append({
            "event_type": item.get("event_type"),
            "effective_ts": effective_ts,
            "title": item.get("title"),
            "metadata": meta,
        })
    return rows


def datetime_utc(year: int, month: int, day: int) -> float:
    from datetime import datetime, timezone
    return datetime(year, month, day, tzinfo=timezone.utc).timestamp()


def cumulative_adjustment_factor(
    symbol: str,
    bar_time: int,
    *,
    mode: str = "split_only",
) -> float:
    """Product of adjustment multipliers for bars strictly before each ex-date."""
    actions = load_corporate_actions(symbol)
    if not actions:
        return 1.0
    factor = 1.0
    for action in actions:
        eff = int(action.get("effective_ts") or 0)
        if bar_time >= eff:
            continue
        etype = str(action.get("event_type") or "")
        meta = action.get("metadata") or {}
        if etype == "split":
            mult = _parse_split_mult(meta)
            if mult and mult > 0:
                factor *= mult
        elif etype == "dividend" and mode == "total_return":
            mult = _parse_div_mult(meta, meta.get("close") or meta.get("prev_close"))
            if mult and mult > 0:
                factor *= mult
    return factor


def apply_price_adjustments(
    bars: list[dict],
    symbol: str,
    *,
    mode: str = "split_only",
) -> list[dict]:
    """Return new bar list with backward split (and optional dividend) adjustment."""
    if not bars or mode == "raw":
        return bars
    out: list[dict] = []
    for bar in bars:
        t = int(bar.get("time") or 0)
        mult = cumulative_adjustment_factor(symbol, t, mode=mode)
        if mult == 1.0:
            out.append(dict(bar))
            continue
        vol = float(bar.get("volume") or 0)
        inv = 1.0 / mult if mult > 0 else 1.0
        out.append({
            **bar,
            "open": float(bar["open"]) * mult,
            "high": float(bar["high"]) * mult,
            "low": float(bar["low"]) * mult,
            "close": float(bar["close"]) * mult,
            "volume": vol * inv,
        })
    return out


def count_splits_in_range(symbol: str, from_ts: int, to_ts: int) -> int:
    count = 0
    for action in load_corporate_actions(symbol):
        eff = int(action.get("effective_ts") or 0)
        if from_ts <= eff <= to_ts and action.get("event_type") == "split":
            count += 1
    return count


def detect_unadjusted_split_jumps(bars: list[dict], *, threshold_pct: float = 35.0) -> int:
    """Heuristic: count bar-to-bar close moves that look like unhandled splits."""
    if len(bars) < 2:
        return 0
    hits = 0
    for i in range(1, len(bars)):
        prev = float(bars[i - 1].get("close") or 0)
        cur = float(bars[i].get("close") or 0)
        if prev <= 0 or cur <= 0:
            continue
        ret = abs((cur - prev) / prev) * 100.0
        if ret >= threshold_pct:
            hits += 1
    return hits
