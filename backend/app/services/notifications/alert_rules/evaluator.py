"""Evaluate a single alert rule against bar-close metrics."""

from __future__ import annotations

from typing import Any

from app.services.notifications.alert_rules import types as atypes


def compute_bar_metrics(symbol: str, candles: list) -> dict[str, Any] | None:
    """Build indicator snapshot for the last completed bar."""
    if not candles or len(candles) < 30:
        return None

    from app.services.agent.feature_builder import FeatureBuilder
    from app.services.agent.rule_engine import macd_cross_label, score_dataframe
    from app.services.bots.screener import MarketScreenerService

    df = FeatureBuilder(MarketScreenerService()).build(symbol, candles)
    if df is None or df.empty or len(df) < 3:
        return None

    idx = len(df) - 2
    row = df.iloc[idx]
    prev = df.iloc[idx - 1] if idx > 0 else None
    closed = candles[-2]
    prev_bar = candles[-3] if len(candles) >= 3 else None

    close = float(closed.get("close") or 0)
    prev_close = float(prev_bar.get("close") or close) if prev_bar else close
    pct_change = ((close - prev_close) / prev_close * 100.0) if prev_close else 0.0

    rsi_raw = row.get("RSI_14")
    rsi = float(rsi_raw) if rsi_raw is not None else None

    insight = score_dataframe(df, symbol, eval_index=idx)
    signal = insight.signal if insight else "NONE"

    return {
        "close": close,
        "rsi": rsi,
        "macd_cross": macd_cross_label(row, prev),
        "signal": signal,
        "pct_change": round(pct_change, 4),
        "bar_time": closed.get("time"),
        "score": insight.score if insight else 0,
    }


def rule_matches(rule: dict[str, Any], metrics: dict[str, Any]) -> bool:
    ctype = rule.get("condition_type")
    threshold = rule.get("threshold")

    if ctype == atypes.PRICE_ABOVE:
        return metrics["close"] >= float(threshold)
    if ctype == atypes.PRICE_BELOW:
        return metrics["close"] <= float(threshold)
    if ctype == atypes.RSI_ABOVE:
        return metrics.get("rsi") is not None and metrics["rsi"] >= float(threshold)
    if ctype == atypes.RSI_BELOW:
        return metrics.get("rsi") is not None and metrics["rsi"] <= float(threshold)
    if ctype == atypes.MACD_CROSS_BULL:
        return metrics.get("macd_cross") == "bullish"
    if ctype == atypes.MACD_CROSS_BEAR:
        return metrics.get("macd_cross") == "bearish"
    if ctype == atypes.SIGNAL_IS:
        want = (rule.get("signal") or "BUY").upper()
        return (metrics.get("signal") or "NONE").upper() == want
    if ctype == atypes.PCT_CHANGE_ABOVE:
        return metrics["pct_change"] >= float(threshold)
    if ctype == atypes.PCT_CHANGE_BELOW:
        return metrics["pct_change"] <= float(threshold)
    return False


def format_alert_message(rule: dict[str, Any], metrics: dict[str, Any]) -> tuple[str, str]:
    sym = rule.get("symbol", "")
    tf = rule.get("timeframe", "1m")
    name = rule.get("name") or "Alert"
    ctype = rule.get("condition_type", "")

    title = f"Alert: {name} ({sym} {tf})"
    parts = [f"{sym} {tf} — {ctype.replace('_', ' ')}"]

    if ctype in atypes.NEEDS_THRESHOLD:
        parts.append(f"threshold {rule.get('threshold')}")
    if ctype == atypes.SIGNAL_IS:
        parts.append(f"signal {rule.get('signal')}")

    parts.append(f"close={metrics.get('close')}")
    if metrics.get("rsi") is not None:
        parts.append(f"RSI={metrics['rsi']:.1f}")
    if metrics.get("signal"):
        parts.append(f"analyst={metrics['signal']}")
    if metrics.get("macd_cross"):
        parts.append(f"MACD={metrics['macd_cross']}")

    return title, " · ".join(parts)
