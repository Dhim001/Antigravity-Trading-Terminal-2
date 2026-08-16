"""Tool implementations shared by Copilot and external (HTTP) callers.

The ``tool_*`` functions are the plain implementations (previously the private
``_tool_*`` functions on the Copilot module — Copilot keeps aliases for
back-compat). The ``*_handler`` callables adapt them to the registry's
``(args, ToolContext)`` convention, preserving Copilot's argument enrichment
(message symbol extraction, session timeframe memory, bot-id resolution).
For HITL-gated tools the ``*_plan`` callables build the pending action that
the Copilot confirm flow executes; the ``*_handler`` callables perform the
actual mutation (same bodies as the old ``confirm_action`` branches).
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import RISK_MAX_DRAWDOWN_PCT, TRADE_COPILOT_USE_LLM
from app.services.altdata.store import get_aggregate_sentiment
from app.services.analytics.portfolio import get_bot_rankings, get_risk_utilization
from app.services.bots import analytics as bot_analytics
from app.services.bots.portfolio_risk import build_portfolio_snapshot
from app.services.market.timeframes import normalize_timeframe

from app.services.agent.tools.registry import ToolContext

logger = logging.getLogger(__name__)

_DEFAULT_ANALYZE_TF = "1m"
_ADX_TREND_THRESHOLD = 25


def _safe_tf(raw: Any, default: str = _DEFAULT_ANALYZE_TF) -> str:
    try:
        return normalize_timeframe(str(raw or default))
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Plain tool implementations
# ---------------------------------------------------------------------------


def _snapshot_dict(oms: Any) -> dict[str, Any]:
    snap = build_portfolio_snapshot(oms)
    return {
        "account_equity": snap.account_equity,
        "gross_exposure": snap.gross_exposure,
        "group_exposure": snap.group_exposure,
        "symbol_exposure": snap.symbol_exposure,
    }


def tool_list_bots(bot_manager: Any) -> dict[str, Any]:
    bots = []
    for bot in (bot_manager.list_bots_public() if hasattr(bot_manager, "list_bots_public") else []):
        bots.append({
            "id": bot.get("id"),
            "symbol": bot.get("symbol"),
            "strategy": bot.get("strategy"),
            "status": bot.get("status"),
            "allocation": bot.get("allocation"),
            "timeframe": bot.get("timeframe"),
            "total_pnl": bot.get("total_pnl"),
        })
    return {"bots": bots, "count": len(bots)}


def tool_bot_performance(bot_manager: Any, bot_id: str | None = None) -> dict[str, Any]:
    if bot_id:
        stats = bot_analytics.get_bot_stats(bot_id)
        return {"bot_id": bot_id, "stats": stats}
    rankings = get_bot_rankings(limit=10)
    active = tool_list_bots(bot_manager)
    return {"rankings": rankings, "active_bots": active}


async def tool_scan_market(bot_manager: Any, limit: int = 5) -> dict[str, Any]:
    if not bot_manager:
        return {"error": "Bot manager unavailable."}
    from app.config import SCANNER_DEPLOY_WATCHLIST
    from app.services.scanner.market_scanner import MarketScannerService
    scanner = MarketScannerService(bot_manager.oms.feed if hasattr(bot_manager, "oms") else None)
    scan_res = await scanner.scan(SCANNER_DEPLOY_WATCHLIST, signal_filter="any")

    rows = scan_res.get("rows", [])
    # Sort by confidence then score
    sorted_assets = sorted(
        rows,
        key=lambda x: (x.get("confidence", 0.0), abs(x.get("score", 0))),
        reverse=True
    )

    top_assets = []
    for asset in sorted_assets[:limit]:
        top_assets.append({
            "symbol": asset.get("symbol"),
            "signal": asset.get("signal"),
            "score": asset.get("score"),
            "confidence": asset.get("confidence"),
            "regime": asset.get("atr_regime") or "unknown",
            "close": None, # Price isn't in rows, but keeping structure
        })

    return {
        "scanned_count": len(rows),
        "watchlist_size": len(SCANNER_DEPLOY_WATCHLIST),
        "top_movers": top_assets,
    }


def tool_portfolio(oms: Any) -> dict[str, Any]:
    body = _snapshot_dict(oms)
    try:
        body["risk_utilization"] = get_risk_utilization(oms)
    except Exception as exc:
        body["risk_utilization_error"] = str(exc)
    body["risk_max_drawdown_pct_limit"] = RISK_MAX_DRAWDOWN_PCT
    return body


def tool_sentiment(symbol: str) -> dict[str, Any]:
    return get_aggregate_sentiment(symbol, lookback_hours=24.0)


def extract_regime_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Pull ADX trend regime + scoring/vol context from chart insight payloads.

    ChartAgentInsight stores regime under sub_reports (not top-level):
    - trend.trend_regime → 'trending' | 'ranging' | 'unknown' (ADX > 25)
    - regime_weights.regime → scoring bucket (may be elevated_vol / compressed)
    - risk.atr_regime → volatility bucket
    """
    sub = data.get("sub_reports") if isinstance(data.get("sub_reports"), dict) else {}
    trend = sub.get("trend") if isinstance(sub.get("trend"), dict) else {}
    weights = sub.get("regime_weights") if isinstance(sub.get("regime_weights"), dict) else {}
    risk = sub.get("risk") if isinstance(sub.get("risk"), dict) else {}

    def _norm(val: Any) -> str | None:
        if val is None:
            return None
        s = str(val).strip().lower()
        return s or None

    trend_regime = _norm(
        data.get("trend_regime") or trend.get("trend_regime")
    )
    scoring = _norm(
        data.get("regime")
        or weights.get("regime")
        or sub.get("regime")
        or trend_regime
    )
    atr_regime = _norm(risk.get("atr_regime") or data.get("atr_regime"))

    # Prefer ADX trend label for "trending vs ranging" answers.
    market = trend_regime
    if market in (None, "unknown") and scoring in ("trending", "ranging"):
        market = scoring

    return {
        "regime": scoring,
        "trend_regime": trend_regime,
        "market_regime": market,
        "atr_regime": atr_regime,
    }


async def tool_analyze(
    state: Any,
    symbol: str,
    timeframe: str = _DEFAULT_ANALYZE_TF,
) -> dict[str, Any]:
    analyst = getattr(state, "chart_analyst", None)
    if analyst is None or not hasattr(analyst, "analyze"):
        return {"error": "Chart analyst unavailable", "symbol": symbol}
    tf = timeframe or _DEFAULT_ANALYZE_TF
    insight = await analyst.analyze(symbol, timeframe=tf, broadcast=False, force_llm=False)
    if insight is None:
        return {"error": "No insight produced (need more bars or agent disabled)", "symbol": symbol}
    if hasattr(insight, "to_dict"):
        data = insight.to_dict()
    elif isinstance(insight, dict):
        data = insight
    else:
        data = {"raw": str(insight)}
    regime = extract_regime_fields(data if isinstance(data, dict) else {})
    thr = _ADX_TREND_THRESHOLD
    out: dict[str, Any] = {
        "symbol": symbol,
        "signal": data.get("signal"),
        "score": data.get("score"),
        "confidence": data.get("confidence"),
        "regime": regime.get("regime"),
        "trend_regime": regime.get("trend_regime"),
        "market_regime": regime.get("market_regime"),
        "atr_regime": regime.get("atr_regime"),
        "reasons": (data.get("reasons") or [])[:5],
        "timeframe": tf,
        "adx_threshold": thr,
        "bar": "closed",
        "method": f"ADX > {thr} → trending, else ranging",
    }
    return out


async def tool_recommend_strategy(
    state: Any,
    message: str,
    *,
    active_symbol: str | None = None,
) -> dict[str, Any]:
    """Pick a strategy from stated/live regime — do not invent a deploy confirm."""
    from app.services.agent import copilot as _copilot

    sym = _copilot.normalize_symbol(_copilot.extract_symbol(message, active_symbol))
    text_regime = _copilot.extract_regime_from_text(message)
    live_regime = None
    analysis: dict[str, Any] | None = None
    if sym:
        from app.services.agent.copilot_agent import extract_timeframe_hint

        tf = extract_timeframe_hint(message) or _DEFAULT_ANALYZE_TF
        analysis = await tool_analyze(state, sym, timeframe=tf)
        if not analysis.get("error"):
            live_regime = (
                analysis.get("market_regime")
                or analysis.get("trend_regime")
                or analysis.get("regime")
            )
            atr = analysis.get("atr_regime")
            if atr == "elevated":
                live_regime = "elevated_vol"
            elif atr == "compressed" and live_regime != "trending":
                live_regime = "compressed"

    # Prefer explicit user wording ("still ranging") over a conflicting live read.
    regime = text_regime or live_regime or "ranging"
    rec = _copilot.recommend_strategy_for_regime(regime)
    alloc = _copilot.extract_allocation(message)
    primary = rec["primary"]
    out: dict[str, Any] = {
        "symbol": sym,
        "regime": regime,
        "regime_source": "user_text" if text_regime else ("live_analysis" if live_regime else "default"),
        "primary": primary,
        "alternatives": rec["alternatives"],
        "avoid": rec["avoid"],
        "allocation": alloc,
        "deploy_example": (
            f"Deploy {primary} on {sym} with ${alloc:.0f}"
            if sym
            else f"Deploy {primary} on <SYMBOL> with ${alloc:.0f}"
        ),
    }
    if analysis and not analysis.get("error"):
        out["live_signal"] = analysis.get("signal")
        out["live_market_regime"] = analysis.get("market_regime")
        # Stash for meta follow-ups ("what timeframe…") even when path was recommend.
        out["_insight"] = analysis
    elif analysis and analysis.get("error"):
        out["analysis_note"] = analysis.get("error")
    return out


async def tool_run_backtest(
    state: Any,
    symbol: str,
    strategy: str,
    days: int,
    *,
    timeframe: str = "1m",
    allocation: float = 1000.0,
) -> dict[str, Any]:
    """Run a backtest for chat.

    Short horizons run inline. Longer / heavy runs are queued on the existing
    backtest job worker so the Copilot HTTP request does not time out.
    """
    import asyncio

    from app.services.bots.backtest_perf import backtest_tier_meta

    days = max(1, min(365, int(days)))
    config = {
        "allocation": float(allocation),
        "pipeline_source": "copilot",
    }
    req = {
        "symbol": symbol,
        "strategy": strategy,
        "config": config,
        "days": days,
        "timeframe": timeframe,
        "allocation": float(allocation),
    }
    tier_meta = backtest_tier_meta(req)
    # Chat sync budget is tighter than the Algo panel — queue anything likely >~2 min.
    queue_async = tier_meta.get("tier") == "deferred" or days > 10

    if queue_async:
        from app.api.context import RequestContext
        from app.api.handlers.bots import _execute_backtest
        from app.services.bots.backtest_job_store import (
            create_backtest_job,
            set_job_status,
            update_job_progress,
        )
        from app.services.bots.backtest_jobs import start_job

        job_req = {
            **req,
            "tier": "deferred",
            "estimated_sec": tier_meta.get("estimated_sec"),
            "source": "copilot",
        }
        job_id = create_backtest_job(job_req, status="pending", client_key="copilot")
        start_job(None, job_id)
        update_job_progress(job_id, {
            "pct": 0,
            "phase": "queued",
            "message": (
                f"Queued {days}d {strategy} backtest on {symbol} "
                f"(~{tier_meta.get('estimated_sec')}s est.)…"
            ),
            "job_id": job_id,
            "tier": "deferred",
            "estimated_sec": tier_meta.get("estimated_sec"),
        })

        ctx = RequestContext(
            websocket=None,
            manager=getattr(state, "manager", None),
            oms=getattr(state, "oms", None),
            bot_manager=getattr(state, "bot_manager", None),
            backtester=getattr(state, "backtester", None),
            chart_analyst=getattr(state, "chart_analyst", None),
            message=job_req,
            action="run_backtest",
        )

        async def _run_queued() -> None:
            try:
                await _execute_backtest(
                    ctx,
                    job_id=job_id,
                    symbol=symbol,
                    strategy=strategy,
                    config=config,
                    days=days,
                    interval=None,
                    timeframe=timeframe,
                )
            except Exception as exc:
                logger.exception("Copilot queued backtest %s failed", job_id)
                set_job_status(job_id, "failed", error=str(exc) or "Backtest failed")

        asyncio.create_task(_run_queued())
        return {
            "queued": True,
            "job_id": job_id,
            "symbol": symbol,
            "strategy": strategy,
            "days": days,
            "timeframe": timeframe,
            "estimated_sec": tier_meta.get("estimated_sec"),
            "message": (
                f"Queued {days}d backtest — track job `{job_id}` in the Algo / Backtest panel."
            ),
        }

    from app.services.archive.resolve import resolve_backtest_candles

    feed = getattr(getattr(state, "oms", None), "feed", None) or getattr(state, "feed", None)
    bt = getattr(state, "backtester", None)

    try:
        candles, meta = await asyncio.to_thread(
            resolve_backtest_candles,
            symbol,
            feed,
            days=days,
            timeframe=timeframe,
        )
    except Exception as exc:
        return {
            "error": f"Failed to resolve history: {exc}",
            "symbol": symbol,
            "strategy": strategy,
            "days": days,
        }

    bar_count = len(candles or [])
    replayed = float((meta or {}).get("replayed_days") or 0.0)
    if not candles or bar_count < 50:
        note = (meta or {}).get("range_note") or (meta or {}).get("resolution_note") or ""
        return {
            "error": (
                f"Not enough history for {days}d {timeframe} backtest "
                f"(got {bar_count} bars ≈{replayed:.1f}d). {note}"
            ).strip(),
            "symbol": symbol,
            "strategy": strategy,
            "days": days,
            "bar_count": bar_count,
            "meta": meta,
        }

    if bt is None or not hasattr(bt, "run_backtest"):
        from app.services.bots.backtester import BacktesterService

        bt = BacktesterService()

    try:
        result = await asyncio.to_thread(
            bt.run_backtest,
            symbol,
            strategy,
            config,
            candles,
        )
    except Exception as exc:
        return {
            "error": f"Backtest failed: {exc}",
            "symbol": symbol,
            "strategy": strategy,
            "days": days,
        }

    if not isinstance(result, dict):
        return {"error": "Invalid backtest result", "symbol": symbol, "strategy": strategy, "days": days}
    if result.get("error"):
        return {
            "error": result["error"],
            "symbol": symbol,
            "strategy": strategy,
            "days": days,
            "bar_count": bar_count,
        }

    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    return {
        "symbol": symbol,
        "strategy": strategy,
        "days": days,
        "timeframe": timeframe,
        "bar_count": bar_count,
        "replayed_days": replayed or (meta or {}).get("replayed_days"),
        "win_rate": result.get("win_rate") if result.get("win_rate") is not None else summary.get("win_rate"),
        "total_pnl": result.get("total_pnl") if result.get("total_pnl") is not None else summary.get("total_pnl"),
        "max_drawdown": result.get("max_drawdown") if result.get("max_drawdown") is not None else summary.get("max_drawdown"),
        "trade_count": result.get("trade_count") if result.get("trade_count") is not None else summary.get("total_trades"),
        "return_pct": summary.get("return_pct"),
        "sharpe_ratio": summary.get("sharpe_ratio"),
        "starting_equity": result.get("starting_equity") or summary.get("starting_equity"),
        "range_note": (meta or {}).get("range_note") or (meta or {}).get("resolution_note"),
    }


async def tool_explain(state: Any, bot_id: str, trade_id: str | None = None) -> dict[str, Any]:
    from app.services.agent.trade_explain import explain_trade
    from app.database import get_connection

    if not bot_id:
        return {"error": "bot_id required"}
    tid = trade_id
    if not tid:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id FROM bot_trades
                WHERE bot_id = ? AND is_exit = 1
                ORDER BY timestamp DESC LIMIT 1
                """,
                (bot_id,),
            )
            row = cur.fetchone()
            if row:
                tid = str(row["id"] if isinstance(row, dict) else row[0])
        finally:
            conn.close()
    if not tid:
        return {"error": "No exit trades found for this bot"}
    try:
        result = await explain_trade(
            bot_id,
            str(tid),
            chart_analyst=getattr(state, "chart_analyst", None),
            use_llm=TRADE_COPILOT_USE_LLM,
        )
        return {
            "bot_id": bot_id,
            "trade_id": tid,
            "narrative": result.get("narrative") or result.get("summary"),
            "insight": result.get("insight"),
            "trade": {
                k: result.get("trade", {}).get(k)
                for k in ("symbol", "side", "price", "pnl", "is_exit")
                if isinstance(result.get("trade"), dict)
            },
        }
    except Exception as exc:
        return {"error": str(exc)}


async def tool_explain_bot_events(bot_id: str, limit: int = 5) -> dict[str, Any]:
    from app.database import get_connection
    import json

    conn = get_connection()
    try:
        cur = conn.cursor()
        if bot_id:
            cur.execute(
                "SELECT level, message, meta, timestamp FROM bot_logs WHERE bot_id = ? AND level IN ('WARN', 'ERROR', 'INFO') ORDER BY timestamp DESC LIMIT ?",
                (bot_id, limit)
            )
        else:
            cur.execute(
                "SELECT bot_id, level, message, meta, timestamp FROM bot_logs WHERE level IN ('WARN', 'ERROR') ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            )
        rows = cur.fetchall()
        events = []
        for r in rows:
            event = dict(r)
            meta_json = event.get("meta")
            if meta_json:
                try:
                    event["meta"] = json.loads(meta_json)
                except Exception:
                    event["meta"] = None
            events.append(event)

        return {
            "bot_id": bot_id,
            "events": events
        }
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def tool_meta_insight(
    session_id: str,
    message: str,
    *,
    active_symbol: str | None = None,
) -> dict[str, Any]:
    from app.services.agent import copilot as _copilot

    field = _copilot.detect_meta_field(message) or "timeframe"
    sym = _copilot.normalize_symbol(_copilot.extract_symbol(message, active_symbol))
    insight = _copilot.get_last_insight(session_id, sym)
    if insight is None and sym is None:
        insight = _copilot.get_last_insight(session_id, None)
    if insight and not sym:
        sym = _copilot.normalize_symbol(str(insight.get("symbol") or "")) or None
    prov = _copilot._provenance_bits(insight)
    out: dict[str, Any] = {
        "field": field,
        "symbol": sym,
        "found_prior": bool(insight),
        **prov,
    }
    if insight:
        out["market_regime"] = insight.get("market_regime") or insight.get("trend_regime")
        out["signal"] = insight.get("signal")
        out["score"] = insight.get("score")
        out["confidence"] = insight.get("confidence")
    elif sym:
        out["note"] = (
            f"No prior analysis for {sym} in this session — defaults below apply. "
            f"Ask *what market is {sym} in?* to refresh."
        )
    else:
        out["note"] = "No prior analysis in this session — showing Copilot defaults."
    return out


# ---------------------------------------------------------------------------
# Registry adapters — (args, ToolContext) wrappers with Copilot arg enrichment
# ---------------------------------------------------------------------------


async def analyze_symbol_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.services.agent import copilot as _copilot

    message = ctx.message or ""
    sym = _copilot.normalize_symbol(
        args.get("symbol") or _copilot.extract_symbol(message, ctx.active_symbol)
    )
    preferred = _copilot.get_preferred_timeframe(ctx.session_id)
    tf = _safe_tf(args.get("timeframe") or preferred, preferred)
    _copilot.remember_timeframe(ctx.session_id, tf)
    if not sym:
        # Reuse last insight symbol on TF-only follow-ups
        last = _copilot.get_last_insight(ctx.session_id)
        sym = _copilot.normalize_symbol((last or {}).get("symbol")) if last else None
    if not sym:
        return {"error": "Specify a symbol"}
    analysis = await tool_analyze(ctx.state, sym, timeframe=tf)
    _copilot.remember_insight(ctx.session_id, analysis)
    return analysis


def meta_insight_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    message = ctx.message or ""
    # Inject field into a synthetic message if the caller provided it
    field = args.get("field")
    if field and field not in message.lower():
        message = f"{field}: {message}"
    return tool_meta_insight(ctx.session_id, message, active_symbol=ctx.active_symbol)


async def recommend_strategy_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.services.agent import copilot as _copilot

    message = ctx.message or ""
    if not message.strip():
        # Direct (non-chat) callers pass regime/symbol as args.
        bits = [str(args.get("regime") or ""), str(args.get("symbol") or "")]
        message = " ".join(b for b in bits if b).strip()
    rec = await tool_recommend_strategy(ctx.state, message, active_symbol=ctx.active_symbol)
    insight = rec.pop("_insight", None)
    if isinstance(insight, dict):
        _copilot.remember_insight(ctx.session_id, insight)
    return rec


async def scan_market_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    limit = int(args.get("limit") or 5)
    return await tool_scan_market(ctx.bot_manager, limit=limit)


def portfolio_status_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return tool_portfolio(ctx.oms)


def list_bots_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return tool_list_bots(ctx.bot_manager)


def bot_performance_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.services.agent import copilot as _copilot

    bid = args.get("bot_id")
    if not bid:
        m = _copilot._BOT_ID_RE.search(ctx.message or "")
        bid = m.group(1) if m else None
    return tool_bot_performance(ctx.bot_manager, bid)


def sentiment_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.services.agent import copilot as _copilot

    sym = _copilot.normalize_symbol(
        args.get("symbol") or _copilot.extract_symbol(ctx.message or "", ctx.active_symbol)
    ) or "AAPL"
    return tool_sentiment(sym)


async def run_backtest_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.services.agent import copilot as _copilot

    message = ctx.message or ""
    sym = _copilot.normalize_symbol(
        args.get("symbol") or _copilot.extract_symbol(message, ctx.active_symbol)
    )
    if not sym:
        return {"error": "Specify a symbol"}
    preferred = _copilot.get_preferred_timeframe(ctx.session_id)
    strategy = args.get("strategy") or _copilot.extract_strategy(message)
    days = int(args.get("days") or _copilot.extract_days(message, default=30))
    tf = _safe_tf(args.get("timeframe") or preferred, preferred)
    alloc = float(args.get("allocation") or _copilot.extract_allocation(message))
    return await tool_run_backtest(
        ctx.state, sym, strategy, days, timeframe=tf, allocation=alloc
    )


def _resolve_bot_id(args: dict[str, Any], ctx: ToolContext, *, pick_any_active: bool = False) -> str | None:
    from app.services.agent import copilot as _copilot

    bid = args.get("bot_id")
    if not bid:
        m = _copilot._BOT_ID_RE.search(ctx.message or "")
        bid = m.group(1) if m else None
    bot_manager = ctx.bot_manager
    if not bid and bot_manager and getattr(bot_manager, "active_bots", None):
        sym = _copilot.normalize_symbol(
            args.get("symbol") or _copilot.extract_symbol(ctx.message or "", ctx.active_symbol)
        )
        for b in bot_manager.active_bots.values():
            if sym and str(b.get("symbol") or "").upper() == sym:
                bid = b.get("id")
                break
        if not bid and pick_any_active:
            bid = next(iter(bot_manager.active_bots))
    return bid


async def explain_trade_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    bid = _resolve_bot_id(args, ctx, pick_any_active=True)
    return await tool_explain(ctx.state, bid or "")


async def explain_bot_events_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    bid = _resolve_bot_id(args, ctx, pick_any_active=True)
    return await tool_explain_bot_events(bid or "", limit=args.get("limit") or 5)


def help_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.services.agent import copilot as _copilot

    return _copilot._help_text()


# ---------------------------------------------------------------------------
# HITL-gated tools — plan (pending preview) + execute (confirmed mutation)
# ---------------------------------------------------------------------------


def deploy_bot_plan(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.services.agent import copilot as _copilot

    message = ctx.message or ""
    sym = _copilot.normalize_symbol(
        args.get("symbol") or _copilot.extract_symbol(message, ctx.active_symbol)
    )
    if not sym:
        return {"error": "Specify a symbol to deploy"}
    preferred = _copilot.get_preferred_timeframe(ctx.session_id)
    tf = _safe_tf(args.get("timeframe") or preferred, preferred)
    return {
        "type": "deploy_bot",
        "params": {
            "strategy": args.get("strategy") or _copilot.extract_strategy(message) or "CHART_AGENT",
            "symbol": sym,
            "timeframe": tf,
            "allocation": float(args.get("allocation") or _copilot.extract_allocation(message)),
            "config": {"pipeline_source": "copilot"},
        },
    }


def pause_bot_plan(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    bid = _resolve_bot_id(args, ctx)
    if not bid:
        return {"error": "Specify a bot id or symbol"}
    return {"type": "pause_bot", "params": {"bot_id": bid}}


def stop_bot_plan(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    bid = _resolve_bot_id(args, ctx)
    if not bid:
        return {"error": "Specify a bot id or symbol"}
    return {"type": "stop_bot", "params": {"bot_id": bid}}


def pause_all_bots_plan(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return {"type": "pause_all_bots", "params": {}}


def stop_all_bots_plan(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return {"type": "stop_all_bots", "params": {}}


def update_bot_config_plan(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.services.agent import copilot as _copilot

    message = ctx.message or ""
    bid = args.get("bot_id")
    patch = dict(args.get("config_patch")) if isinstance(args.get("config_patch"), dict) else {}
    if not bid:
        m = _copilot._BOT_ID_RE.search(message)
        bid = m.group(1) if m else None
    if not patch:
        pct_m = _copilot._PCT_RE.search(message)
        if pct_m and ("stop" in message.lower() or "sl" in message.lower()):
            patch["stop_loss_percent"] = float(pct_m.group(1))
        if "confidence" in message.lower() and pct_m:
            val = float(pct_m.group(1))
            patch["min_confidence"] = val / 100.0 if val > 1 else val
    bot_manager = ctx.bot_manager
    if not bid and bot_manager and getattr(bot_manager, "active_bots", None):
        sym = _copilot.normalize_symbol(
            args.get("symbol") or _copilot.extract_symbol(message, ctx.active_symbol)
        )
        for b in bot_manager.active_bots.values():
            if sym and str(b.get("symbol") or "").upper() == sym:
                bid = b.get("id")
                break
    if not bid or not patch:
        return {"error": "Need bot id/symbol and a config change"}
    return {"type": "update_bot_config", "params": {"bot_id": bid, "config_patch": patch}}


async def deploy_bot_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    bot_manager = ctx.bot_manager
    if bot_manager is None:
        raise RuntimeError("Bot manager unavailable")
    bot_id = await bot_manager.create_bot(
        args.get("strategy") or "CHART_AGENT",
        args["symbol"],
        args.get("timeframe") or "1m",
        float(args.get("allocation") or 1000),
        args.get("config") or {"pipeline_source": "copilot"},
    )
    return {"bot_id": bot_id, "action": "deploy_bot"}


async def pause_bot_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    bot_manager = ctx.bot_manager
    if bot_manager is None:
        raise RuntimeError("Bot manager unavailable")
    await bot_manager.pause_bot(args["bot_id"])
    return {"bot_id": args["bot_id"], "action": "pause_bot"}


async def stop_bot_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    bot_manager = ctx.bot_manager
    if bot_manager is None:
        raise RuntimeError("Bot manager unavailable")
    await bot_manager.stop_bot(args["bot_id"])
    return {"bot_id": args["bot_id"], "action": "stop_bot"}


async def pause_all_bots_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    bot_manager = ctx.bot_manager
    if bot_manager is None:
        raise RuntimeError("Bot manager unavailable")
    paused = 0
    for bot_id, bot in list(bot_manager.active_bots.items()):
        if bot.get("status") == "RUNNING":
            await bot_manager.pause_bot(bot_id)
            paused += 1
    return {"paused": paused, "action": "pause_all_bots"}


async def stop_all_bots_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    bot_manager = ctx.bot_manager
    if bot_manager is None:
        raise RuntimeError("Bot manager unavailable")
    await bot_manager.stop_all_bots()
    return {"action": "stop_all_bots"}


async def update_bot_config_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    bot_manager = ctx.bot_manager
    if bot_manager is None:
        raise RuntimeError("Bot manager unavailable")
    detail = await bot_manager.update_bot_config(
        args["bot_id"],
        args.get("config_patch") or {},
    )
    return {"action": "update_bot_config", "detail": bool(detail)}
