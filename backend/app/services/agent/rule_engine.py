"""Rule-based chart signal engine — parity with frontend generateSignal()."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from app.services.agent.bar_time import coerce_bar_time, find_bar_index
from app.services.agent.models import ChartAgentInsight, SignalType
from app.services.agent.anomaly_detector import detect_bar_anomaly
from app.services.bots.indicators import atr_col

RSI_LEN = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
MACD_COL = f"MACD_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"
MACD_SIGNAL_COL = f"MACDs_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"
EMA_COLS = ("EMA_9", "EMA_21", "EMA_50")
ATR_LEN = 14


@dataclass
class DomainScore:
    score: int = 0
    reasons: list[str] = field(default_factory=list)


def _display_signal(score: int) -> str:
    if score >= 4:
        return "STRONG BUY"
    if score >= 2:
        return "BUY"
    if score <= -4:
        return "STRONG SELL"
    if score <= -2:
        return "SELL"
    return "NEUTRAL"


def _bot_signal(score: int) -> SignalType:
    if score >= 2:
        return "BUY"
    if score <= -2:
        return "SELL"
    return "NONE"


def _confidence(score: int) -> float:
    """Sigmoid-mapped confidence: score 2→~0.62, 3→~0.73, 4→~0.82, 5→~0.88."""
    return round(1.0 / (1.0 + math.exp(-0.8 * (abs(score) - 3))), 3)


def _score_sentiment(symbol: str) -> DomainScore:
    """News/social aggregate sentiment from persisted sentiment_events."""
    from app.config import SENTIMENT_ENABLED, SENTIMENT_LOOKBACK_HOURS, SENTIMENT_SCORE_THRESHOLD
    from app.services.altdata.store import get_aggregate_sentiment

    if not SENTIMENT_ENABLED:
        return DomainScore(score=0, reasons=[])

    agg = get_aggregate_sentiment(symbol, lookback_hours=SENTIMENT_LOOKBACK_HOURS)
    score_val = float(agg.get("aggregate_score") or 0.0)
    mentions = int(agg.get("mention_count") or 0)
    if mentions == 0:
        return DomainScore(score=0, reasons=[])

    threshold = float(SENTIMENT_SCORE_THRESHOLD)
    domain_score = 0
    reasons: list[str] = []
    if score_val >= threshold:
        domain_score = 1
        reasons.append(f"News sentiment bullish ({score_val:+.2f}, {mentions} items)")
    elif score_val <= -threshold:
        domain_score = -1
        reasons.append(f"News sentiment bearish ({score_val:+.2f}, {mentions} items)")
    else:
        reasons.append(f"News sentiment neutral ({score_val:+.2f}, {mentions} items)")

    return DomainScore(score=domain_score, reasons=reasons)


def _score_trend(row: pd.Series, price: float) -> DomainScore:
    score = 0
    reasons: list[str] = []
    e9, e21, e50 = (row.get(c) for c in EMA_COLS)
    if all(v is not None and not (isinstance(v, float) and math.isnan(v)) for v in (e9, e21, e50)):
        e9, e21, e50 = float(e9), float(e21), float(e50)
        if price > e9 > e21 > e50:
            score += 2
            reasons.append("Price above all EMAs (uptrend)")
        elif price < e9 < e21 < e50:
            score -= 2
            reasons.append("Price below all EMAs (downtrend)")
        elif price > e21:
            score += 1
            reasons.append("Price above EMA21")
        elif price < e21:
            score -= 1
            reasons.append("Price below EMA21")
    return DomainScore(score=score, reasons=reasons)


def _score_momentum(row: pd.Series, prev: pd.Series | None) -> DomainScore:
    score = 0
    reasons: list[str] = []

    rsi = row.get(f"RSI_{RSI_LEN}")
    if rsi is not None and not (isinstance(rsi, float) and math.isnan(rsi)):
        rsi = float(rsi)
        if rsi < 30:
            score += 2
            reasons.append(f"RSI oversold ({rsi:.1f})")
        elif rsi < 45:
            score += 1
            reasons.append(f"RSI bullish zone ({rsi:.1f})")
        elif rsi > 70:
            score -= 2
            reasons.append(f"RSI overbought ({rsi:.1f})")
        elif rsi > 55:
            score -= 1
            reasons.append(f"RSI bearish zone ({rsi:.1f})")
        else:
            reasons.append(f"RSI neutral ({rsi:.1f})")

    last_macd = row.get(MACD_COL)
    last_signal = row.get(MACD_SIGNAL_COL)
    prev_macd = prev.get(MACD_COL) if prev is not None else None
    prev_signal = prev.get(MACD_SIGNAL_COL) if prev is not None else None
    if (
        last_macd is not None
        and last_signal is not None
        and not any(isinstance(v, float) and math.isnan(v) for v in (last_macd, last_signal))
    ):
        last_macd = float(last_macd)
        last_signal = float(last_signal)
        if (
            prev_macd is not None
            and prev_signal is not None
            and float(last_macd) > float(last_signal)
            and float(prev_macd) <= float(prev_signal)
        ):
            score += 2
            reasons.append("MACD bullish crossover")
        elif (
            prev_macd is not None
            and prev_signal is not None
            and float(last_macd) < float(last_signal)
            and float(prev_macd) >= float(prev_signal)
        ):
            score -= 2
            reasons.append("MACD bearish crossover")
        elif last_macd > last_signal:
            score += 1
            reasons.append("MACD above signal")
        elif last_macd < last_signal:
            score -= 1
            reasons.append("MACD below signal")

    return DomainScore(score=score, reasons=reasons)


def _score_volume(row: pd.Series, df: pd.DataFrame, idx: int) -> DomainScore:
    """Volume domain: rewards conviction surges, penalises low-volume signals.

    +1 when bar volume is ≥ 1.5× the 20-bar rolling average (conviction surge).
    −1 when bar volume is ≤ 0.5× the 20-bar rolling average (weak conviction).
     0 otherwise (normal volume or data unavailable).
    """
    vol = row.get("volume")
    if vol is None or (isinstance(vol, float) and math.isnan(vol)):
        return DomainScore(score=0, reasons=["volume unavailable"])
    vol_f = float(vol)
    if vol_f <= 0:
        return DomainScore(score=0, reasons=["volume zero"])

    window = df.iloc[max(0, idx - 19): idx + 1]
    if "volume" not in window.columns:
        return DomainScore(score=0, reasons=["volume column missing"])
    series = window["volume"].replace(0, float("nan")).dropna()
    if series.empty:
        return DomainScore(score=0, reasons=["no volume history"])
    avg_vol = float(series.mean())
    if avg_vol <= 0:
        return DomainScore(score=0, reasons=["avg volume zero"])

    ratio = vol_f / avg_vol
    if ratio >= 1.5:
        return DomainScore(score=1, reasons=[f"Volume surge {ratio:.1f}× avg (confirms move)"])
    if ratio <= 0.5:
        return DomainScore(score=-1, reasons=[f"Volume below avg {ratio:.1f}× (weak conviction)"])
    return DomainScore(score=0, reasons=[f"Volume normal {ratio:.1f}× avg"])


def _risk_report(row: pd.Series, df: pd.DataFrame, idx: int) -> dict:
    atr = row.get(atr_col(ATR_LEN))
    if atr is None or (isinstance(atr, float) and math.isnan(atr)):
        return {
            "score": 0,
            "atr_regime": "normal",
            "suggested_size_factor": 1.0,
            "reasons": ["ATR unavailable"],
        }

    atr = float(atr)
    window = df.iloc[max(0, idx - 19): idx + 1]
    atr_col_name = atr_col(ATR_LEN)
    if atr_col_name not in window.columns:
        return {
            "score": 0,
            "atr_regime": "normal",
            "suggested_size_factor": 1.0,
            "reasons": [],
        }

    series = window[atr_col_name].dropna()
    if series.empty:
        median_atr = atr
    else:
        median_atr = float(series.median())

    ratio = atr / median_atr if median_atr > 0 else 1.0
    reasons: list[str] = []
    if ratio >= 1.5:
        regime = "elevated"
        factor = 0.8
        reasons.append(f"ATR {ratio:.1f}× 20-bar median (elevated vol)")
    elif ratio <= 0.7:
        regime = "compressed"
        factor = 1.2
        reasons.append(f"ATR {ratio:.1f}× 20-bar median (compressed vol)")
    else:
        regime = "normal"
        factor = 1.0
        reasons.append(f"ATR {ratio:.1f}× 20-bar median")

    return {
        "score": 0,
        "atr_regime": regime,
        "suggested_size_factor": factor,
        "reasons": reasons,
    }


def _score_row(
    row: pd.Series,
    prev: pd.Series | None,
    price: float,
) -> tuple[int, list[str]]:
    """Legacy flat scorer — trend + momentum only."""
    trend = _score_trend(row, price)
    momentum = _score_momentum(row, prev)
    score = trend.score + momentum.score
    reasons = trend.reasons + momentum.reasons
    return score, reasons


def _classify_trend_regime(row: pd.Series, df: pd.DataFrame, idx: int) -> str:
    """Classifies the market trend regime based on ADX (3.4-A).

    Returns 'trending' if ADX > 25, else 'ranging'.
    """
    from app.services.bots.indicators import adx_col
    # Retrieve ADX column name (default length 14)
    adx_name = adx_col(14)
    if adx_name not in df.columns:
        return "unknown"

    adx_val = row.get(adx_name)
    if adx_val is None or (isinstance(adx_val, float) and math.isnan(adx_val)):
        return "unknown"

    return "trending" if float(adx_val) > 25 else "ranging"


def _build_sub_reports(
    row: pd.Series,
    prev: pd.Series | None,
    price: float,
    df: pd.DataFrame,
    idx: int,
    *,
    symbol: str = "",
) -> dict:
    trend = _score_trend(row, price)
    momentum = _score_momentum(row, prev)
    volume = _score_volume(row, df, idx)
    risk = _risk_report(row, df, idx)
    sentiment = _score_sentiment(symbol)
    trend_regime = _classify_trend_regime(row, df, idx)
    indicator = {"score": momentum.score, "reasons": list(momentum.reasons)}
    anomaly = detect_bar_anomaly(df, idx)
    from app.config import SENTIMENT_LOOKBACK_HOURS
    from app.services.altdata.store import get_aggregate_sentiment

    agg = get_aggregate_sentiment(symbol, lookback_hours=SENTIMENT_LOOKBACK_HOURS) if symbol else {}
    sentiment_block: dict = {
        "score": sentiment.score,
        "reasons": sentiment.reasons,
        "aggregate_score": agg.get("aggregate_score", 0.0),
        "mention_count": agg.get("mention_count", 0),
        "sources": agg.get("sources") or [],
    }
    if agg.get("sample_headlines"):
        sentiment_block["sample_headlines"] = agg["sample_headlines"]
    return {
        "trend": {
            "score": trend.score,
            "reasons": trend.reasons,
            "trend_regime": trend_regime,  # 3.4-A: ADX trend regime detection
        },
        # Plan §Future: Indicator / Trend / Risk — momentum is the indicator domain (RSI/MACD).
        "indicator": indicator,
        "momentum": {"score": momentum.score, "reasons": momentum.reasons},
        # 3.1-A: Volume conviction domain.
        "volume": {"score": volume.score, "reasons": volume.reasons},
        "risk": risk,
        "anomaly": anomaly,
        "sentiment": sentiment_block,
    }


def _levels(row: pd.Series, signal: SignalType, price: float, df: pd.DataFrame, idx: int) -> dict:
    atr = row.get(atr_col(ATR_LEN))
    if atr is None or (isinstance(atr, float) and math.isnan(atr)):
        atr = 0.0
    atr = float(atr)
    levels: dict = {"entry_hint": price}
    if atr > 0:
        # 3.1-B: Regime-aware SL multiplier — wider in elevated vol (prevents whipsaw),
        # tighter in compressed vol (quick reversals need snug stops).
        risk = _risk_report(row, df, idx)
        regime = risk.get("atr_regime", "normal")
        sl_mult = {"elevated": 2.0, "compressed": 1.2, "normal": 1.5}.get(regime, 1.5)
        levels["stop_loss_distance"] = round(sl_mult * atr, 6)
        levels["sl_regime"] = regime  # expose for transparency in insight
        if signal == "BUY":
            levels["take_profit_price"] = round(price + 3.0 * atr, 6)
        elif signal == "SELL":
            levels["take_profit_price"] = round(price - 3.0 * atr, 6)
    return levels



def score_dataframe(
    df: pd.DataFrame,
    symbol: str,
    *,
    eval_index: int | None = None,
    expected_bar_time: int | None = None,
    timeframe: str = "1m",
) -> ChartAgentInsight | None:
    """Score the closed bar at eval_index (default: second-to-last row, bot convention)."""
    if df is None or df.empty or len(df) < 3:
        return None

    if expected_bar_time is not None:
        matched = find_bar_index(df, expected_bar_time)
        if matched is not None:
            idx = matched
        elif eval_index is not None:
            idx = eval_index
        else:
            return None
    else:
        idx = eval_index if eval_index is not None else len(df) - 2
    if idx < 1 or idx >= len(df):
        return None

    row = df.iloc[idx]
    prev = df.iloc[idx - 1]
    bar_time = row.get("time")
    if bar_time is None:
        return None

    price = float(row.get("close", 0))
    sub_reports = _build_sub_reports(row, prev, price, df, idx, symbol=symbol)
    trend_score = sub_reports["trend"]["score"]
    momentum_score = sub_reports["momentum"]["score"]
    volume_score = sub_reports["volume"]["score"]
    sentiment_score = sub_reports["sentiment"]["score"]
    # 3.1-A: Include volume conviction in total score.
    score = trend_score + momentum_score + volume_score + sentiment_score
    reasons = (
        sub_reports["trend"]["reasons"]
        + sub_reports["momentum"]["reasons"]
        + sub_reports["volume"]["reasons"]
        + sub_reports["sentiment"]["reasons"]
    )
    bot_sig = _bot_signal(score)

    return ChartAgentInsight(
        symbol=symbol,
        bar_time=coerce_bar_time(bar_time) or 0,
        timeframe=timeframe,
        signal=bot_sig,
        confidence=_confidence(score),  # 3.1-A: now sigmoid-mapped
        score=score,
        reasons=reasons,
        levels=_levels(row, bot_sig, price, df, idx),
        version=2,
        sub_reports=sub_reports,
    )


def score_at_index(df: pd.DataFrame, index: int, symbol: str) -> ChartAgentInsight | None:
    """Score a specific bar index (for backtest replay)."""
    return score_dataframe(df, symbol, eval_index=index)


def display_label(score: int) -> str:
    return _display_signal(score)


def macd_cross_label(row: pd.Series, prev: pd.Series | None) -> str:
    last_macd = row.get(MACD_COL)
    last_signal = row.get(MACD_SIGNAL_COL)
    if last_macd is None or last_signal is None:
        return "none"
    if (
        prev is not None
        and prev.get(MACD_COL) is not None
        and prev.get(MACD_SIGNAL_COL) is not None
        and float(last_macd) > float(last_signal)
        and float(prev.get(MACD_COL)) <= float(prev.get(MACD_SIGNAL_COL))
    ):
        return "bullish"
    if (
        prev is not None
        and prev.get(MACD_COL) is not None
        and prev.get(MACD_SIGNAL_COL) is not None
        and float(last_macd) < float(last_signal)
        and float(prev.get(MACD_COL)) >= float(prev.get(MACD_SIGNAL_COL))
    ):
        return "bearish"
    if float(last_macd) > float(last_signal):
        return "above"
    if float(last_macd) < float(last_signal):
        return "below"
    return "none"
