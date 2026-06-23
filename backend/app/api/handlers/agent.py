"""Chart Analyst agent WebSocket/HTTP handlers."""

from __future__ import annotations

import logging

from app.api.context import RequestContext
from app.api.rate_limit import rate_limit_allow
from app.api.outbound import agent_insight, error
from app.api.protocol import Action
from app.api.responses import send_to
from app.api.router import route
from app.config import AGENT_ENABLED, AGENT_LLM_ENABLED, TERMINAL_MODE
from app.services.agent.llm.router import is_llm_available, summarize_with_critique
from app.observability.json_log import log_event

logger = logging.getLogger(__name__)

ANALYZE_MIN_INTERVAL_SEC = 10.0
DEEP_REASON_MIN_INTERVAL_SEC = 30.0


def _rate_key(ctx: RequestContext, symbol: str) -> str:
    client = str(ctx.message.get("_rate_key") or id(ctx.websocket) or "http")
    return f"{client}:{symbol.upper()}"


def _llm_model_from_message(msg: dict) -> str | None:
    model = (msg.get("llm_model") or msg.get("model") or "").strip()
    return model or None


def _resolve_use_llm(msg: dict, *, default_sim: bool = False) -> bool:
    if not AGENT_LLM_ENABLED:
        return False
    raw = msg.get("use_llm")
    if raw is not None:
        return bool(raw)
    if default_sim and TERMINAL_MODE == "SIMULATED":
        return True
    return False


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
    if not rate_limit_allow(f"analyze:{key}", ANALYZE_MIN_INTERVAL_SEC):
        await send_to(ctx, error("Rate limited — wait before analyzing this symbol again"))
        return

    force_llm = bool(ctx.message.get("force_llm", False))
    llm_model = _llm_model_from_message(ctx.message)
    raw_tf = ctx.message.get("timeframe") or "1m"
    try:
        from app.services.market.timeframes import normalize_timeframe

        timeframe = normalize_timeframe(raw_tf)
    except ValueError:
        await send_to(ctx, error(f"Unsupported timeframe: {raw_tf}"))
        return

    analyst = ctx.chart_analyst
    if analyst is None:
        await send_to(ctx, error("Chart analyst service unavailable"))
        return

    insight = await analyst.analyze(
        symbol,
        force_llm=force_llm,
        llm_model=llm_model,
        timeframe=timeframe,
        broadcast=True,
    )
    if insight is None:
        await send_to(ctx, error("Not enough candle data for analysis"))
        return

    await send_to(ctx, agent_insight(insight.to_dict()))


@route(Action.CHART_DEEP_REASON, tags=["agent"])
async def chart_deep_reason(ctx: RequestContext) -> None:
    """Manual deep reasoning — adds metadata only, never changes signal."""
    if not AGENT_ENABLED:
        await send_to(ctx, error("Chart analyst is disabled"))
        return
    if not AGENT_LLM_ENABLED:
        await send_to(ctx, error("LLM narrator is disabled (AGENT_LLM_ENABLED=false)"))
        return
    if not await is_llm_available():
        await send_to(ctx, error("No LLM provider available — start Ollama or configure OpenRouter"))
        return

    symbol = (ctx.message.get("symbol") or "").upper().strip()
    if not symbol:
        await send_to(ctx, error("symbol is required"))
        return

    key = _rate_key(ctx, symbol)
    if not rate_limit_allow(f"deep_reason:{key}", DEEP_REASON_MIN_INTERVAL_SEC):
        await send_to(ctx, error("Rate limited — wait before deep reasoning again"))
        return

    analyst = ctx.chart_analyst
    if analyst is None:
        await send_to(ctx, error("Chart analyst service unavailable"))
        return

    raw_tf = ctx.message.get("timeframe") or "1m"
    try:
        from app.services.market.timeframes import normalize_timeframe

        timeframe = normalize_timeframe(raw_tf)
    except ValueError:
        await send_to(ctx, error(f"Unsupported timeframe: {raw_tf}"))
        return

    insight_id = (ctx.message.get("insight_id") or "").strip()
    llm_model = _llm_model_from_message(ctx.message)

    insight_dict = None
    if insight_id:
        rows = analyst.list_insights(symbol, limit=50, timeframe=timeframe)
        for row in rows:
            if row.get("insight_id") == insight_id:
                insight_dict = row
                break
    if insight_dict is None:
        cached = analyst.get_cached(symbol, timeframe=timeframe)
        insight_dict = cached

    if not insight_dict:
        fresh = await analyst.analyze(symbol, timeframe=timeframe, broadcast=False, llm_model=llm_model)
        if fresh is None:
            await send_to(ctx, error("Not enough candle data for analysis"))
            return
        insight_dict = fresh.to_dict()

    enrichment = await summarize_with_critique(insight_dict, model=llm_model)
    payload = {
        **insight_dict,
        "deep_reasoning": {
            "reasoning_summary": enrichment.get("reasoning_summary"),
            "risk_notes": enrichment.get("risk_notes"),
            "model": enrichment.get("model"),
            "provider": enrichment.get("provider"),
            "latency_ms": enrichment.get("latency_ms"),
        },
    }
    analyst.persist_deep_reasoning(
        payload.get("insight_id") or insight_id,
        payload["deep_reasoning"],
    )
    await send_to(ctx, {"type": "agent_deep_reason", "data": payload})


@route(Action.EXPLAIN_TRADE, tags=["agent"])
async def explain_trade_handler(ctx: RequestContext) -> None:
    bot_id = (ctx.message.get("bot_id") or "").strip()
    trade_id = (ctx.message.get("trade_id") or "").strip()
    if not bot_id or not trade_id:
        await send_to(ctx, error("bot_id and trade_id are required"))
        return

    from app.services.agent.trade_explain import explain_trade

    use_llm = _resolve_use_llm(ctx.message, default_sim=True)
    if use_llm and not await is_llm_available():
        use_llm = False

    try:
        result = await explain_trade(
            bot_id,
            trade_id,
            chart_analyst=ctx.chart_analyst,
            use_llm=use_llm,
            llm_model=_llm_model_from_message(ctx.message),
        )
    except ValueError as exc:
        await send_to(ctx, error(str(exc)))
        return

    log_event(
        logger,
        "trade_explain",
        bot_id=bot_id,
        action="explain_trade",
        event="trade_explain",
        insight_id=(result.get("insight") or {}).get("insight_id"),
    )
    await send_to(ctx, {"type": "trade_explain", "data": result})


async def get_agent_insights(symbol: str, ctx: RequestContext, limit: int = 20) -> None:
    """HTTP helper — list recent insights for a symbol."""
    analyst = ctx.chart_analyst
    if analyst is None:
        await send_to(ctx, error("Chart analyst service unavailable"))
        return
    insights = analyst.list_insights(symbol.upper(), limit=limit)
    await send_to(ctx, {"type": "agent_insights_list", "data": insights})
