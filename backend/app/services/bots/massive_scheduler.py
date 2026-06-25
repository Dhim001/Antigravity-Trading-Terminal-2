"""LIVE_MASSIVE bot scheduling — adapts to Massive feed semantics (no feed mutation)."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import ALLOW_LIVE_BOTS
from app.services.bots.execution_mode import is_live_massive
from app.services.bots.paper_oms import run_paper_oms_tick
from app.services.market.timeframes import normalize_timeframe

logger = logging.getLogger(__name__)

# HT BAR_CLOSE: evaluate at most once per new 1m bar (not every broadcast tick).
_ht_last_1m_bar: dict[str, int] = {}


def _bot_bar_timeframe(bot: dict) -> str:
    raw = (bot.get("timeframe") or "1m").strip()
    if raw.lower() == "tick":
        return "1m"
    try:
        return normalize_timeframe(raw)
    except ValueError:
        return "1m"


def _watched_symbols(bot_manager: Any) -> set[str]:
    return {
        b["symbol"]
        for b in bot_manager.active_bots.values()
        if b.get("status") == "RUNNING"
    }


def _tick_symbols(bot_manager: Any) -> set[str]:
    return {
        b["symbol"]
        for b in bot_manager.active_bots.values()
        if b.get("status") == "RUNNING"
        and b.get("execution_mode", "BAR_CLOSE") == "TICK"
    }


def _ht_timeframes_for_symbol(bot_manager: Any, symbol: str) -> set[str]:
    out: set[str] = set()
    for bot in bot_manager.active_bots.values():
        if bot.get("status") != "RUNNING":
            continue
        if bot["symbol"] != symbol:
            continue
        if bot.get("execution_mode", "BAR_CLOSE") == "TICK":
            continue
        tf = _bot_bar_timeframe(bot)
        if tf != "1m":
            out.add(tf)
    return out


def _latest_1m_bar_time(feed: Any, symbol: str) -> int | None:
    if not hasattr(feed, "get_candles"):
        return None
    candles = feed.get_candles(symbol) or []
    if not candles:
        return None
    t = candles[-1].get("time")
    return int(t) if t is not None else None


def _should_eval_ht_bar_close(feed: Any, symbol: str) -> bool:
    """Run HT evaluation only when the feed 1m series advances to a new bar."""
    bar_time = _latest_1m_bar_time(feed, symbol)
    if bar_time is None:
        return False
    prev = _ht_last_1m_bar.get(symbol)
    if prev == bar_time:
        return False
    _ht_last_1m_bar[symbol] = bar_time
    return True


async def run_massive_bot_tick(
    bot_manager: Any,
    feed: Any,
    manager: Any,
    oms: Any,
    *,
    last_prices: dict[str, float] | None = None,
) -> dict[str, float]:
    """
    Paper OMS + TICK bots + HT BAR_CLOSE evaluation on each Massive broadcast tick.

    Does not modify MassiveFeedService — reads prices/candles via public API only.
    """
    if not is_live_massive() or not ALLOW_LIVE_BOTS:
        return last_prices or {}

    await run_paper_oms_tick(oms, bot_manager, manager)

    if not bot_manager.active_bots:
        return last_prices or {}

    now_ms = int(time.time() * 1000)
    prices = dict(last_prices or {})

    for symbol in _tick_symbols(bot_manager):
        if symbol not in feed.symbols:
            continue
        md = feed.get_market_data(symbol)
        price = float(md.get("price") or 0)
        if price <= 0:
            continue
        prev = prices.get(symbol)
        if prev is not None and abs(price - prev) < 1e-12:
            continue
        prices[symbol] = price
        await bot_manager.process_price_tick(symbol, price, now_ms)

    for symbol in _watched_symbols(bot_manager):
        ht_tfs = _ht_timeframes_for_symbol(bot_manager, symbol)
        if ht_tfs and _should_eval_ht_bar_close(feed, symbol):
            await bot_manager.process_massive_ht_bar_close(symbol, feed, ht_tfs)

    return prices
