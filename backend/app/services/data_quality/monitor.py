"""Evaluate feed health — stale ticks, candle gaps, abnormal spreads."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import (
    DATA_QUALITY_GAP_BAR_SEC,
    DATA_QUALITY_MAX_SPREAD_PCT,
    DATA_QUALITY_STALE_PAUSE_SEC,
    DATA_QUALITY_STALE_WARN_SEC,
    DATA_QUALITY_ACTIVE_PAUSE,
    ARCHIVE_ENABLED,
)
from app.db.connection import db_session
from app.observability.metrics import inc, observe
from app.services.data_quality import registry

logger = logging.getLogger(__name__)

_last_pause_at: float = 0.0
_PAUSE_COOLDOWN_SEC = 120.0
_MONITOR_START = time.time()
_STARTUP_GRACE_SEC = 30.0
# Bots paused by data quality (not manual operator pause) — eligible for auto-resume.
_dq_paused_bot_ids: set[str] = set()


def _count_candle_gaps(symbol: str, *, lookback_bars: int = 120) -> int:
    if not ARCHIVE_ENABLED:
        return 0
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT time FROM market_bars_1m
            WHERE symbol = ?
            ORDER BY time DESC
            LIMIT ?
            """,
            (symbol, lookback_bars),
        )
        times = sorted(int(r[0] if not isinstance(r, dict) else r["time"]) for r in cursor.fetchall())

    if len(times) < 2:
        return 0
    gaps = 0
    for i in range(1, len(times)):
        if times[i] - times[i - 1] > DATA_QUALITY_GAP_BAR_SEC:
            gaps += 1
    return gaps


def _count_unadjusted_split_jumps(symbol: str, *, lookback_bars: int = 120) -> int:
    if not ARCHIVE_ENABLED:
        return 0
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT time, open, high, low, close, volume
            FROM market_bars_1m
            WHERE symbol = ?
            ORDER BY time DESC
            LIMIT ?
            """,
            (symbol, lookback_bars),
        )
        rows = cursor.fetchall()
    bars = []
    for row in reversed(rows):
        if isinstance(row, dict):
            bars.append(row)
        else:
            bars.append({
                "time": row[0], "open": row[1], "high": row[2],
                "low": row[3], "close": row[4], "volume": row[5],
            })
    from app.services.altdata.adjustments import detect_unadjusted_split_jumps
    return detect_unadjusted_split_jumps(bars)


def evaluate_symbols(symbols: list[str]) -> dict[str, Any]:
    snap = registry.snapshot()
    per = snap.get("symbols") or {}
    issues: list[dict[str, Any]] = []
    stale_warn: list[str] = []
    stale_severe: list[str] = []
    spread_alerts: list[str] = []
    gap_symbols: list[str] = []
    split_jump_symbols: list[str] = []

    for sym in symbols:
        row = per.get(sym, {})
        stale = row.get("stale_sec")
        if row.get("last_tick_ms") is None:
            if time.time() - _MONITOR_START < _STARTUP_GRACE_SEC:
                stale = None
            else:
                stale = DATA_QUALITY_STALE_PAUSE_SEC + 1.0
        if stale is not None:
            observe("feed_stale_seconds", stale, labels={"symbol": sym})
            if stale >= DATA_QUALITY_STALE_PAUSE_SEC:
                stale_severe.append(sym)
                issues.append({"symbol": sym, "type": "stale", "severity": "critical", "stale_sec": stale})
            elif stale >= DATA_QUALITY_STALE_WARN_SEC:
                stale_warn.append(sym)
                issues.append({"symbol": sym, "type": "stale", "severity": "warn", "stale_sec": stale})

        spread = row.get("spread_pct")
        if spread is not None and spread > DATA_QUALITY_MAX_SPREAD_PCT:
            spread_alerts.append(sym)
            inc("abnormal_spread_total", labels={"symbol": sym})
            issues.append({"symbol": sym, "type": "spread", "severity": "warn", "spread_pct": spread})

        gaps = _count_candle_gaps(sym)
        if gaps > 0:
            gap_symbols.append(sym)
            inc("candle_gap_count", value=gaps, labels={"symbol": sym})
            issues.append({"symbol": sym, "type": "gap", "severity": "warn", "gap_count": gaps})

        split_jumps = _count_unadjusted_split_jumps(sym)
        if split_jumps > 0:
            split_jump_symbols.append(sym)
            issues.append({
                "symbol": sym,
                "type": "split_jump",
                "severity": "warn",
                "jump_count": split_jumps,
            })

    return {
        "checked_at_ms": snap.get("checked_at_ms"),
        "symbols": {s: per.get(s, {}) for s in symbols},
        "stale_warn": stale_warn,
        "stale_severe": stale_severe,
        "spread_alerts": spread_alerts,
        "gap_symbols": gap_symbols,
        "split_jump_symbols": split_jump_symbols,
        "issues": issues,
        "healthy": not issues,
    }


async def evaluate_and_act(feed, bot_manager) -> dict[str, Any]:
    """Run evaluation; pause bots on severe stale and resume when feed recovers."""
    global _last_pause_at
    symbols = list(getattr(feed, "symbols", []) or [])
    if not symbols:
        return {"healthy": True, "symbols": {}, "issues": []}

    report = evaluate_symbols(symbols)
    severe = set(report.get("stale_severe") or [])

    if DATA_QUALITY_ACTIVE_PAUSE and bot_manager is not None:
        report = await _resume_bots_on_feed_recovery(bot_manager, severe, report)

    if not severe or not DATA_QUALITY_ACTIVE_PAUSE or bot_manager is None:
        return report

    affected_bot_ids: set[str] = set()
    for bot_id, bot in bot_manager.active_bots.items():
        if bot.get("status") != "RUNNING":
            continue
        if bot.get("symbol") in severe:
            affected_bot_ids.add(bot_id)

    if not affected_bot_ids:
        return report

    now = time.time()
    if now - _last_pause_at < _PAUSE_COOLDOWN_SEC:
        report["pause_skipped_cooldown"] = True
        return report

    _last_pause_at = now
    paused = 0
    for bot_id in affected_bot_ids:
        await bot_manager.pause_bot(bot_id)
        _dq_paused_bot_ids.add(bot_id)
        sym = bot_manager.active_bots.get(bot_id, {}).get("symbol")
        await bot_manager.log_bot_event(
            bot_id,
            "WARN",
            f"Data quality: stale feed on {sym} — bot paused.",
        )
        paused += 1

    logger.warning(
        "Data quality pause: %d bot(s) paused due to stale feed on %s",
        paused,
        ", ".join(sorted(severe)),
    )
    report["bots_paused"] = paused
    report["paused_symbols"] = sorted(severe)
    await _publish_bot_reload()
    return report


async def _resume_bots_on_feed_recovery(
    bot_manager,
    severe: set[str],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Resume bots that were auto-paused for stale feed once their symbol is healthy again."""
    if not _dq_paused_bot_ids:
        return report

    resumed = 0
    cleared: list[str] = []
    for bot_id in list(_dq_paused_bot_ids):
        bot = bot_manager.active_bots.get(bot_id)
        if not bot:
            cleared.append(bot_id)
            continue
        if bot.get("status") != "PAUSED":
            cleared.append(bot_id)
            continue
        sym = bot.get("symbol")
        if sym in severe:
            continue
        try:
            await bot_manager.resume_bot(bot_id)
            await bot_manager.log_bot_event(
                bot_id,
                "INFO",
                f"Data quality: feed on {sym} recovered — bot resumed.",
            )
            resumed += 1
            cleared.append(bot_id)
        except Exception as exc:
            logger.warning("Data quality auto-resume failed for %s: %s", bot_id, exc)

    for bot_id in cleared:
        _dq_paused_bot_ids.discard(bot_id)

    if resumed:
        logger.info(
            "Data quality resume: %d bot(s) resumed after feed recovery",
            resumed,
        )
        report["bots_resumed"] = resumed
        await _publish_bot_reload()
    return report


async def _publish_bot_reload() -> None:
    try:
        from app.services.events import channels, publish as event_publish

        await event_publish.publish(channels.BOT_RELOAD, {"source": "data_quality"})
    except Exception:
        pass


def data_quality_stats_from_report(report: dict[str, Any]) -> dict[str, Any]:
    """Compact summary for system_stats / diagnostics."""
    return {
        "healthy": report.get("healthy"),
        "stale_warn": report.get("stale_warn") or [],
        "stale_severe": report.get("stale_severe") or [],
        "spread_alerts": report.get("spread_alerts") or [],
        "gap_symbols": report.get("gap_symbols") or [],
        "split_jump_symbols": report.get("split_jump_symbols") or [],
        "issue_count": len(report.get("issues") or []),
        "bots_paused": report.get("bots_paused"),
        "bots_resumed": report.get("bots_resumed"),
    }
