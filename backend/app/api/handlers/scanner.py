"""Market scanner WebSocket/HTTP handlers."""

from __future__ import annotations

import time

from app.api.context import RequestContext
from app.api.outbound import error
from app.api.protocol import Action, MessageType
from app.api.responses import send_to
from app.api.router import route
from app.config import SCANNER_ENABLED
from app.observability.metrics import inc, observe
from app.services.scanner.market_scanner import MarketScannerService

_scan_last_at: dict[str, float] = {}
SCAN_MIN_INTERVAL_SEC = 30.0

_scanner: MarketScannerService | None = None


def get_scanner(feed=None) -> MarketScannerService:
    global _scanner
    if _scanner is None:
        _scanner = MarketScannerService(feed=feed)
    return _scanner


def _rate_key(ctx: RequestContext) -> str:
    return str(ctx.message.get("_rate_key") or id(ctx.websocket) or "http")


@route(Action.MARKET_SCAN, tags=["scanner"])
async def market_scan(ctx: RequestContext) -> None:
    if not SCANNER_ENABLED:
        await send_to(ctx, error("Market scanner is disabled"))
        return

    key = _rate_key(ctx)
    now = time.monotonic()
    last = _scan_last_at.get(key, 0.0)
    if now - last < SCAN_MIN_INTERVAL_SEC:
        await send_to(ctx, error("Rate limited — wait before scanning again"))
        return
    _scan_last_at[key] = now

    msg = ctx.message
    symbols = msg.get("symbols")
    if not symbols:
        feed = getattr(ctx.oms, "feed", None)
        if feed and hasattr(feed, "_symbols"):
            symbols = list(feed._symbols.keys())
        else:
            from app.config import SYMBOLS
            symbols = list(SYMBOLS.keys())

    signal_filter = (msg.get("signal_filter") or "any").upper()
    if signal_filter not in ("ANY", "BUY", "SELL", "NONE"):
        signal_filter = "ANY"
    sort_by = msg.get("sort_by") or "score"

    scanner = get_scanner(getattr(ctx.oms, "feed", None))
    t0 = time.monotonic()
    result = await scanner.scan(
        list(symbols),
        signal_filter=signal_filter,
        sort_by=sort_by,
    )
    observe("market_scan_duration_seconds", time.monotonic() - t0)
    inc("market_scan_total", labels={"rows": str(result.get("count", 0))})

    await send_to(ctx, {"type": MessageType.SCAN_RESULTS, "data": result})
