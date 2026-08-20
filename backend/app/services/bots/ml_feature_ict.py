"""ICT / Smart Money detectors shared by the ICT_SMC strategy and ML schema v8.

Pure functions of OHLC (+ ATR). Strategy wrappers keep the same df_row
``_prev`` column contract so live ICT behaviour is unchanged.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def impulse_mult(ob_lookback: int = 10) -> float:
    return 1.5 * max(0.75, min(2.5, 10.0 / max(1, int(ob_lookback))))


def detect_bullish_ob(
    close: float,
    open_: float,
    prev_close: float,
    prev_open: float,
    atr: float,
    ob_lookback: int = 10,
) -> bool:
    if not all((close, open_, prev_close, prev_open)) or atr <= 0:
        return False
    prior_bearish = prev_close < prev_open
    impulse = (close - open_) > impulse_mult(ob_lookback) * atr
    return bool(prior_bearish and impulse)


def detect_bearish_ob(
    close: float,
    open_: float,
    prev_close: float,
    prev_open: float,
    atr: float,
    ob_lookback: int = 10,
) -> bool:
    if not all((close, open_, prev_close, prev_open)) or atr <= 0:
        return False
    prior_bullish = prev_close > prev_open
    impulse = (open_ - close) > impulse_mult(ob_lookback) * atr
    return bool(prior_bullish and impulse)


def detect_bullish_fvg(low: float, prev2_high: float, min_gap_pct: float = 0.0005) -> bool:
    if prev2_high is None or prev2_high <= 0 or low <= 0:
        return False
    gap = low - prev2_high
    return gap > prev2_high * float(min_gap_pct)


def detect_bearish_fvg(high: float, prev2_low: float, min_gap_pct: float = 0.0005) -> bool:
    if prev2_low is None or prev2_low <= 0 or high <= 0:
        return False
    gap = prev2_low - high
    return gap > high * float(min_gap_pct)


def detect_sweep_low(low: float, close: float, rolling_low: float) -> bool:
    if rolling_low is None or rolling_low <= 0:
        return False
    return low < rolling_low and close > rolling_low


def detect_sweep_high(high: float, close: float, rolling_high: float) -> bool:
    if rolling_high is None or rolling_high <= 0:
        return False
    return high > rolling_high and close < rolling_high


ICT_FEATURE_NAMES: tuple[str, ...] = (
    "dist_to_ob_atr",
    "in_fvg",
    "sweep_reclaim",
    "ob_age_norm",
)


def ict_feature_matrix(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr: np.ndarray,
    *,
    ob_lookback: int = 10,
    sweep_lookback: int = 20,
    fvg_min_gap_pct: float = 0.0005,
) -> dict[str, np.ndarray]:
    """Vectorized ICT features from OHLC arrays (prior-only rolling extrema)."""
    n = len(close)
    dist = np.zeros(n, dtype=np.float64)
    in_fvg = np.zeros(n, dtype=np.float64)
    sweep = np.zeros(n, dtype=np.float64)
    ob_age = np.ones(n, dtype=np.float64)
    if n == 0:
        return {
            "dist_to_ob_atr": dist,
            "in_fvg": in_fvg,
            "sweep_reclaim": sweep,
            "ob_age_norm": ob_age,
        }

    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    a = np.asarray(atr, dtype=np.float64)
    if a.shape[0] != n:
        a = np.zeros(n, dtype=np.float64)

    last_ob_mid = 0.0
    last_ob_i = -10**9
    have_ob = False
    slb = max(2, int(sweep_lookback))
    olb = max(1, int(ob_lookback))

    for i in range(n):
        atr_i = float(a[i]) if a[i] > 0 else 0.0
        if i >= 1 and atr_i > 0:
            pc, po = float(c[i - 1]), float(o[i - 1])
            cc, oo = float(c[i]), float(o[i])
            if detect_bullish_ob(cc, oo, pc, po, atr_i, olb) or detect_bearish_ob(
                cc, oo, pc, po, atr_i, olb
            ):
                last_ob_mid = 0.5 * (float(h[i - 1]) + float(l[i - 1]))
                last_ob_i = i
                have_ob = True

        if have_ob and atr_i > 1e-12:
            dist[i] = (float(c[i]) - last_ob_mid) / atr_i
            ob_age[i] = min(1.0, (i - last_ob_i) / float(olb))
        elif have_ob:
            ob_age[i] = min(1.0, (i - last_ob_i) / float(olb))

        if i >= 2:
            p2h = float(h[i - 2])
            p2l = float(l[i - 2])
            if detect_bullish_fvg(float(l[i]), p2h, fvg_min_gap_pct):
                in_fvg[i] = 1.0
            elif detect_bearish_fvg(float(h[i]), p2l, fvg_min_gap_pct):
                in_fvg[i] = -1.0

        start = max(0, i - slb)
        if i > start:
            roll_high = float(np.max(h[start:i]))
            roll_low = float(np.min(l[start:i]))
            if detect_sweep_low(float(l[i]), float(c[i]), roll_low):
                sweep[i] = 1.0
            elif detect_sweep_high(float(h[i]), float(c[i]), roll_high):
                sweep[i] = -1.0

    np.nan_to_num(dist, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    return {
        "dist_to_ob_atr": dist,
        "in_fvg": in_fvg,
        "sweep_reclaim": sweep,
        "ob_age_norm": ob_age,
    }
