"""Rule-based chart signal engine — parity with frontend generateSignal()."""

from __future__ import annotations

import math

import pandas as pd

from app.services.agent.models import ChartAgentInsight, SignalType
from app.services.bots.indicators import atr_col

RSI_LEN = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
MACD_COL = f"MACD_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"
MACD_SIGNAL_COL = f"MACDs_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"
EMA_COLS = ("EMA_9", "EMA_21", "EMA_50")
ATR_LEN = 14


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
    return round(min(1.0, max(0.0, abs(score) / 4.0)), 3)


def _score_row(
    row: pd.Series,
    prev: pd.Series | None,
    price: float,
) -> tuple[int, list[str]]:
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

    return score, reasons


def _levels(row: pd.Series, signal: SignalType, price: float) -> dict:
    atr = row.get(atr_col(ATR_LEN))
    if atr is None or (isinstance(atr, float) and math.isnan(atr)):
        atr = 0.0
    atr = float(atr)
    levels: dict = {"entry_hint": price}
    if atr > 0:
        levels["stop_loss_distance"] = round(1.5 * atr, 6)
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
    timeframe: str = "1m",
) -> ChartAgentInsight | None:
    """Score the closed bar at eval_index (default: second-to-last row, bot convention)."""
    if df is None or df.empty or len(df) < 3:
        return None

    idx = eval_index if eval_index is not None else len(df) - 2
    if idx < 1 or idx >= len(df):
        return None

    row = df.iloc[idx]
    prev = df.iloc[idx - 1]
    bar_time = row.get("time")
    if bar_time is None:
        return None

    price = float(row.get("close", 0))
    score, reasons = _score_row(row, prev, price)
    bot_sig = _bot_signal(score)

    return ChartAgentInsight(
        symbol=symbol,
        bar_time=int(bar_time),
        timeframe=timeframe,
        signal=bot_sig,
        confidence=_confidence(score),
        score=score,
        reasons=reasons,
        levels=_levels(row, bot_sig, price),
    )


def score_at_index(df: pd.DataFrame, index: int, symbol: str) -> ChartAgentInsight | None:
    """Score a specific bar index (for backtest replay)."""
    return score_dataframe(df, symbol, eval_index=index)


def display_label(score: int) -> str:
    return _display_signal(score)
