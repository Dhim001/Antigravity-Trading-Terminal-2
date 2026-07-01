"""CHART_AGENT built-in strategy — consumes cached analyst insights."""

from __future__ import annotations

from typing import Any

from app.services.agent.chart_analyst import get_chart_analyst
from app.services.agent.regime_routing import resolve_regime_config
from app.services.bots.indicators import merge_strategy_config
from app.services.market.timeframes import normalize_timeframe


def compact_insight_snapshot(insight: dict) -> dict:
    """Compact insight payload for trade rows and bot logs."""
    return {
        "signal": insight.get("signal"),
        "score": insight.get("score"),
        "confidence": insight.get("confidence"),
        "reasons": (insight.get("reasons") or [])[:5],
        "sub_reports": insight.get("sub_reports"),
        "insight_id": insight.get("insight_id"),
        "bar_time": insight.get("bar_time"),
        "timeframe": insight.get("timeframe"),
    }


def sub_reports_summary(sub_reports: dict | None) -> dict:
    if not sub_reports:
        return {}
    out: dict[str, Any] = {}
    for key in ("trend", "momentum", "risk", "sentiment"):
        block = sub_reports.get(key)
        if not block:
            continue
        entry: dict[str, Any] = {}
        if block.get("score") is not None:
            entry["score"] = block["score"]
        if key == "risk":
            if block.get("atr_regime"):
                entry["atr_regime"] = block["atr_regime"]
            if block.get("suggested_size_factor") is not None:
                entry["suggested_size_factor"] = block["suggested_size_factor"]
        if key == "sentiment" and block.get("aggregate_score") is not None:
            entry["aggregate_score"] = block["aggregate_score"]
        if entry:
            out[key] = entry
    return out


def check_entry_filters(
    insight: dict,
    cfg: dict,
    signal: str,
    *,
    confirm_insight: dict | None = None,
) -> str | None:
    """Return reject reason when entry filters block the signal."""
    sub = insight.get("sub_reports") or {}

    min_score = cfg.get("min_score")
    if min_score is not None and min_score != "":
        try:
            threshold = int(min_score)
        except (TypeError, ValueError):
            threshold = None
        if threshold is not None:
            score = abs(int(insight.get("score") or 0))
            if score < threshold:
                return f"score {score} below min_score {threshold}"

    if cfg.get("require_trend_alignment"):
        trend_score = int((sub.get("trend") or {}).get("score") or 0)
        if signal == "BUY" and trend_score < 1:
            return f"trend score {trend_score} does not align with BUY"
        if signal == "SELL" and trend_score > -1:
            return f"trend score {trend_score} does not align with SELL"

    if cfg.get("block_elevated_vol"):
        regime = (sub.get("risk") or {}).get("atr_regime")
        if regime == "elevated":
            return "elevated ATR regime blocks entry"

    # 3.4-A: ADX trend regime filter — blocks momentum entries in choppy, ranging markets
    if cfg.get("block_ranging_markets"):
        trend_reg = (sub.get("trend") or {}).get("trend_regime")
        if trend_reg == "ranging":
            return "ranging market blocks entry"

    if cfg.get("sentiment_filter_enabled"):
        sent = sub.get("sentiment") or {}
        agg = sent.get("aggregate_score")
        if agg is not None:
            try:
                min_sent = float(cfg.get("min_sentiment_score", 0.0))
            except (TypeError, ValueError):
                min_sent = 0.0
            agg_f = float(agg)
            if signal == "BUY" and agg_f < min_sent:
                return f"sentiment {agg_f:+.2f} below min {min_sent} for BUY"
            if signal == "SELL" and agg_f > -min_sent:
                return f"sentiment {agg_f:+.2f} above -{min_sent} for SELL"

    confirm_tf = (cfg.get("confirm_timeframe") or "").strip()
    if confirm_tf:
        if not confirm_insight:
            return f"missing cached insight for confirm_timeframe {confirm_tf}"
        confirm_trend = int((confirm_insight.get("sub_reports") or {}).get("trend", {}).get("score") or 0)
        if signal == "BUY" and confirm_trend < 1:
            return f"{confirm_tf} trend score {confirm_trend} does not confirm BUY"
        if signal == "SELL" and confirm_trend > -1:
            return f"{confirm_tf} trend score {confirm_trend} does not confirm SELL"

    return None


def classify_filter_reject(reason: str | None) -> str | None:
    """Map reject_reason text to analytics bucket (min_score, trend, vol, htf, …)."""
    if not reason:
        return None
    text = reason.lower()
    if "min_score" in text or ("score" in text and "below" in text):
        return "min_score"
    if "confirm_timeframe" in text or "missing cached insight for confirm" in text:
        return "htf"
    if " confirm " in f" {text} " and "trend" in text:
        return "htf"
    if "trend" in text and ("align" in text or "does not" in text):
        return "trend"
    if "ranging market" in text:
        return "trend"
    if "elevated" in text or "atr regime" in text:
        return "vol"
    if "confidence" in text:
        return "confidence"
    if "calibration gate" in text:
        return "calibration"
    if "sentiment" in text:
        return "sentiment"
    return "other"


def build_signal_from_insight(
    insight: dict,
    cfg: dict,
    *,
    confirm_insight: dict | None = None,
    bot_id: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> dict:
    """Map a cached insight dict to bot signal_data (may return NONE + reject_reason)."""
    effective_cfg, regime = resolve_regime_config(cfg, insight)

    min_confidence = float(effective_cfg.get("min_confidence", 0.55))
    if float(insight.get("confidence", 0)) < min_confidence:
        return {
            "signal": "NONE",
            "reject_reason": f"confidence {insight.get('confidence')} below min {min_confidence}",
        }

    signal = insight.get("signal", "NONE")
    if signal not in ("BUY", "SELL"):
        return {"signal": "NONE", "reject_reason": f"non-actionable signal {signal}"}

    reject = check_entry_filters(insight, effective_cfg, signal, confirm_insight=confirm_insight)
    if reject:
        reason = reject
        if regime and regime != "normal":
            reason = f"{reject} (regime={regime})"
        return {"signal": "NONE", "reject_reason": reason}

    gate_symbol = symbol or effective_cfg.get("symbol") or insight.get("symbol") or ""
    gate_tf = timeframe or effective_cfg.get("timeframe") or insight.get("timeframe") or "1m"
    gate_bot_id = bot_id or effective_cfg.get("_bot_id")
    from app.services.bots.calibration import check_meta_label_gate

    meta_reject = check_meta_label_gate(
        insight,
        effective_cfg,
        symbol=str(gate_symbol),
        timeframe=str(gate_tf),
        signal=signal,
        bot_id=str(gate_bot_id) if gate_bot_id else None,
    )
    if meta_reject:
        reason = meta_reject
        if regime and regime != "normal":
            reason = f"{meta_reject} (regime={regime})"
        return {"signal": "NONE", "reject_reason": reason}

    sub = insight.get("sub_reports") or {}
    size_factor = float((sub.get("risk") or {}).get("suggested_size_factor") or 1.0)

    out: dict[str, Any] = {
        "signal": signal,
        "confidence": float(insight.get("confidence", 0)),
        "score": insight.get("score"),
        "reasons": insight.get("reasons") or [],
        "sub_reports": sub,
        "sub_reports_summary": sub_reports_summary(sub),
        "insight_id": insight.get("insight_id"),
        "size_factor": size_factor,
        "insight_snapshot": compact_insight_snapshot(insight),
    }
    levels = insight.get("levels") or {}
    if levels.get("stop_loss_distance") is not None:
        out["stop_loss_distance"] = levels["stop_loss_distance"]
    if levels.get("take_profit_price") is not None:
        out["take_profit_price"] = levels["take_profit_price"]
    return out


class ChartAgentStrategy:
    def __init__(self, config: dict):
        self.config = config or {}

    def evaluate(self, df_row: dict) -> dict:
        cfg = merge_strategy_config("CHART_AGENT", self.config)
        symbol = cfg.get("symbol") or self.config.get("symbol", "")
        timeframe = cfg.get("timeframe") or self.config.get("timeframe", "1m")
        try:
            tf = normalize_timeframe(timeframe)
        except ValueError:
            tf = "1m"
        bar_time = df_row.get("time")

        try:
            analyst = get_chart_analyst()
        except RuntimeError:
            return {"signal": "NONE", "reject_reason": "chart analyst unavailable"}

        insight = analyst.get_cached(symbol, timeframe=tf)
        if not insight:
            return {"signal": "NONE", "reject_reason": "no cached insight"}

        if insight.get("bar_time") != bar_time:
            return {
                "signal": "NONE",
                "reject_reason": (
                    f"bar_time mismatch (cached={insight.get('bar_time')}, bar={bar_time})"
                ),
            }

        insight_tf = insight.get("timeframe", "1m")
        try:
            if normalize_timeframe(insight_tf) != tf:
                return {
                    "signal": "NONE",
                    "reject_reason": f"timeframe mismatch (cached={insight_tf}, bot={tf})",
                }
        except ValueError:
            return {"signal": "NONE", "reject_reason": f"invalid cached timeframe {insight_tf}"}

        confirm_insight = None
        confirm_tf = (cfg.get("confirm_timeframe") or "").strip()
        if confirm_tf:
            try:
                confirm_tf_norm = normalize_timeframe(confirm_tf)
            except ValueError:
                return {"signal": "NONE", "reject_reason": f"invalid confirm_timeframe {confirm_tf}"}
            confirm_insight = analyst.get_cached(symbol, timeframe=confirm_tf_norm)

        return build_signal_from_insight(
            insight,
            cfg,
            confirm_insight=confirm_insight,
            bot_id=cfg.get("_bot_id"),
            symbol=symbol,
            timeframe=tf,
        )
