"""Time-based risk controls — no-trade windows and weekend flatten (crypto exempt)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import (
    RISK_EQUITY_MARKET_TZ,
    RISK_NO_TRADE_WINDOWS,
    RISK_TIME_CONTROLS_ENABLED,
    RISK_WEEKEND_FLATTEN_ENABLED,
    RISK_WEEKEND_FLATTEN_FRIDAY_AFTER,
    CRYPTO_SYMBOLS,
)
from app.services.massive_symbols import is_crypto_terminal_symbol


_WINDOW_RE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class TimeWindow:
    start: time
    end: time


def is_crypto_symbol(symbol: str) -> bool:
    sym = str(symbol or "").upper().strip()
    return is_crypto_terminal_symbol(sym) or sym in CRYPTO_SYMBOLS


def _parse_hhmm(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time {value!r} — expected HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    return time(hour, minute)


def parse_no_trade_windows(spec: str) -> list[TimeWindow]:
    windows: list[TimeWindow] = []
    for chunk in (spec or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = _WINDOW_RE.match(chunk)
        if not match:
            raise ValueError(f"Invalid no-trade window {chunk!r} — expected HH:MM-HH:MM")
        sh, sm, eh, em = (int(match.group(i)) for i in range(1, 5))
        windows.append(TimeWindow(time(sh, sm), time(eh, em)))
    return windows


def _market_tz() -> ZoneInfo:
    try:
        return ZoneInfo(RISK_EQUITY_MARKET_TZ)
    except Exception:
        return ZoneInfo("America/New_York")


def _to_market_local(now: datetime) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("UTC"))
    return now.astimezone(_market_tz())


def _time_in_window(local_t: time, window: TimeWindow) -> bool:
    if window.start <= window.end:
        return window.start <= local_t <= window.end
    # Overnight span (not used for RTH windows, but handled for completeness)
    return local_t >= window.start or local_t <= window.end


def is_no_trade_window(now: datetime | None, symbol: str) -> tuple[bool, str]:
    """Block equity entries during configured RTH no-trade windows. Crypto is exempt."""
    if not RISK_TIME_CONTROLS_ENABLED:
        return False, ""
    if is_crypto_symbol(symbol):
        return False, ""
    if not RISK_NO_TRADE_WINDOWS.strip():
        return False, ""

    local = _to_market_local(now or datetime.now(_market_tz()))
    if local.weekday() >= 5:
        return False, ""

    try:
        windows = parse_no_trade_windows(RISK_NO_TRADE_WINDOWS)
    except ValueError:
        return False, ""

    local_t = local.time().replace(microsecond=0)
    for window in windows:
        if _time_in_window(local_t, window):
            label = f"{window.start.strftime('%H:%M')}-{window.end.strftime('%H:%M')}"
            return True, f"No-trade window ({label} {RISK_EQUITY_MARKET_TZ})."
    return False, ""


def _friday_flatten_time() -> time:
    return _parse_hhmm(RISK_WEEKEND_FLATTEN_FRIDAY_AFTER)


def _weekend_anchor_friday(local: datetime) -> datetime:
    """Friday date (local) for the weekend containing `local`."""
    weekday = local.weekday()
    if weekday == 4:
        return local.replace(hour=0, minute=0, second=0, microsecond=0)
    if weekday == 5:
        return (local - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if weekday == 6:
        return (local - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    raise ValueError("Not in weekend flatten window")


def in_weekend_flatten_window(now: datetime | None = None) -> bool:
    """True during Fri (after cutoff) through Sunday in market TZ."""
    if not RISK_TIME_CONTROLS_ENABLED or not RISK_WEEKEND_FLATTEN_ENABLED:
        return False

    local = _to_market_local(now or datetime.now(_market_tz()))
    weekday = local.weekday()
    flatten_after = _friday_flatten_time()

    if weekday == 4:
        return local.time() >= flatten_after
    if weekday in (5, 6):
        return True
    return False


def weekend_flatten_bar_time(now: datetime | None = None) -> int:
    """Stable numeric bar_time for weekend-flatten signal idempotency."""
    local = _to_market_local(now or datetime.now(_market_tz()))
    friday = _weekend_anchor_friday(local)
    return int(friday.strftime("%Y%m%d"))


def should_flatten_symbol(symbol: str, now: datetime | None = None) -> bool:
    """Non-crypto symbols flatten during the weekend window; crypto is always exempt."""
    if is_crypto_symbol(symbol):
        return False
    return in_weekend_flatten_window(now)


def time_controls_status(now: datetime | None = None) -> dict:
    local = _to_market_local(now or datetime.now(_market_tz()))
    blocked, reason = is_no_trade_window(local, "AAPL")
    status = {
        "enabled": RISK_TIME_CONTROLS_ENABLED,
        "no_trade_windows": RISK_NO_TRADE_WINDOWS,
        "market_tz": RISK_EQUITY_MARKET_TZ,
        "equity_no_trade_active": blocked,
        "equity_no_trade_reason": reason if blocked else None,
        "weekend_flatten_enabled": RISK_WEEKEND_FLATTEN_ENABLED,
        "weekend_flatten_friday_after": RISK_WEEKEND_FLATTEN_FRIDAY_AFTER,
        "weekend_flatten_active": in_weekend_flatten_window(local),
        "crypto_exempt": True,
    }
    try:
        from app.config import CALENDAR_GATES_ENABLED, MACRO_GATES_ENABLED
        from app.services.altdata.calendar import is_market_holiday, is_equity_rth_open
        from app.services.altdata.event_policy import get_upcoming_macro

        status["calendar_gates_enabled"] = CALENDAR_GATES_ENABLED
        status["macro_gates_enabled"] = MACRO_GATES_ENABLED
        if CALENDAR_GATES_ENABLED:
            import time as _time

            epoch = local.timestamp()
            is_hol, hol_title = is_market_holiday(epoch)
            rth_open, rth_reason = is_equity_rth_open("AAPL", epoch)
            status["market_holiday_today"] = is_hol
            status["market_holiday_title"] = hol_title
            status["equity_rth_open"] = rth_open
            status["equity_rth_reason"] = rth_reason
        if MACRO_GATES_ENABLED:
            status["upcoming_macro"] = get_upcoming_macro(days=3)[:5]
    except Exception:
        pass
    return status
