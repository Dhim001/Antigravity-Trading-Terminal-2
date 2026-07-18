"""Triple-barrier labelling for ML signal training data.

Implements the labelling method from *Advances in Financial Machine Learning*
(de Prado, 2018).  Each bar is labelled based on which price barrier is touched
first after entry:

  - Upper barrier (price rises by k × ATR)  → BUY  (label = 1)
  - Lower barrier (price falls by k × ATR)  → SELL (label = -1)
  - Time barrier  (neither hit in N bars)    → NONE (label = 0)
"""

from __future__ import annotations

import math
from typing import Any


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def label_triple_barrier(
    candles: list[dict],
    *,
    atr_mult_upper: float = 2.0,
    atr_mult_lower: float = 2.0,
    max_holding_bars: int = 30,
    atr_column: str = "ATR_14",
) -> list[dict]:
    """Assign triple-barrier labels to each candle in the series.

    Parameters
    ----------
    candles : list[dict]
        List of candle dicts with at least ``close``, ``high``, ``low``,
        and the ATR column present.  Should be sorted oldest-first.
    atr_mult_upper : float
        ATR multiplier for the upper (profit) barrier.
    atr_mult_lower : float
        ATR multiplier for the lower (stop) barrier.
    max_holding_bars : int
        Maximum bars before the time barrier triggers.
    atr_column : str
        Name of the ATR column in each candle dict.

    Returns
    -------
    list[dict]
        Each entry contains:
        - ``index``: position in the input list
        - ``time``: bar timestamp
        - ``label``: 1 (BUY), -1 (SELL), or 0 (NONE)
        - ``label_name``: "BUY", "SELL", or "NONE"
        - ``barrier_hit``: "upper", "lower", or "time"
        - ``entry_price``: close of the labelled bar
        - ``atr``: ATR value used for barrier widths
        - ``bars_held``: how many bars until barrier was hit
        - ``exit_price``: price at which barrier was hit
    """
    n = len(candles)
    results: list[dict] = []

    for i in range(n):
        candle = candles[i]
        entry_price = _safe_float(candle.get("close"))
        atr = _safe_float(candle.get(atr_column) or candle.get("ATRr_14"))
        bar_time = candle.get("time")

        if entry_price <= 0 or atr <= 0:
            results.append({
                "index": i,
                "time": bar_time,
                "label": 0,
                "label_name": "NONE",
                "barrier_hit": "invalid",
                "entry_price": entry_price,
                "atr": atr,
                "bars_held": 0,
                "exit_price": entry_price,
            })
            continue

        upper_barrier = entry_price + atr * atr_mult_upper
        lower_barrier = entry_price - atr * atr_mult_lower

        label = 0
        label_name = "NONE"
        barrier_hit = "time"
        bars_held = max_holding_bars
        exit_price = entry_price

        # Walk forward through future bars
        for j in range(i + 1, min(i + 1 + max_holding_bars, n)):
            future = candles[j]
            future_high = _safe_float(future.get("high"))
            future_low = _safe_float(future.get("low"))
            future_close = _safe_float(future.get("close"))

            # Check upper barrier (bullish outcome)
            if future_high >= upper_barrier:
                label = 1
                label_name = "BUY"
                barrier_hit = "upper"
                bars_held = j - i
                exit_price = upper_barrier
                break

            # Check lower barrier (bearish outcome)
            if future_low <= lower_barrier:
                label = -1
                label_name = "SELL"
                barrier_hit = "lower"
                bars_held = j - i
                exit_price = lower_barrier
                break

            # If last bar in horizon, use close as exit
            if j == min(i + max_holding_bars, n - 1):
                bars_held = j - i
                exit_price = future_close

        results.append({
            "index": i,
            "time": bar_time,
            "label": label,
            "label_name": label_name,
            "barrier_hit": barrier_hit,
            "entry_price": entry_price,
            "atr": atr,
            "bars_held": bars_held,
            "exit_price": exit_price,
        })

    return results


def label_distribution(labels: list[dict]) -> dict[str, int]:
    """Count label distribution for diagnostics."""
    counts = {"BUY": 0, "SELL": 0, "NONE": 0, "invalid": 0}
    for item in labels:
        name = item.get("label_name", "NONE")
        if name in counts:
            counts[name] += 1
        else:
            counts["invalid"] += 1
    return counts
