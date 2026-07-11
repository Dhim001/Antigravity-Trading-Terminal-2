import asyncio

from app.api.context import RequestContext
from app.api.protocol import Action
from app.api.responses import send_error, send_history_update, send_to
from app.api.outbound import history_update, orderbook_update, ticks_update
from app.api.router import route
from app.services.archive.query import query_market_history_detailed
from app.config import (
    ARCHIVE_RETENTION_1M_DAYS,
    MARKET_CANDLE_SNAPSHOT_LIMIT,
    MARKET_CANDLE_SNAPSHOT_MAX,
    TERMINAL_MODE,
)
from app.services.market.timeframes import normalize_timeframe
from app.services.massive_ht_limits import massive_ht_limit


def _parse_candle_snapshot_limit(message: dict, interval: str = "1m") -> int | None:
    """Return max bars to send; None means full in-memory buffer (limit=0)."""
    raw = message.get("limit")
    if raw is None or raw == "":
        if TERMINAL_MODE == "LIVE_MASSIVE" and interval != "1m":
            return massive_ht_limit(interval, purpose="chart")
        return MARKET_CANDLE_SNAPSHOT_LIMIT
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        if TERMINAL_MODE == "LIVE_MASSIVE" and interval != "1m":
            return massive_ht_limit(interval, purpose="chart")
        return MARKET_CANDLE_SNAPSHOT_LIMIT
    if parsed <= 0:
        return None
    return min(parsed, MARKET_CANDLE_SNAPSHOT_MAX)


def _tail_candles(candles: list, limit: int | None) -> list:
    if not candles or limit is None:
        return candles
    if len(candles) <= limit:
        return candles
    return candles[-limit:]


def _feed_for(ctx: RequestContext):
    feed = getattr(ctx.oms, "feed", None)
    return feed


async def _send_orderbook_snapshot(ctx: RequestContext, symbol: str) -> None:
    feed = _feed_for(ctx)
    if not feed or not symbol:
        return
    md = feed.get_market_data(symbol)
    ob = md.get("orderbook") if md else None
    if ob and ob.get("bids") and ob.get("asks"):
        await send_to(ctx, orderbook_update({symbol: ob}))


def _parse_chart_interval(message: dict) -> str:
    raw = message.get("interval") or message.get("timeframe") or "1m"
    try:
        return normalize_timeframe(str(raw))
    except ValueError:
        return "1m"


async def _resolve_candles(feed, symbol: str, interval: str, limit: int | None) -> list:
    """1m from in-memory feed; LIVE_MASSIVE HT from Massive REST proxy (non-blocking)."""
    if (
        TERMINAL_MODE == "LIVE_MASSIVE"
        and interval != "1m"
        and hasattr(feed, "fetch_ht_candles")
    ):
        return await asyncio.to_thread(feed.fetch_ht_candles, symbol, interval, limit=limit)
    return feed.get_candles(symbol) or []


@route(Action.SUBSCRIBE_SYMBOL, tags=["market"])
async def subscribe_symbol(ctx: RequestContext) -> None:
    symbol = ctx.message.get("symbol")
    if not symbol:
        await send_error(ctx, "symbol is required")
        return
    ctx.manager.set_client_symbol(ctx.websocket, symbol)
    feed = _feed_for(ctx)
    if not feed:
        await send_error(ctx, "Market feed unavailable")
        return
    interval = _parse_chart_interval(ctx.message)
    limit = _parse_candle_snapshot_limit(ctx.message, interval)
    candles = await _resolve_candles(feed, symbol, interval, limit)
    snapshot = _tail_candles(candles, limit)
    meta = {"interval": interval, "symbol": symbol, "count": len(snapshot)}
    await send_history_update(ctx, {symbol: snapshot}, meta=meta)
    await _send_orderbook_snapshot(ctx, symbol)


@route(Action.GET_MARKET_HISTORY, tags=["market"])
async def get_market_history(ctx: RequestContext) -> None:
    symbol = ctx.message.get("symbol")
    if not symbol:
        await send_error(ctx, "symbol is required")
        return

    def _parse_ts(value, default=None):
        if value is None or value == "":
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    from_ts = _parse_ts(ctx.message.get("from"))
    to_ts = _parse_ts(ctx.message.get("to"))
    interval = ctx.message.get("interval") or "auto"

    from app.services.archive.query import query_market_history_detailed

    bars, qmeta = query_market_history_detailed(
        symbol,
        from_ts=from_ts,
        to_ts=to_ts,
        interval=interval,
        purpose="ui",
    )
    payload = history_update({symbol: bars})
    meta = {
        "symbol": symbol,
        "from": from_ts,
        "to": to_ts,
        "interval": interval,
        "count": len(bars),
        "retention_1m_days": ARCHIVE_RETENTION_1M_DAYS,
        "purpose": "ui",
        "truncated": bool(qmeta.get("truncated")),
        "limit": qmeta.get("limit"),
    }
    if bars:
        meta["oldest"] = bars[0]["time"]
        meta["newest"] = bars[-1]["time"]
    payload["meta"] = meta
    await send_to(ctx, payload)


@route(Action.GET_MARKET_TICKS, tags=["market"])
async def get_market_ticks(ctx: RequestContext) -> None:
    from app.config import ARCHIVE_TICKS_ENABLED, ARCHIVE_TICK_QUERY_LIMIT
    from app.services.archive.tick_writer import query_ticks

    if not ARCHIVE_TICKS_ENABLED:
        await send_error(ctx, "Tick archive disabled (ARCHIVE_TICKS_ENABLED=false)")
        return

    symbol = ctx.message.get("symbol")
    if not symbol:
        await send_error(ctx, "symbol is required")
        return

    def _parse_ms(value, default=None):
        if value is None or value == "":
            return default
        try:
            v = int(value)
            return v if v > 9999999999 else v * 1000
        except (TypeError, ValueError):
            return default

    import time
    now_ms = int(time.time() * 1000)
    from_ms = _parse_ms(ctx.message.get("from"), now_ms - 3600_000)
    to_ms = _parse_ms(ctx.message.get("to"), now_ms)
    tick_meta: dict = {}
    ticks = query_ticks(
        symbol, from_ms, to_ms, limit=ARCHIVE_TICK_QUERY_LIMIT, result_meta=tick_meta
    )
    await send_to(ctx, ticks_update(
        {symbol: ticks},
        meta={
            "symbol": symbol,
            "from_ms": from_ms,
            "to_ms": to_ms,
            "count": len(ticks),
            "truncated": bool(tick_meta.get("truncated")),
            "limit": tick_meta.get("limit"),
        },
    ))
