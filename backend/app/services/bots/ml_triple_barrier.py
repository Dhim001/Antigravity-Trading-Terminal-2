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

    # Compute AFML sample uniqueness over label holding spans
    uniqueness = compute_sample_uniqueness(results, n)
    for i, res in enumerate(results):
        res["uniqueness"] = uniqueness[i] if i < len(uniqueness) else 1.0

    return results


def compute_sample_uniqueness(labels: list[dict], total_bars: int) -> list[float]:
    """Compute sample uniqueness for triple-barrier labels (AFML Ch. 4).

    Measures average uniqueness of each sample's label holding interval [t0, t1].
    Overlapping label intervals reduce sample uniqueness.
    """
    if not labels or total_bars <= 0:
        return []

    # Step 1: Compute concurrent label count c_t for each bar t
    c_t = [0] * total_bars
    intervals = []

    for item in labels:
        idx = int(item.get("index", 0))
        holding = int(item.get("bars_held", 1))
        t0 = max(0, min(total_bars - 1, idx))
        t1 = max(t0, min(total_bars - 1, idx + max(1, holding)))
        intervals.append((t0, t1))
        for t in range(t0, t1 + 1):
            c_t[t] += 1

    # Step 2: Average uniqueness u_i = avg_{t in [t0, t1]} (1 / c_t)
    uniqueness: list[float] = []
    for t0, t1 in intervals:
        span_len = t1 - t0 + 1
        if span_len <= 0:
            uniqueness.append(1.0)
            continue
        u_sum = sum(1.0 / c_t[t] for t in range(t0, t1 + 1) if c_t[t] > 0)
        u_avg = u_sum / span_len
        uniqueness.append(round(u_avg, 4))

    return uniqueness


def label_contamination_mask(labels: list[dict], cutoff_index: int) -> list[bool]:
    """Identify labels whose holding interval extends past a cutoff index.

    Returns a boolean list where True indicates the sample's label outcome
    depends on price path data past ``cutoff_index`` (i.e. contaminated for IS).
    """
    mask = []
    for item in labels:
        idx = item.get("index", 0)
        holding = item.get("bars_held", 1)
        t1 = idx + holding
        mask.append(t1 > cutoff_index)
    return mask


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


# ── Phase 2.4: config resolver + asymmetric barrier support ─────────────────


def resolve_barrier_multipliers(config: dict | None) -> tuple[float, float]:
    """Resolve (upper, lower) ATR multipliers from config.

    Supports asymmetric barriers via ``triple_barrier_atr_mult_upper`` /
    ``triple_barrier_atr_mult_lower``. Falls back to the symmetric
    ``triple_barrier_atr_mult`` knob for backward compatibility.
    """
    cfg = config if isinstance(config, dict) else {}
    sym = float(cfg.get("triple_barrier_atr_mult") or 2.0)
    upper = float(cfg.get("triple_barrier_atr_mult_upper") or sym)
    lower = float(cfg.get("triple_barrier_atr_mult_lower") or sym)
    # Sanity: barriers must be positive and not degenerate.
    upper = max(0.1, min(20.0, upper))
    lower = max(0.1, min(20.0, lower))
    return upper, lower


def label_triple_barrier_from_config(
    candles: list[dict],
    config: dict | None,
) -> list[dict]:
    """Convenience wrapper that pulls ATR mult + horizon from config."""
    cfg = config if isinstance(config, dict) else {}
    upper, lower = resolve_barrier_multipliers(cfg)
    max_bars = max(1, int(cfg.get("triple_barrier_max_bars") or 30))
    return label_triple_barrier(
        candles,
        atr_mult_upper=upper,
        atr_mult_lower=lower,
        max_holding_bars=max_bars,
    )

