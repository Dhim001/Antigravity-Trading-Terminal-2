"""Exchange session calendar — holidays and RTH from stored economic_events."""

from __future__ import annotations

import json
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.config import CALENDAR_GATES_ENABLED, RISK_EQUITY_MARKET_TZ
from app.db.connection import db_session
from app.services.bots.time_windows import is_crypto_symbol

_RTH_OPEN = time(9, 30)
_RTH_CLOSE = time(16, 0)
_EARLY_CLOSE = time(13, 0)


def _market_tz() -> ZoneInfo:
    try:
        return ZoneInfo(RISK_EQUITY_MARKET_TZ)
    except Exception:
        return ZoneInfo("America/New_York")


def _to_local(ts: float) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(_market_tz())


def _date_key(dt: datetime) -> str:
    return dt.date().isoformat()


def _load_holiday_map() -> dict[str, dict[str, Any]]:
    """Date (YYYY-MM-DD) -> holiday row (cached per call)."""
    out: dict[str, dict[str, Any]] = {}
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT event_id, event_type, title, scheduled_at, impact, raw_json
                FROM economic_events
                WHERE event_type = 'market_holiday'
                ORDER BY scheduled_at DESC
                LIMIT 500
                """
            )
            rows = cursor.fetchall()
        except Exception:
            return out
    for row in rows:
        if isinstance(row, dict):
            item = dict(row)
        else:
            item = {
                "event_id": row[0],
                "event_type": row[1],
                "title": row[2],
                "scheduled_at": row[3],
                "impact": row[4],
                "raw_json": row[5],
            }
        sched = str(item.get("scheduled_at") or "")[:10]
        if sched:
            out[sched] = item
    return out


def _parse_early_close(holiday_row: dict[str, Any] | None) -> time | None:
    if not holiday_row:
        return None
    impact = str(holiday_row.get("impact") or "").lower()
    if "early" in impact or "earlyclose" in impact:
        return _EARLY_CLOSE
    raw = holiday_row.get("raw_json")
    if raw:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(payload, dict):
                status = str(payload.get("status") or payload.get("exchange_status") or "").lower()
                if "early" in status:
                    return _EARLY_CLOSE
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def is_market_holiday(ts: float) -> tuple[bool, str | None]:
    """True when ts falls on a stored exchange holiday (US equities)."""
    local = _to_local(ts)
    holidays = _load_holiday_map()
    row = holidays.get(_date_key(local))
    if row:
        title = row.get("title") or "Market holiday"
        return True, str(title)
    return False, None


def is_equity_rth_open(symbol: str, ts: float) -> tuple[bool, str | None]:
    """
    True when US equity regular trading hours are open for symbol at ts.
    Crypto symbols are always open.
    """
    if not CALENDAR_GATES_ENABLED:
        return True, None
    if is_crypto_symbol(symbol):
        return True, None

    local = _to_local(ts)
    if local.weekday() >= 5:
        return False, "Weekend — equity market closed"

    is_hol, hol_title = is_market_holiday(ts)
    if is_hol:
        return False, f"Exchange holiday ({hol_title})"

    holidays = _load_holiday_map()
    close_t = _parse_early_close(holidays.get(_date_key(local))) or _RTH_CLOSE
    open_t = _RTH_OPEN
    local_t = local.time().replace(microsecond=0)
    if local_t < open_t:
        return False, f"Before market open ({open_t.strftime('%H:%M')} {RISK_EQUITY_MARKET_TZ})"
    if local_t >= close_t:
        label = "early close" if close_t != _RTH_CLOSE else "market close"
        return False, f"After {label} ({close_t.strftime('%H:%M')} {RISK_EQUITY_MARKET_TZ})"
    return True, None


def calendar_gate(symbol: str, ts: float | None) -> tuple[bool, str | None]:
    """Returns (blocked, reason). blocked=True means entry should not proceed."""
    if ts is None:
        return False, None
    try:
        epoch = float(ts)
    except (TypeError, ValueError):
        return False, None
    open_ok, reason = is_equity_rth_open(symbol, epoch)
    if open_ok:
        return False, None
    return True, reason or "Market session closed"
