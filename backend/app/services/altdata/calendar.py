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


def _bar_epoch(bar: dict[str, Any] | None) -> float | None:
    if not isinstance(bar, dict):
        return None
    raw = bar.get("time")
    if raw is None:
        return None
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:
        ts /= 1000.0
    return ts


def _rth_open_with_holidays(
    ts: float,
    *,
    holidays: dict[str, dict[str, Any]],
) -> tuple[bool, str | None]:
    """RTH check using a preloaded holiday map (avoids per-bar DB hits).

    Matches ``is_equity_rth_open``: any stored holiday date is a full close;
    early-close parsing applies only when no holiday row exists (same as live).
    """
    local = _to_local(ts)
    if local.weekday() >= 5:
        return False, "Weekend — equity market closed"
    row = holidays.get(_date_key(local))
    if row:
        title = row.get("title") or "Market holiday"
        return False, f"Exchange holiday ({title})"
    close_t = _parse_early_close(None) or _RTH_CLOSE
    open_t = _RTH_OPEN
    local_t = local.time().replace(microsecond=0)
    if local_t < open_t:
        return False, f"Before market open ({open_t.strftime('%H:%M')} {RISK_EQUITY_MARKET_TZ})"
    if local_t >= close_t:
        return False, f"After market close ({close_t.strftime('%H:%M')} {RISK_EQUITY_MARKET_TZ})"
    return True, None


def filter_equity_rth_candles(symbol: str, candles: list | None) -> list:
    """Keep only US equity RTH bars for training/backtest feature builds.

    Crypto (and when calendar gates are disabled) returns the list unchanged.
    Holiday map is loaded once for the batch.
    """
    bars = list(candles or [])
    if not bars:
        return bars
    if not CALENDAR_GATES_ENABLED or is_crypto_symbol(symbol):
        return bars
    try:
        holidays = _load_holiday_map()
    except Exception:
        holidays = {}
    out: list = []
    for bar in bars:
        epoch = _bar_epoch(bar if isinstance(bar, dict) else None)
        if epoch is None:
            out.append(bar)
            continue
        open_ok, _ = _rth_open_with_holidays(epoch, holidays=holidays)
        if open_ok:
            out.append(bar)
    return out


def session_features_for_bar(
    symbol: str | None,
    ts: float | None,
) -> dict[str, float]:
    """Equity session features for ML; crypto gets always-open defaults.

    Uses clock-based RTH (9:30–16:00 ET, weekdays) without hitting the holiday DB
    so training feature extraction stays cheap. Live calendar gates still enforce
    holidays separately.

    Returns:
      is_rth, minutes_from_open_norm, et_hour_sin, et_hour_cos
    """
    import math

    # Crypto / unknown → treat as always open; keep ET clocks at neutral defaults.
    if not symbol or is_crypto_symbol(symbol):
        return {
            "is_rth": 1.0,
            "minutes_from_open_norm": 0.0,
            "et_hour_sin": 0.0,
            "et_hour_cos": 1.0,
        }
    if ts is None:
        return {
            "is_rth": 0.0,
            "minutes_from_open_norm": 0.0,
            "et_hour_sin": 0.0,
            "et_hour_cos": 1.0,
        }
    try:
        epoch = float(ts)
    except (TypeError, ValueError):
        return {
            "is_rth": 0.0,
            "minutes_from_open_norm": 0.0,
            "et_hour_sin": 0.0,
            "et_hour_cos": 1.0,
        }
    if epoch > 1e12:
        epoch /= 1000.0

    local = _to_local(epoch)
    et_hour = local.hour + local.minute / 60.0
    angle = 2.0 * math.pi * et_hour / 24.0
    et_sin, et_cos = math.sin(angle), math.cos(angle)

    local_t = local.time().replace(microsecond=0)
    open_ok = local.weekday() < 5 and _RTH_OPEN <= local_t < _RTH_CLOSE
    open_dt = local.replace(hour=_RTH_OPEN.hour, minute=_RTH_OPEN.minute, second=0, microsecond=0)
    minutes = (local - open_dt).total_seconds() / 60.0
    session_len = float(
        (_RTH_CLOSE.hour * 60 + _RTH_CLOSE.minute) - (_RTH_OPEN.hour * 60 + _RTH_OPEN.minute)
    )
    if session_len <= 0:
        session_len = 390.0
    if open_ok:
        minutes_norm = max(0.0, min(1.0, minutes / session_len))
    else:
        minutes_norm = 0.0

    return {
        "is_rth": 1.0 if open_ok else 0.0,
        "minutes_from_open_norm": minutes_norm,
        "et_hour_sin": et_sin,
        "et_hour_cos": et_cos,
    }
