"""LLM tool-calling agent loop for TRADE_COPILOT.

The model chooses tools + arguments from natural language (including timeframe).
Keyword classify_intent remains a fallback when the LLM is off or fails.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services.agent.llm.base import parse_json_object
from app.services.agent.llm.router import _chat
from app.services.market.timeframes import TIMEFRAME_SECS, normalize_timeframe

logger = logging.getLogger(__name__)

_AGENT_TIMEOUT_SEC = 45.0
_ALLOWED_TFS = tuple(TIMEFRAME_SECS.keys())

PLANNER_SYSTEM = """You are TRADE_COPILOT's tool planner for a trading terminal.
You can use multiple turns to gather information before giving a final answer.
Reply with ONLY a JSON object (no markdown fences):
{
  "tool_calls": [{"name": "<tool>", "arguments": {}}],
  "direct_reply": "<your final response if you have enough info, or null>"
}

Rules:
- Prefer calling a tool over guessing market state.
- If you need more info, output `tool_calls` and keep `direct_reply` null.
- If you have gathered enough info from previous turns, output an empty list for `tool_calls` and put your final comprehensive answer in `direct_reply`.
- For regime / trending / ranging / "what is X doing" / price / "how is SYMBOL" → analyze_symbol.
- For "which assets are doing a lot", "market scan", "top movers", "active assets" → scan_market.
- For "why did my bot pause" / "why was the trade blocked" / logs / events → explain_bot_events.
- Advisory "which bot / strategy for ranging" → recommend_strategy.
- deploy_bot / pause / stop / update_bot_config require confirmation — still emit the tool call; the host will gate it.
- Use at most 2 tool_calls per turn.

Tools:
1. analyze_symbol — args: symbol (string), timeframe (1m,5m,15m,1h,4h,1d)
2. meta_insight — args: field (timeframe|method|confidence|signal|regime), symbol (optional)
3. recommend_strategy — args: symbol (optional), regime (optional)
4. get_portfolio_status — args: {}
5. list_bots — args: {}
6. get_bot_performance — args: bot_id (optional)
7. get_sentiment — args: symbol
8. run_backtest — args: symbol, strategy, days (int), timeframe (optional)
9. explain_trade — args: bot_id (optional), symbol (optional)
10. explain_bot_events — args: bot_id (optional), limit (int, optional)
11. deploy_bot — args: symbol, strategy, allocation, timeframe (optional)
12. pause_bot / stop_bot / pause_all_bots / stop_all_bots
13. update_bot_config — args: bot_id or symbol, config_patch (object)
14. help — args: {}
15. scan_market — args: limit (int, optional)
"""


def _safe_tf(raw: Any, default: str = "1m") -> str:
    try:
        return normalize_timeframe(str(raw or default))
    except ValueError:
        return default


def parse_planner_response(text: str | None) -> dict[str, Any]:
    """Normalize planner JSON into {tool_calls: [...], direct_reply: str|None}."""
    parsed = parse_json_object(text) or {}
    if not isinstance(parsed, dict):
        parsed = {}

    calls_raw = parsed.get("tool_calls")
    if calls_raw is None and isinstance(parsed.get("name"), str):
        calls_raw = [parsed]
    if calls_raw is None and isinstance(parsed.get("tool"), str):
        calls_raw = [{"name": parsed.get("tool"), "arguments": parsed.get("arguments") or parsed.get("args") or {}}]

    calls: list[dict[str, Any]] = []
    if isinstance(calls_raw, dict):
        calls_raw = [calls_raw]
    if isinstance(calls_raw, list):
        for item in calls_raw[:2]:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("tool")
            if not name:
                continue
            args = item.get("arguments") if isinstance(item.get("arguments"), dict) else None
            if args is None:
                args = item.get("args") if isinstance(item.get("args"), dict) else {}
            # Native OpenAI tool_calls shape
            if not args and isinstance(item.get("function"), dict):
                fn = item["function"]
                name = fn.get("name") or name
                raw_args = fn.get("arguments")
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = {}
                elif isinstance(raw_args, dict):
                    args = raw_args
            calls.append({"name": str(name).strip(), "arguments": dict(args or {})})

    direct = parsed.get("direct_reply")
    if direct is not None and not isinstance(direct, str):
        direct = str(direct)
    return {"tool_calls": calls, "direct_reply": direct}


def build_session_context(
    *,
    session_id: str,
    active_symbol: str | None,
    message: str,
    session_memory: dict[str, Any],
    turn_history: list[dict[str, Any]] | None = None,
) -> str:
    preferred_tf = session_memory.get("preferred_timeframe") or "1m"
    last = session_memory.get("last_insight") if isinstance(session_memory.get("last_insight"), dict) else None
    lines = [
        f"Active chart symbol: {active_symbol or '(none)'}",
        f"Session preferred timeframe: {preferred_tf}",
        f"User message: {message}",
    ]
    if last:
        lines.append(
            "Last analysis: "
            f"{last.get('symbol')} @ {last.get('timeframe') or preferred_tf} → "
            f"regime={last.get('market_regime') or last.get('trend_regime')} "
            f"signal={last.get('signal')}"
        )
    if turn_history:
        lines.append("\n--- Turn History ---")
        for i, turn in enumerate(turn_history, 1):
            lines.append(f"Turn {i}: Tool '{turn.get('tool')}' returned:\n{json.dumps(turn.get('result'))}")
        lines.append("--------------------")
    return "\n".join(lines)


async def plan_tool_calls(
    *,
    message: str,
    session_id: str,
    active_symbol: str | None,
    session_memory: dict[str, Any],
    turn_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Ask the LLM which tools to run. Returns None if LLM unavailable/unusable."""
    ctx = build_session_context(
        session_id=session_id,
        active_symbol=active_symbol,
        message=message,
        session_memory=session_memory,
        turn_history=turn_history,
    )
    result = await _chat(
        system=PLANNER_SYSTEM,
        user=ctx,
        task="narrator",
        max_tokens=400,
        temperature=0.1,
        json_mode=True,
        timeout=_AGENT_TIMEOUT_SEC,
    )
    if result.provider == "off" or (not result.text and not result.tool_calls):
        return None

    if result.tool_calls:
        return parse_planner_response(json.dumps({"tool_calls": result.tool_calls}))

    plan = parse_planner_response(result.text)
    if plan["tool_calls"] or plan.get("direct_reply"):
        return plan
    return None


def extract_timeframe_hint(text: str) -> str | None:
    """Best-effort TF from user text (agent uses LLM; this helps rules fallback)."""
    t = (text or "").lower()
    # Explicit "5 minute" / "5 min" / "5m"
    m = re.search(
        r"\b(\d+)\s*(?:m|min|mins|minute|minutes)\b|\b(\d+)\s*(?:h|hr|hour|hours)\b|\b(\d+)\s*d(?:ay)?s?\b",
        t,
    )
    if m:
        if m.group(1):
            n = int(m.group(1))
            cand = f"{n}m"
        elif m.group(2):
            n = int(m.group(2))
            cand = f"{n}h"
        else:
            n = int(m.group(3))
            cand = f"{n}d"
        if cand in TIMEFRAME_SECS:
            return cand
    for tf in ("15m", "5m", "1m", "4h", "1h", "1d"):
        if re.search(rf"\b{re.escape(tf)}\b", t):
            return tf
    return None


async def execute_planned_calls(
    state: Any,
    plan: dict[str, Any],
    *,
    session_id: str,
    message: str,
    active_symbol: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str]:
    """Run planned tool calls via copilot handlers. Returns (tool_results, pending, intent)."""
    from app.services.agent.copilot import (  # local import avoids cycles at module load
        INTENT_ACTION,
        INTENT_ANALYSIS,
        INTENT_EXPLAIN,
        INTENT_HELP,
        INTENT_QUERY,
        _BOT_ID_RE,
        _PCT_RE,
        _clarify_text,
        _help_text,
        _tool_analyze,
        _tool_bot_performance,
        _tool_explain,
        _tool_list_bots,
        _tool_meta_insight,
        _tool_portfolio,
        _tool_recommend_strategy,
        _tool_run_backtest,
        _tool_sentiment,
        extract_allocation,
        extract_days,
        extract_strategy,
        extract_symbol,
        get_last_insight,
        looks_like_explicit_help,
        looks_like_market_question,
        normalize_symbol,
        remember_insight,
        remember_timeframe,
        get_preferred_timeframe,
    )

    bot_manager = getattr(state, "bot_manager", None)
    oms = getattr(state, "oms", None)
    tool_results: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    intent = INTENT_ANALYSIS

    preferred = get_preferred_timeframe(session_id)
    hint_tf = extract_timeframe_hint(message)
    if hint_tf:
        remember_timeframe(session_id, hint_tf)
        preferred = hint_tf

    calls = plan.get("tool_calls") or []
    if not calls and plan.get("direct_reply"):
        # Treat planner prose as clarify — not the canned help menu.
        tool_results.append({
            "tool": "clarify",
            "result": {"text": str(plan["direct_reply"])},
        })
        return tool_results, None, INTENT_HELP

    for call in calls:
        name = str(call.get("name") or "").strip()
        args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}

        # Planner sometimes emits help on market Qs — redirect.
        if name == "help" and looks_like_market_question(message) and not looks_like_explicit_help(message):
            name = "analyze_symbol"
            args = dict(args or {})

        if name == "analyze_symbol":
            intent = INTENT_ANALYSIS
            sym = normalize_symbol(
                args.get("symbol") or extract_symbol(message, active_symbol)
            )
            tf = _safe_tf(args.get("timeframe") or preferred, preferred)
            remember_timeframe(session_id, tf)
            if not sym:
                # Reuse last insight symbol on TF-only follow-ups
                last = get_last_insight(session_id)
                sym = normalize_symbol((last or {}).get("symbol")) if last else None
            if not sym:
                tool_results.append({"tool": "analyze_symbol", "result": {"error": "Specify a symbol"}})
            else:
                analysis = await _tool_analyze(state, sym, timeframe=tf)
                remember_insight(session_id, analysis)
                tool_results.append({"tool": "analyze_symbol", "result": analysis})

        elif name == "meta_insight":
            intent = INTENT_ANALYSIS
            # Inject field into a synthetic message if LLM provided it
            field = args.get("field")
            meta_msg = message
            if field and field not in message.lower():
                meta_msg = f"{field}: {message}"
            tool_results.append({
                "tool": "meta_insight",
                "result": _tool_meta_insight(session_id, meta_msg, active_symbol=active_symbol),
            })

        elif name == "recommend_strategy":
            intent = INTENT_ANALYSIS
            rec = await _tool_recommend_strategy(state, message, active_symbol=active_symbol)
            if isinstance(rec.get("_insight"), dict):
                remember_insight(session_id, rec.pop("_insight"))
            else:
                rec.pop("_insight", None)
            tool_results.append({"tool": "recommend_strategy", "result": rec})

        elif name == "scan_market":
            intent = INTENT_ANALYSIS
            from app.services.agent.copilot import _tool_scan_market
            limit = int(args.get("limit") or 5)
            tool_results.append({
                "tool": "scan_market",
                "result": await _tool_scan_market(bot_manager, limit=limit),
            })

        elif name == "get_portfolio_status":
            intent = INTENT_QUERY
            tool_results.append({"tool": "get_portfolio_status", "result": _tool_portfolio(oms)})

        elif name == "list_bots":
            intent = INTENT_QUERY
            tool_results.append({"tool": "list_bots", "result": _tool_list_bots(bot_manager)})

        elif name == "get_bot_performance":
            intent = INTENT_QUERY
            bid = args.get("bot_id")
            if not bid:
                m = _BOT_ID_RE.search(message)
                bid = m.group(1) if m else None
            tool_results.append({
                "tool": "get_bot_performance",
                "result": _tool_bot_performance(bot_manager, bid),
            })

        elif name == "get_sentiment":
            intent = INTENT_ANALYSIS
            sym = normalize_symbol(args.get("symbol") or extract_symbol(message, active_symbol)) or "AAPL"
            tool_results.append({"tool": "get_sentiment", "result": _tool_sentiment(sym)})

        elif name == "run_backtest":
            intent = INTENT_ANALYSIS
            sym = normalize_symbol(args.get("symbol") or extract_symbol(message, active_symbol))
            if not sym:
                tool_results.append({"tool": "run_backtest", "result": {"error": "Specify a symbol"}})
            else:
                strategy = args.get("strategy") or extract_strategy(message)
                days = int(args.get("days") or extract_days(message, default=30))
                tf = _safe_tf(args.get("timeframe") or preferred, preferred)
                alloc = float(args.get("allocation") or extract_allocation(message))
                tool_results.append({
                    "tool": "run_backtest",
                    "result": await _tool_run_backtest(
                        state, sym, strategy, days, timeframe=tf, allocation=alloc
                    ),
                })

        elif name == "explain_trade":
            intent = INTENT_EXPLAIN
            bid = args.get("bot_id")
            if not bid and bot_manager and bot_manager.active_bots:
                sym = normalize_symbol(args.get("symbol") or extract_symbol(message, active_symbol))
                for b in bot_manager.active_bots.values():
                    if sym and str(b.get("symbol") or "").upper() == sym:
                        bid = b.get("id")
                        break
                if not bid:
                    bid = next(iter(bot_manager.active_bots))
            tool_results.append({
                "tool": "explain_trade",
                "result": await _tool_explain(state, bid or ""),
            })

        elif name == "explain_bot_events":
            intent = INTENT_EXPLAIN
            bid = args.get("bot_id")
            if not bid and bot_manager and bot_manager.active_bots:
                sym = normalize_symbol(args.get("symbol") or extract_symbol(message, active_symbol))
                for b in bot_manager.active_bots.values():
                    if sym and str(b.get("symbol") or "").upper() == sym:
                        bid = b.get("id")
                        break
                if not bid:
                    bid = next(iter(bot_manager.active_bots))
            from app.services.agent.copilot import _tool_explain_bot_events
            tool_results.append({
                "tool": "explain_bot_events",
                "result": await _tool_explain_bot_events(bid or "", limit=args.get("limit") or 5),
            })

        elif name == "deploy_bot":
            intent = INTENT_ACTION
            sym = normalize_symbol(args.get("symbol") or extract_symbol(message, active_symbol))
            if not sym:
                tool_results.append({"tool": "deploy_bot", "result": {"error": "Specify a symbol to deploy"}})
            else:
                tf = _safe_tf(args.get("timeframe") or preferred, preferred)
                pending = {
                    "type": "deploy_bot",
                    "params": {
                        "strategy": args.get("strategy") or extract_strategy(message) or "CHART_AGENT",
                        "symbol": sym,
                        "timeframe": tf,
                        "allocation": float(args.get("allocation") or extract_allocation(message)),
                        "config": {"pipeline_source": "copilot"},
                    },
                }

        elif name in ("pause_all_bots", "stop_all_bots"):
            intent = INTENT_ACTION
            pending = {"type": name, "params": {}}

        elif name in ("pause_bot", "stop_bot"):
            intent = INTENT_ACTION
            bid = args.get("bot_id")
            if not bid:
                m = _BOT_ID_RE.search(message)
                bid = m.group(1) if m else None
            if not bid and bot_manager:
                sym = normalize_symbol(args.get("symbol") or extract_symbol(message, active_symbol))
                for b in bot_manager.active_bots.values():
                    if sym and str(b.get("symbol") or "").upper() == sym:
                        bid = b.get("id")
                        break
            if not bid:
                tool_results.append({"tool": name, "result": {"error": "Specify a bot id or symbol"}})
            else:
                pending = {"type": name, "params": {"bot_id": bid}}

        elif name == "update_bot_config":
            intent = INTENT_ACTION
            bid = args.get("bot_id")
            patch = args.get("config_patch") if isinstance(args.get("config_patch"), dict) else {}
            if not bid:
                m = _BOT_ID_RE.search(message)
                bid = m.group(1) if m else None
            if not patch:
                pct_m = _PCT_RE.search(message)
                if pct_m and ("stop" in message.lower() or "sl" in message.lower()):
                    patch["stop_loss_percent"] = float(pct_m.group(1))
                if "confidence" in message.lower() and pct_m:
                    val = float(pct_m.group(1))
                    patch["min_confidence"] = val / 100.0 if val > 1 else val
            if not bid and bot_manager:
                sym = normalize_symbol(args.get("symbol") or extract_symbol(message, active_symbol))
                for b in bot_manager.active_bots.values():
                    if sym and str(b.get("symbol") or "").upper() == sym:
                        bid = b.get("id")
                        break
            if not bid or not patch:
                tool_results.append({
                    "tool": "update_bot_config",
                    "result": {"error": "Need bot id/symbol and a config change"},
                })
            else:
                pending = {"type": "update_bot_config", "params": {"bot_id": bid, "config_patch": patch}}

        elif name == "help":
            intent = INTENT_HELP
            tool_results.append({"tool": "help", "result": _help_text()})

        else:
            tool_results.append({
                "tool": name or "unknown",
                "result": {"error": f"Unknown tool: {name}"},
            })

    if not tool_results and not pending:
        if looks_like_explicit_help(message):
            tool_results.append({"tool": "help", "result": _help_text()})
            intent = INTENT_HELP
        elif looks_like_market_question(message) or extract_symbol(message, active_symbol):
            intent = INTENT_ANALYSIS
            sym = normalize_symbol(extract_symbol(message, active_symbol))
            if not sym:
                last = get_last_insight(session_id)
                sym = normalize_symbol((last or {}).get("symbol")) if last else None
            if sym:
                analysis = await _tool_analyze(state, sym, timeframe=preferred)
                remember_insight(session_id, analysis)
                tool_results.append({"tool": "analyze_symbol", "result": analysis})
            else:
                tool_results.append({
                    "tool": "clarify",
                    "result": _clarify_text(normalize_symbol(active_symbol)),
                })
                intent = INTENT_HELP
        else:
            tool_results.append({
                "tool": "clarify",
                "result": _clarify_text(normalize_symbol(active_symbol)),
            })
            intent = INTENT_HELP

    return tool_results, pending, intent
