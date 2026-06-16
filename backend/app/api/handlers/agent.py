"""Chart Analyst agent WebSocket/HTTP handlers."""

from __future__ import annotations

import time

from app.api.context import RequestContext
from app.api.outbound import agent_insight, error
from app.api.protocol import Action
from app.api.responses import send_to
from app.api.router import route
from app.config import AGENT_ENABLED

_analyze_last_at: dict[str, float] = {}
ANALYZE_MIN_INTERVAL_SEC = 10.0


def _rate_key(ctx: RequestContext, symbol: str) -> str:
    client = str(ctx.message.get("_rate_key") or id(ctx.websocket) or "http")
    return f"{client}:{symbol.upper()}"


@route(Action.CHART_ANALYZE, tags=["agent"])
async def chart_analyze(ctx: RequestContext) -> None:
    if not AGENT_ENABLED:
        await send_to(ctx, error("Chart analyst is disabled"))
        return

    symbol = (ctx.message.get("symbol") or "").upper().strip()
    if not symbol:
        await send_to(ctx, error("symbol is required"))
        return

    key = _rate_key(ctx, symbol)
    now = time.monotonic()
    last = _analyze_last_at.get(key, 0.0)
    if now - last < ANALYZE_MIN_INTERVAL_SEC:
        await send_to(ctx, error("Rate limited — wait before analyzing this symbol again"))
        return
    _analyze_last_at[key] = now

    force_llm = bool(ctx.message.get("force_llm", False))
    analyst = ctx.chart_analyst
    if analyst is None:
        await send_to(ctx, error("Chart analyst service unavailable"))
        return

    insight = await analyst.analyze(symbol, force_llm=force_llm, broadcast=True)
    if insight is None:
        await send_to(ctx, error("Not enough candle data for analysis"))
        return

    await send_to(ctx, agent_insight(insight.to_dict()))


async def get_agent_insights(symbol: str, ctx: RequestContext, limit: int = 20) -> None:
    """HTTP helper — list recent insights for a symbol."""
    analyst = ctx.chart_analyst
    if analyst is None:
        await send_to(ctx, error("Chart analyst service unavailable"))
        return
    insights = analyst.list_insights(symbol.upper(), limit=limit)
    await send_to(ctx, {"type": "agent_insights_list", "data": insights})
