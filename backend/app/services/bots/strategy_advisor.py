"""LLM strategy parameter advisor — suggests config patches from backtest context."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import STRATEGY_ADVISOR_DEFAULT_DAYS, STRATEGY_ADVISOR_ENABLED
from app.services.agent.llm.base import STRATEGY_ADVISOR_JSON_SYSTEM_PROMPT, parse_json_object
from app.services.agent.llm.payloads import dumps_payload
from app.services.agent.llm.router import _chat, _pick_provider, is_llm_available, resolve_model
from app.services.bots.backtest_store import _summary_from_results, list_backtest_runs
from app.services.bots.indicators import merge_strategy_config

logger = logging.getLogger(__name__)

ADVISABLE_PARAMS: frozenset[str] = frozenset({
    "min_confidence",
    "min_score",
    "stop_loss_percent",
    "take_profit_percent",
    "trailing_stop_percent",
    "require_trend_alignment",
    "block_elevated_vol",
    "block_ranging_markets",
    "sentiment_filter_enabled",
    "min_sentiment_score",
    "confirm_timeframe",
    "fee_bps",
    "slippage_bps",
})

PARAM_BOUNDS: dict[str, tuple[float, float] | tuple[int, int]] = {
    "min_confidence": (0.5, 0.95),
    "min_score": (1, 5),
    "stop_loss_percent": (0.1, 15.0),
    "take_profit_percent": (0.1, 30.0),
    "trailing_stop_percent": (0.1, 15.0),
    "min_sentiment_score": (-1.0, 1.0),
    "fee_bps": (0.0, 50.0),
    "slippage_bps": (0.0, 100.0),
}

BOOL_PARAMS = frozenset({
    "require_trend_alignment",
    "block_elevated_vol",
    "block_ranging_markets",
    "sentiment_filter_enabled",
})


def _load_bot(bot_id: str) -> dict[str, Any] | None:
    from app.db.connection import get_connection

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, strategy, symbol, timeframe, status, allocation, config FROM bots WHERE id = ?",
        (bot_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    if isinstance(row, dict):
        bot = dict(row)
    else:
        bot = {
            "id": row[0],
            "strategy": row[1],
            "symbol": row[2],
            "timeframe": row[3],
            "status": row[4],
            "allocation": row[5],
            "config": row[6],
        }
    cfg = bot.get("config")
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except json.JSONDecodeError:
            cfg = {}
    bot["config"] = cfg or {}
    return bot


def build_advisor_context(
    bot: dict[str, Any],
    *,
    recent_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.services.altdata.store import get_aggregate_sentiment
    from app.services.bots.calibration import get_filter_reject_dashboard

    symbol = str(bot.get("symbol") or "").upper()
    strategy = str(bot.get("strategy") or "")
    config = merge_strategy_config(strategy, bot.get("config") or {})

    runs = list_backtest_runs(limit=5, symbol=symbol)
    run_summaries = []
    for run in runs[:5]:
        run_summaries.append({
            "run_id": run.get("id"),
            "created_at": run.get("created_at"),
            "days": run.get("days"),
            "summary": run.get("summary") or _summary_from_results(run.get("results") or {}),
        })

    active_summary = None
    if recent_run:
        active_summary = recent_run.get("summary") or _summary_from_results(recent_run.get("results") or {})

    filter_rejects = get_filter_reject_dashboard(symbol=symbol, strategy=strategy)

    return {
        "bot_id": bot.get("id"),
        "symbol": symbol,
        "strategy": strategy,
        "timeframe": bot.get("timeframe"),
        "status": bot.get("status"),
        "current_config": config,
        "allowable_params": sorted(ADVISABLE_PARAMS),
        "param_bounds": {k: list(v) for k, v in PARAM_BOUNDS.items()},
        "recent_backtests": run_summaries,
        "active_backtest_summary": active_summary,
        "sentiment": get_aggregate_sentiment(symbol),
        "filter_rejects": filter_rejects,
    }


def validate_suggested_params(
    strategy: str,
    patch: dict[str, Any],
    *,
    base_config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Clamp and filter LLM/heuristic suggestions to safe bounds."""
    merged_base = merge_strategy_config(strategy, base_config or {})
    clean: dict[str, Any] = {}
    warnings: list[str] = []

    for key, raw in (patch or {}).items():
        if key not in ADVISABLE_PARAMS:
            warnings.append(f"dropped unknown param: {key}")
            continue
        if key in BOOL_PARAMS:
            clean[key] = bool(raw)
            continue
        if key == "confirm_timeframe":
            tf = str(raw or "").strip()
            if tf:
                clean[key] = tf
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            warnings.append(f"dropped non-numeric {key}")
            continue
        bounds = PARAM_BOUNDS.get(key)
        if bounds:
            lo, hi = bounds
            if val < lo or val > hi:
                val = max(lo, min(hi, val))
                warnings.append(f"clamped {key} to {val}")
        if key == "min_score":
            clean[key] = int(round(val))
        elif key in ("fee_bps", "slippage_bps"):
            clean[key] = int(round(val))
        else:
            clean[key] = round(val, 4)

    if not clean:
        return {}, warnings

    # Avoid no-op suggestions
    for key, val in list(clean.items()):
        if merged_base.get(key) == val:
            del clean[key]
            warnings.append(f"skipped unchanged {key}")

    return clean, warnings


def _heuristic_suggestion(context: dict[str, Any]) -> dict[str, Any]:
    summary = context.get("active_backtest_summary") or {}
    if not summary and context.get("recent_backtests"):
        summary = (context["recent_backtests"][0] or {}).get("summary") or {}

    patch: dict[str, Any] = {}
    rationale_parts: list[str] = []

    try:
        win_rate = float(summary.get("win_rate") or 0)
    except (TypeError, ValueError):
        win_rate = 0.0
    try:
        max_dd = float(summary.get("max_drawdown") or 0)
    except (TypeError, ValueError):
        max_dd = 0.0
    try:
        blocked = int(summary.get("blocked_entries") or 0)
    except (TypeError, ValueError):
        blocked = 0

    cfg = context.get("current_config") or {}
    min_conf = float(cfg.get("min_confidence", 0.55))

    if win_rate < 0.4 and max_dd > 10:
        patch["min_confidence"] = min(0.85, round(min_conf + 0.05, 2))
        rationale_parts.append("Low win rate and high drawdown — tighten entry confidence.")
    elif blocked > 5 and win_rate > 0.5:
        patch["min_score"] = max(1, int(cfg.get("min_score") or 2) - 1)
        rationale_parts.append("Many blocked entries with decent win rate — slightly relax min_score.")

    sent = context.get("sentiment") or {}
    agg = float(sent.get("aggregate_score") or 0)
    if agg >= 0.25 and not cfg.get("sentiment_filter_enabled"):
        patch["sentiment_filter_enabled"] = True
        patch["min_sentiment_score"] = 0.1
        rationale_parts.append("Positive news sentiment — optional sentiment alignment filter.")

    if not rationale_parts:
        rationale_parts.append("Metrics look balanced — no strong heuristic change; review manually.")

    clean, warnings = validate_suggested_params(
        context.get("strategy") or "",
        patch,
        base_config=cfg,
    )
    return {
        "rationale": " ".join(rationale_parts),
        "suggested_params": clean,
        "confidence": 0.45 if clean else 0.2,
        "source": "heuristic",
        "validation_warnings": warnings,
    }


async def suggest_strategy_params(
    context: dict[str, Any],
    *,
    model: str | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    if not STRATEGY_ADVISOR_ENABLED:
        return {
            "available": False,
            "error": "Strategy advisor disabled (STRATEGY_ADVISOR_ENABLED=false)",
        }

    if not use_llm or not await is_llm_available():
        out = _heuristic_suggestion(context)
        out["available"] = True
        out["llm_used"] = False
        return out

    provider, provider_name = await _pick_provider()
    if provider is None:
        out = _heuristic_suggestion(context)
        out["available"] = True
        out["llm_used"] = False
        return out

    user = f"DATA:\n{dumps_payload(context)}"
    result = await _chat(
        system=STRATEGY_ADVISOR_JSON_SYSTEM_PROMPT,
        user=user,
        model=resolve_model(model, task="deep"),
        task="deep",
        max_tokens=320,
        temperature=0.25,
        json_mode=True,
    )
    parsed = parse_json_object(result.text or "") if result.text else None
    if not parsed:
        out = _heuristic_suggestion(context)
        out["available"] = True
        out["llm_used"] = False
        out["llm_error"] = "Failed to parse LLM JSON"
        return out

    raw_patch = parsed.get("suggested_params") if isinstance(parsed.get("suggested_params"), dict) else {}
    clean, warnings = validate_suggested_params(
        context.get("strategy") or "",
        raw_patch,
        base_config=context.get("current_config") or {},
    )
    return {
        "available": True,
        "llm_used": True,
        "llm_provider": provider_name,
        "llm_model": result.model,
        "rationale": str(parsed.get("rationale") or "").strip() or _heuristic_suggestion(context)["rationale"],
        "suggested_params": clean,
        "confidence": float(parsed.get("confidence") or 0.5),
        "validation_warnings": warnings,
        "source": "llm",
    }


def _run_quick_backtest(
    backtester,
    feed,
    *,
    symbol: str,
    strategy: str,
    config: dict,
    days: int,
    timeframe: str,
) -> dict[str, Any]:
    from app.services.archive.resolve import resolve_backtest_candles
    from app.services.bots.risk_sizing import enrich_backtest_risk_config

    candles, meta = resolve_backtest_candles(
        symbol,
        feed,
        days=days,
        timeframe=timeframe,
    )
    enriched = enrich_backtest_risk_config(dict(config), None)
    result = backtester.run_backtest(symbol, strategy, enriched, candles)
    result["meta"] = meta
    return result


async def advise_bot_strategy(
    bot_id: str,
    *,
    backtester=None,
    feed=None,
    days: int | None = None,
    run_backtest: bool = True,
    use_llm: bool = True,
    model: str | None = None,
    recent_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bot = _load_bot(bot_id)
    if not bot:
        raise ValueError("Bot not found")

    days = int(days if days is not None else STRATEGY_ADVISOR_DEFAULT_DAYS)
    context = build_advisor_context(bot, recent_run=recent_run)
    suggestion = await suggest_strategy_params(context, model=model, use_llm=use_llm)

    out: dict[str, Any] = {
        "bot_id": bot_id,
        "symbol": bot.get("symbol"),
        "strategy": bot.get("strategy"),
        "timeframe": bot.get("timeframe"),
        "current_config": context["current_config"],
        "context": {
            "recent_backtests": context.get("recent_backtests"),
            "active_backtest_summary": context.get("active_backtest_summary"),
            "sentiment": context.get("sentiment"),
        },
        **suggestion,
    }

    patch = suggestion.get("suggested_params") or {}
    if run_backtest and backtester is not None and feed is not None and patch:
        try:
            baseline = _run_quick_backtest(
                backtester,
                feed,
                symbol=bot["symbol"],
                strategy=bot["strategy"],
                config=context["current_config"],
                days=days,
                timeframe=bot.get("timeframe") or "1m",
            )
            proposed_cfg = {**context["current_config"], **patch}
            candidate = _run_quick_backtest(
                backtester,
                feed,
                symbol=bot["symbol"],
                strategy=bot["strategy"],
                config=proposed_cfg,
                days=days,
                timeframe=bot.get("timeframe") or "1m",
            )
            out["backtest_comparison"] = {
                "days": days,
                "baseline": _summary_from_results(baseline),
                "proposed": _summary_from_results(candidate),
                "proposed_config": proposed_cfg,
            }
        except Exception as exc:
            logger.warning("Strategy advisor shadow backtest failed: %s", exc)
            out["backtest_comparison"] = {"error": str(exc)}

    return out
