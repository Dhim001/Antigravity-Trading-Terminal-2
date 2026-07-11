"""Per-bot max position duration — auto-close stale holdings."""

from __future__ import annotations

import time

from app.config import RISK_MAX_POSITION_HOURS, RISK_POSITION_DURATION_ENABLED


def as_unix_seconds(ts: float | int | None) -> float | None:
    """Normalize a timestamp to unix seconds (accepts seconds or milliseconds)."""
    if ts is None:
        return None
    try:
        val = float(ts)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    # Values past ~2001-09 in milliseconds exceed 1e12.
    if val > 1_000_000_000_000:
        val /= 1000.0
    return val


def bars_held_since_open(
    opened_at: float | int | None,
    bar_time: float | int | None,
    timeframe: str,
) -> float | None:
    """Bars elapsed between position open and bar close (both unix seconds)."""
    opened_sec = as_unix_seconds(opened_at)
    bar_sec = as_unix_seconds(bar_time)
    if opened_sec is None or bar_sec is None:
        return None
    from app.services.market.timeframes import timeframe_to_secs

    try:
        tf_secs = timeframe_to_secs(timeframe)
    except ValueError:
        return None
    if tf_secs <= 0:
        return None
    return (bar_sec - opened_sec) / float(tf_secs)


def seconds_held_since_open(
    opened_at: float | int | None,
    time_ms: float | int | None,
) -> float | None:
    """Wall-clock seconds held given opened_at (unix sec) and a tick time in ms."""
    opened_sec = as_unix_seconds(opened_at)
    if opened_sec is None or time_ms is None:
        return None
    try:
        tick = float(time_ms)
    except (TypeError, ValueError):
        return None
    if tick <= 0:
        return None
    # Tick path always passes epoch milliseconds; tolerate accidental seconds.
    now_sec = tick / 1000.0 if tick > 1_000_000_000_000 else tick
    return now_sec - opened_sec


def resolve_max_position_hours(bot_config: dict | None) -> float | None:
    """Return effective max hold hours for a bot, or None when disabled."""
    cfg = bot_config or {}
    if "max_position_hours" in cfg:
        try:
            val = float(cfg["max_position_hours"])
        except (TypeError, ValueError):
            val = 0.0
        return val if val > 0 else None
    if RISK_MAX_POSITION_HOURS > 0:
        return RISK_MAX_POSITION_HOURS
    return None


def position_hold_hours(opened_at: float | None, now: float | None = None) -> float | None:
    if opened_at is None or opened_at <= 0:
        return None
    return max((now or time.time()) - opened_at, 0.0) / 3600.0


def is_position_stale(
    opened_at: float | None,
    bot_config: dict | None,
    *,
    now: float | None = None,
) -> tuple[bool, str, float | None]:
    """Return (stale, reason, limit_hours)."""
    if not RISK_POSITION_DURATION_ENABLED:
        return False, "", None

    limit = resolve_max_position_hours(bot_config)
    if limit is None:
        return False, "", None

    hours = position_hold_hours(opened_at, now)
    if hours is None:
        return False, "", limit

    if hours >= limit:
        return (
            True,
            f"Max position duration exceeded ({hours:.1f}h >= {limit:.1f}h).",
            limit,
        )
    return False, "", limit


def duration_close_bar_time(opened_at: float, limit_hours: float) -> int:
    """Stable bar_time for duration-exit signal idempotency."""
    return int(opened_at + limit_hours * 3600.0)


def position_duration_status() -> dict:
    return {
        "enabled": RISK_POSITION_DURATION_ENABLED,
        "default_max_hours": RISK_MAX_POSITION_HOURS if RISK_MAX_POSITION_HOURS > 0 else None,
    }
