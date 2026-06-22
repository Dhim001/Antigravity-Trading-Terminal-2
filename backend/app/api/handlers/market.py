from app.api.context import RequestContext
from app.api.protocol import Action
from app.api.responses import send_error, send_history_update, send_to
from app.api.outbound import history_update, orderbook_update, ticks_update
from app.api.router import route
from app.services.archive.query import query_market_history
from app.config import (
    ARCHIVE_RETENTION_1M_DAYS,
    MARKET_CANDLE_SNAPSHOT_LIMIT,
    MARKET_CANDLE_SNAPSHOT_MAX,
)


def _parse_candle_snapshot_limit(message: dict) -> int | None:
    """Return max bars to send; None means full in-memory buffer (limit=0)."""
    raw = message.get("limit")
    if raw is None or raw == "":
        return MARKET_CANDLE_SNAPSHOT_LIMIT
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
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
    candles = feed.get_candles(symbol) or []
    limit = _parse_candle_snapshot_limit(ctx.message)
    snapshot = _tail_candles(candles, limit)
    await send_history_update(ctx, {symbol: snapshot})
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

    bars = query_market_history(symbol, from_ts=from_ts, to_ts=to_ts, interval=interval)
    payload = history_update({symbol: bars})
    meta = {
        "symbol": symbol,
        "from": from_ts,
        "to": to_ts,
        "interval": interval,
        "count": len(bars),
        "retention_1m_days": ARCHIVE_RETENTION_1M_DAYS,
    }
    if bars:
        meta["oldest"] = bars[0]["time"]
        meta["newest"] = bars[-1]["time"]
    payload["meta"] = meta
    await send_to(ctx, payload)


@route(Action.GET_MARKET_TICKS, tags=["market"])
async def get_market_ticks(ctx: RequestContext) -> None:
    from app.config import ARCHIVE_TICKS_ENABLED
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
    ticks = query_ticks(symbol, from_ms, to_ms)
    await send_to(ctx, ticks_update(
        {symbol: ticks},
        meta={"symbol": symbol, "from_ms": from_ms, "to_ms": to_ms, "count": len(ticks)},
    ))
