"""Slim LLM prompt payloads and deterministic narrative fallbacks."""

from __future__ import annotations

import json
from typing import Any

_DROP_INSIGHT_KEYS = frozenset({
    "narrative",
    "model",
    "created_at",
    "insight_id",
    "version",
})

_TRADE_CONTEXT_KEYS = (
    "side",
    "price",
    "quantity",
    "signal_bar_time",
    "bar_time",
    "bar_time_iso",
    "reason",
    "pnl",
)


def _slim_sub_reports(sub: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, report in sub.items():
        if not isinstance(report, dict):
            continue
        slim: dict[str, Any] = {}
        for key in ("score", "reasons", "atr_regime", "suggested_size_factor"):
            if report.get(key) is not None:
                slim[key] = report[key]
        if slim.get("reasons"):
            slim["reasons"] = list(slim["reasons"])[:4]
        if slim:
            out[name] = slim
    return out


def slim_insight_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Minimal insight JSON for narrator prompts — no prior LLM output or IDs."""
    out: dict[str, Any] = {
        "symbol": data.get("symbol"),
        "timeframe": data.get("timeframe") or "1m",
        "bar_time": data.get("bar_time"),
        "signal": data.get("signal"),
        "confidence": data.get("confidence"),
        "score": data.get("score"),
        "reasons": (data.get("reasons") or [])[:3],
    }
    levels = data.get("levels")
    if isinstance(levels, dict) and levels:
        out["levels"] = levels
    sub = data.get("sub_reports")
    if isinstance(sub, dict) and sub:
        slim_sub = _slim_sub_reports(sub)
        if slim_sub:
            out["sub_reports"] = slim_sub
    tc = data.get("trade_context")
    if isinstance(tc, dict):
        out["trade_context"] = {
            k: tc[k] for k in _TRADE_CONTEXT_KEYS if tc.get(k) is not None
        }
    if data.get("strategy"):
        out["strategy"] = data.get("strategy")
    if data.get("run_kind"):
        out["run_kind"] = data.get("run_kind")
    if data.get("run_scope"):
        out["run_scope"] = data.get("run_scope")
    analyst_reasons = data.get("analyst_reasons")
    if analyst_reasons:
        out["analyst_reasons"] = list(analyst_reasons)[:3]
    if data.get("analyst_signal"):
        out["analyst_signal"] = data.get("analyst_signal")
    return {k: v for k, v in out.items() if v is not None}


def slim_trade_explain_payload(bundle: dict[str, Any]) -> dict[str, Any]:
    """Post-trade explain bundle — insight + trade + retrieved context."""
    out: dict[str, Any] = {}
    insight = bundle.get("insight") if isinstance(bundle.get("insight"), dict) else bundle
    if isinstance(insight, dict):
        slim = slim_insight_payload(insight)
        for key in _DROP_INSIGHT_KEYS:
            slim.pop(key, None)
        out["insight"] = slim
    tc = bundle.get("trade_context")
    if isinstance(tc, dict):
        out["trade_context"] = {
            k: tc[k] for k in _TRADE_CONTEXT_KEYS if tc.get(k) is not None
        }
    bot = bundle.get("bot")
    if isinstance(bot, dict):
        out["bot"] = {
            k: bot[k]
            for k in ("strategy", "timeframe", "symbol")
            if bot.get(k) is not None
        }
    logs = bundle.get("recent_logs")
    if isinstance(logs, list) and logs:
        out["recent_logs"] = [str(x) for x in logs[:8]]
    related_insights = bundle.get("related_insights")
    if isinstance(related_insights, list) and related_insights:
        out["related_insights"] = related_insights[:5]
    related_trades = bundle.get("related_trades")
    if isinstance(related_trades, list) and related_trades:
        out["related_trades"] = related_trades[:5]
    vision = bundle.get("vision_report")
    if isinstance(vision, dict) and vision.get("structure"):
        out["vision_report"] = {
            k: vision[k]
            for k in ("timeframe", "structure", "patterns", "notes", "rag_text", "report_id", "bar_time")
            if vision.get(k) is not None
        }
    return out


def template_trade_explain_narrative(bundle: dict[str, Any]) -> str | None:
    tc = bundle.get("trade_context") or {}
    insight = bundle.get("insight") or {}
    side = tc.get("side") or "?"
    sym = insight.get("symbol") or bundle.get("bot", {}).get("symbol") or "?"
    signal = insight.get("signal") or "NONE"
    conf = insight.get("confidence")
    conf_pct = f"{round(float(conf) * 100)}%" if conf is not None else "—"
    reasons = insight.get("reasons") or []
    top = reasons[0] if reasons else "rule alignment"
    sub = insight.get("sub_reports") or {}
    risk = sub.get("risk") or {}
    risk_bit = ""
    if risk.get("atr_regime"):
        risk_bit = f" Vol regime: {risk['atr_regime']}."
    logs = bundle.get("recent_logs") or []
    log_bit = f" Bot log: {logs[0][:80]}." if logs else ""
    return (
        f"Entry {side} on {sym}: analyst {signal} at {conf_pct} — {top}.{risk_bit}{log_bit}"
    ).strip()


def slim_backtest_trade_payload(bundle: dict[str, Any]) -> dict[str, Any]:
    """Per-trade backtest narrator payload."""
    tc = bundle.get("trade_context") or {}
    out: dict[str, Any] = {
        "symbol": bundle.get("symbol"),
        "strategy": bundle.get("strategy"),
        "run_kind": bundle.get("run_kind"),
        "run_scope": bundle.get("run_scope"),
        "entry_ordinal": bundle.get("entry_ordinal"),
        "entries_in_batch": bundle.get("entries_in_batch"),
        "signal": bundle.get("signal") or tc.get("side"),
        "trade_context": {
            k: tc[k] for k in _TRADE_CONTEXT_KEYS if tc.get(k) is not None
        },
    }
    insight = bundle.get("insight")
    if isinstance(insight, dict):
        slim = slim_insight_payload(insight)
        for key in _DROP_INSIGHT_KEYS:
            slim.pop(key, None)
        out["insight"] = slim
    elif bundle.get("analyst_signal") or bundle.get("analyst_reasons"):
        if bundle.get("analyst_signal"):
            out["analyst_signal"] = bundle.get("analyst_signal")
        reasons = bundle.get("analyst_reasons")
        if reasons:
            out["analyst_reasons"] = list(reasons)[:3]
    return {k: v for k, v in out.items() if v is not None}


def dumps_payload(data: dict[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"), default=str)


def template_insight_narrative(data: dict[str, Any]) -> str | None:
    signal = data.get("signal")
    if signal not in ("BUY", "SELL"):
        analyst = data.get("analyst_signal")
        if analyst in ("BUY", "SELL"):
            signal = analyst
        else:
            return None
    sym = data.get("symbol") or "?"
    tf = data.get("timeframe") or "1m"
    conf = data.get("confidence")
    conf_pct = f"{round(float(conf) * 100)}%" if conf is not None else "—"
    reasons = data.get("reasons") or data.get("analyst_reasons") or []
    top = reasons[0] if reasons else "rule alignment"
    tc = data.get("trade_context") or {}
    if tc.get("side"):
        price = tc.get("price")
        price_bit = f" at {price}" if price is not None else ""
        return f"Entry {tc['side']} on {sym}{price_bit} ({tf}): {signal} at {conf_pct} — {top}."
    return f"{signal} on {sym} ({tf}) at {conf_pct} confidence — {top}."


def template_backtest_narrative(bundle: dict[str, Any]) -> str | None:
    tc = bundle.get("trade_context") or {}
    side = tc.get("side") or bundle.get("signal")
    if not side:
        return None
    sym = bundle.get("symbol") or "?"
    reason = tc.get("reason") or "ENTRY"
    parts = [f"{side} entry on {sym}"]
    if tc.get("bar_time_iso"):
        parts.append(f"at {tc['bar_time_iso']}")
    parts.append(f"({reason})")
    scope = bundle.get("run_scope")
    if scope:
        parts.append(f"— {scope}")
    analyst_reasons = bundle.get("analyst_reasons")
    if not analyst_reasons and isinstance(bundle.get("insight"), dict):
        analyst_reasons = (bundle["insight"].get("reasons") or [])[:1]
    if analyst_reasons:
        parts.append(f"— {analyst_reasons[0]}")
    return " ".join(parts).rstrip() + "."
