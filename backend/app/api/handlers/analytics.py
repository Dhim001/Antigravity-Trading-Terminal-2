"""Analytics WebSocket handler."""

from __future__ import annotations

from app.api.context import RequestContext
from app.api.outbound import error
from app.api.protocol import Action, MessageType
from app.api.responses import send_to
from app.api.router import route
from app.services.analytics.benchmarks import get_benchmarks
from app.services.analytics.portfolio import (
    get_allocation,
    get_bot_rankings,
    get_breakdown_stats,
    get_correlation_matrix,
    get_daily_pnl_calendar,
    get_portfolio_equity_curve,
    get_risk_utilization,
)

_VALID_REPORTS = frozenset({
    "equity", "breakdown", "calendar", "allocation",
    "correlation", "bot_rankings", "risk", "benchmarks", "dashboard",
})
_VALID_SOURCES = frozenset({"bot", "account", "combined"})
_VALID_GROUPS = frozenset({"strategy", "symbol", "timeframe"})


def _source(msg: dict) -> str:
    src = (msg.get("source") or "combined").lower()
    return src if src in _VALID_SOURCES else "combined"


def _parse_symbols(msg: dict) -> list[str] | None:
    raw = msg.get("symbols")
    if raw is None:
        return None
    if isinstance(raw, str):
        parts = [s.strip().upper() for s in raw.split(",") if s.strip()]
    elif isinstance(raw, list):
        parts = [str(s).strip().upper() for s in raw if str(s).strip()]
    else:
        return None
    return parts or None


@route(Action.ANALYTICS_GET, tags=["analytics"])
async def analytics_get(ctx: RequestContext) -> None:
    report = (ctx.message.get("report") or "dashboard").lower()
    if report not in _VALID_REPORTS:
        await send_to(ctx, error(f"Unknown analytics report: {report}"))
        return

    try:
        account_history = ctx.oms.get_trade_history()
        period = ctx.message.get("period")
        source = _source(ctx.message)
        symbol_universe = _parse_symbols(ctx.message)

        data: dict = {"report": report, "source": source}

        if report in ("equity", "dashboard"):
            data["equity"] = get_portfolio_equity_curve(
                account_history, period=period, source=source,
            )
        if report in ("breakdown", "dashboard"):
            group_by = (ctx.message.get("group_by") or "strategy").lower()
            if group_by not in _VALID_GROUPS:
                group_by = "strategy"
            data["breakdown"] = get_breakdown_stats(
                account_history, group_by, period=period, source=source,
            )
        if report in ("calendar", "dashboard"):
            data["calendar"] = get_daily_pnl_calendar(
                account_history,
                start=ctx.message.get("start"),
                end=ctx.message.get("end"),
                source=source,
            )
        if report in ("allocation", "dashboard"):
            data["allocation"] = get_allocation(ctx.oms)
        if report in ("correlation", "dashboard"):
            data["correlation"] = get_correlation_matrix(
                account_history,
                period=period or "1M",
                source=source,
                symbols=symbol_universe,
            )
        if report in ("bot_rankings", "dashboard"):
            limit = int(ctx.message.get("limit") or 10)
            data["bot_rankings"] = get_bot_rankings(limit=limit)
        if report in ("risk", "dashboard"):
            data["risk"] = get_risk_utilization(ctx.oms)
        if report == "benchmarks":
            symbols = ctx.message.get("symbols")
            if isinstance(symbols, str):
                symbols = [s.strip() for s in symbols.split(",") if s.strip()]
            if not symbols:
                symbols = ["SPY", "BTC"]
            feed = getattr(ctx.oms, "feed", None)
            data["benchmarks"] = get_benchmarks(
                symbols, period=period or "3mo", feed=feed,
            ).get("benchmarks", {})
    except Exception as exc:
        await send_to(ctx, error(f"Analytics failed: {exc}"))
        return

    await send_to(ctx, {"type": MessageType.ANALYTICS_REPORT, "data": data})
