"""Dispatch middleware: logging, rate limits, error handling."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from app.api.context import RequestContext
from app.api.protocol import Action
from app.api.responses import send_order_result
from app.api.types import RouteMeta

logger = logging.getLogger(__name__)

TRADE_RATE_LIMIT_ACTIONS = frozenset({
    Action.PLACE_ORDER,
    Action.CANCEL_ORDER,
    Action.UPDATE_POSITION_SL_TP,
    Action.BOT_CREATE,
})

TRADE_MIN_INTERVAL_SEC = 0.5
_last_trade_at: dict[str, float] = {}


def _rate_key(ctx: RequestContext) -> str:
    return str(ctx.message.get("_rate_key") or id(ctx.websocket) or "http")


async def middleware_log(ctx: RequestContext, meta: RouteMeta) -> None:
    logger.info("Action start: %s tags=%s", ctx.action, meta.tags)


async def middleware_rate_limit(ctx: RequestContext, meta: RouteMeta) -> bool:
    """Return True if request should abort (rate limited)."""
    if ctx.action not in TRADE_RATE_LIMIT_ACTIONS:
        return False
    key = _rate_key(ctx)
    now = time.monotonic()
    last = _last_trade_at.get(key, 0.0)
    if now - last < TRADE_MIN_INTERVAL_SEC:
        await send_order_result(ctx, {
            "status": "error",
            "message": "Rate limited — wait before submitting another trade action",
        })
        return True
    _last_trade_at[key] = now
    return False


async def run_handler(ctx: RequestContext, meta: RouteMeta) -> None:
    await meta.handler(ctx)


MiddlewareFn = Callable[[RequestContext, RouteMeta], Awaitable[None]]
HandlerFn = Callable[[RequestContext, RouteMeta], Awaitable[None]]

MIDDLEWARE: list[MiddlewareFn] = [middleware_log]


async def dispatch_with_middleware(
    ctx: RequestContext,
    meta: RouteMeta,
    handler: HandlerFn = run_handler,
) -> None:
    for mw in MIDDLEWARE:
        await mw(ctx, meta)
    if await middleware_rate_limit(ctx, meta):
        return
    try:
        await handler(ctx, meta)
    except Exception as exc:
        logger.exception("Handler error for action %s", ctx.action)
        from app.api.responses import send_error
        await send_error(ctx, f"Request processing failed: {exc}")
