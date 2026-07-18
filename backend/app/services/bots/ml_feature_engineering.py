"""ML feature engineering — expanded features for XGBoost signal classifier.

Extracts 34 numeric features from a prepared indicator row (pandas Series or dict)
for use with the ML_SIGNAL_BOOST strategy.  Designed to work with the same df_row
format that all BaseStrategy.evaluate() methods receive.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import numpy as np


# ── Feature schema ────────────────────────────────────────────────────────

SIGNAL_FEATURE_VERSION = 1

SIGNAL_FEATURE_NAMES: tuple[str, ...] = (
    # Price action (4)
    "returns_1",
    "returns_5",
    "returns_15",
    "log_return",
    # Volatility (3)
    "atr_ratio",
    "bb_width",
    "rolling_vol_20",
    # Momentum (4)
    "rsi_14",
    "macd_hist",
    "stoch_k",
    "adx",
    # Volume (2)
    "volume_ratio",
    "obv_slope",
    # Trend (3)
    "ema_cross_9_21",
    "price_vs_vwap",
    "supertrend_dir",
    # Regime (4)
    "atr_elevated",
    "atr_compressed",
    "trend_trending",
    "trend_ranging",
    # Cyclical time (4)
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    # Candle shape (4)
    "high_low_range",
    "body_ratio",
    "upper_shadow",
    "lower_shadow",
    # Rolling z-scores (2)
    "close_z_20",
    "volume_z_20",
    # Pattern (2)
    "consecutive_up",
    "consecutive_down",
    # Composite (2)
    "is_buy_bias",
    "momentum_alignment",
)


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


def _cyclical(value: float, period: float) -> tuple[float, float]:
    if period <= 0:
        return 0.0, 0.0
    angle = 2.0 * math.pi * value / period
    return math.sin(angle), math.cos(angle)


def _parse_bar_time(row: dict) -> datetime | None:
    """Extract a datetime from the bar's time field."""
    raw = row.get("time")
    if raw is None:
        return None
    try:
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def bar_to_signal_features(df_row, *, lookback_rows: list | None = None) -> dict[str, float]:
    """Extract 34 ML features from a single indicator-enriched bar row.

    Parameters
    ----------
    df_row : dict-like
        The current bar row with indicators attached (same as BaseStrategy.evaluate receives).
    lookback_rows : list, optional
        Previous bar rows for computing rolling features.  If None, rolling/lag
        features default to 0.

    Returns
    -------
    dict[str, float]
        Feature dict keyed by SIGNAL_FEATURE_NAMES.
    """
    close = _safe_float(df_row.get("close"))
    open_ = _safe_float(df_row.get("open"))
    high = _safe_float(df_row.get("high"))
    low = _safe_float(df_row.get("low"))
    volume = _safe_float(df_row.get("volume"))

    # Lookback closes/volumes for lag features
    lb = lookback_rows or []
    prev_closes = [_safe_float(r.get("close")) for r in lb]
    prev_volumes = [_safe_float(r.get("volume")) for r in lb]

    # ── Price action ──────────────────────────────────────────────────
    close_1 = prev_closes[-1] if len(prev_closes) >= 1 else close
    close_5 = prev_closes[-5] if len(prev_closes) >= 5 else close
    close_15 = prev_closes[-15] if len(prev_closes) >= 15 else close

    returns_1 = (close - close_1) / close_1 if close_1 > 0 else 0.0
    returns_5 = (close - close_5) / close_5 if close_5 > 0 else 0.0
    returns_15 = (close - close_15) / close_15 if close_15 > 0 else 0.0
    log_return = math.log(close / close_1) if close > 0 and close_1 > 0 else 0.0

    # ── Volatility ────────────────────────────────────────────────────
    atr = _safe_float(df_row.get("ATR_14") or df_row.get("ATRr_14"))
    atr_ratio = atr / close if close > 0 and atr > 0 else 0.0

    bb_upper = _safe_float(df_row.get("BBU_20_2.0"))
    bb_lower = _safe_float(df_row.get("BBL_20_2.0"))
    bb_mid = _safe_float(df_row.get("BBM_20_2.0"))
    bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0.0

    # Rolling 20-bar volatility from lookback
    if len(prev_closes) >= 20:
        recent_20 = prev_closes[-20:] + [close]
        rets = [
            (recent_20[i] - recent_20[i - 1]) / recent_20[i - 1]
            for i in range(1, len(recent_20))
            if recent_20[i - 1] > 0
        ]
        rolling_vol_20 = float(np.std(rets)) if rets else 0.0
    else:
        rolling_vol_20 = atr_ratio  # fallback

    # ── Momentum ──────────────────────────────────────────────────────
    rsi_14 = _safe_float(df_row.get("RSI_14"), 50.0) / 100.0  # normalize to [0, 1]

    macd_hist_raw = _safe_float(df_row.get("MACDh_12_26_9"))
    macd_hist = macd_hist_raw / close if close > 0 else 0.0  # normalize by price

    stoch_k = _safe_float(df_row.get("STOCHk_14_3_3"), 50.0) / 100.0

    adx_val = _safe_float(df_row.get("ADX_14"), 20.0) / 100.0

    # ── Volume ────────────────────────────────────────────────────────
    if len(prev_volumes) >= 20:
        avg_vol_20 = sum(prev_volumes[-20:]) / 20.0
        volume_ratio = volume / avg_vol_20 if avg_vol_20 > 0 else 1.0
    else:
        volume_ratio = 1.0

    # OBV slope (approximate: direction-weighted volume over last 5 bars)
    obv_slope = 0.0
    if len(prev_closes) >= 5 and len(prev_volumes) >= 5:
        obv_changes = []
        all_c = prev_closes[-5:] + [close]
        all_v = prev_volumes[-5:] + [volume]
        for i in range(1, len(all_c)):
            direction = 1.0 if all_c[i] > all_c[i - 1] else -1.0 if all_c[i] < all_c[i - 1] else 0.0
            obv_changes.append(direction * all_v[i])
        obv_slope = sum(obv_changes) / (max(1.0, sum(abs(v) for v in obv_changes)) or 1.0)

    # ── Trend ─────────────────────────────────────────────────────────
    ema_9 = _safe_float(df_row.get("EMA_9"))
    ema_21 = _safe_float(df_row.get("EMA_21"))
    ema_cross_9_21 = (ema_9 - ema_21) / close if close > 0 and ema_9 > 0 and ema_21 > 0 else 0.0

    vwap = _safe_float(df_row.get("VWAP"))
    price_vs_vwap = (close - vwap) / close if close > 0 and vwap > 0 else 0.0

    st_dir = _safe_float(df_row.get("SUPERTd_14_3.0"), 0.0)
    supertrend_dir = 1.0 if st_dir > 0 else -1.0 if st_dir < 0 else 0.0

    # ── Regime ────────────────────────────────────────────────────────
    atr_median = _safe_float(df_row.get("ATR_14_median_20"))
    atr_regime_ratio = atr / atr_median if atr_median > 0 and atr > 0 else 1.0
    atr_elevated = 1.0 if atr_regime_ratio >= 1.5 else 0.0
    atr_compressed = 1.0 if atr_regime_ratio <= 0.6 else 0.0

    adx_raw = _safe_float(df_row.get("ADX_14"), 20.0)
    trend_trending = 1.0 if adx_raw >= 25.0 else 0.0
    trend_ranging = 1.0 if adx_raw < 20.0 else 0.0

    # ── Cyclical time ─────────────────────────────────────────────────
    dt = _parse_bar_time(df_row)
    hour = dt.hour if dt else 12
    dow = dt.weekday() if dt else 2
    hour_sin, hour_cos = _cyclical(hour, 24.0)
    dow_sin, dow_cos = _cyclical(dow, 7.0)

    # ── Candle shape ──────────────────────────────────────────────────
    hl_range = high - low
    high_low_range = hl_range / close if close > 0 else 0.0

    body = abs(close - open_)
    body_ratio = body / hl_range if hl_range > 0 else 0.0

    if close >= open_:
        upper_shadow = (high - close) / hl_range if hl_range > 0 else 0.0
        lower_shadow = (open_ - low) / hl_range if hl_range > 0 else 0.0
    else:
        upper_shadow = (high - open_) / hl_range if hl_range > 0 else 0.0
        lower_shadow = (close - low) / hl_range if hl_range > 0 else 0.0

    # ── Rolling z-scores ──────────────────────────────────────────────
    if len(prev_closes) >= 20:
        window_20 = prev_closes[-20:]
        mean_c = sum(window_20) / 20.0
        std_c = float(np.std(window_20))
        close_z_20 = (close - mean_c) / std_c if std_c > 0 else 0.0
    else:
        close_z_20 = 0.0

    if len(prev_volumes) >= 20:
        vol_20 = prev_volumes[-20:]
        mean_v = sum(vol_20) / 20.0
        std_v = float(np.std(vol_20))
        volume_z_20 = (volume - mean_v) / std_v if std_v > 0 else 0.0
    else:
        volume_z_20 = 0.0

    # ── Pattern ───────────────────────────────────────────────────────
    consecutive_up = 0.0
    consecutive_down = 0.0
    if prev_closes:
        all_closes = prev_closes + [close]
        count = 0
        for i in range(len(all_closes) - 1, 0, -1):
            if all_closes[i] > all_closes[i - 1]:
                if count >= 0:
                    count += 1
                else:
                    break
            elif all_closes[i] < all_closes[i - 1]:
                if count <= 0:
                    count -= 1
                else:
                    break
            else:
                break
        consecutive_up = max(0.0, float(count)) / 10.0   # normalize
        consecutive_down = max(0.0, float(-count)) / 10.0

    # ── Composite ─────────────────────────────────────────────────────
    # Buy bias: positive when momentum + trend + mean-reversion align bullishly
    rsi_raw = _safe_float(df_row.get("RSI_14"), 50.0)
    is_buy_bias = 1.0 if (rsi_raw < 50 and ema_cross_9_21 > 0) else (
        -1.0 if (rsi_raw > 50 and ema_cross_9_21 < 0) else 0.0
    )

    # Momentum alignment: how many momentum indicators agree on direction
    bullish_count = sum([
        1.0 if macd_hist_raw > 0 else 0.0,
        1.0 if rsi_raw < 40 else 0.0,  # oversold = reversal buy potential
        1.0 if stoch_k < 0.3 else 0.0,
        1.0 if supertrend_dir > 0 else 0.0,
    ])
    bearish_count = sum([
        1.0 if macd_hist_raw < 0 else 0.0,
        1.0 if rsi_raw > 60 else 0.0,
        1.0 if stoch_k > 0.7 else 0.0,
        1.0 if supertrend_dir < 0 else 0.0,
    ])
    momentum_alignment = (bullish_count - bearish_count) / 4.0  # [-1, +1]

    return {
        "returns_1": returns_1,
        "returns_5": returns_5,
        "returns_15": returns_15,
        "log_return": log_return,
        "atr_ratio": atr_ratio,
        "bb_width": bb_width,
        "rolling_vol_20": rolling_vol_20,
        "rsi_14": rsi_14,
        "macd_hist": macd_hist,
        "stoch_k": stoch_k,
        "adx": adx_val,
        "volume_ratio": volume_ratio,
        "obv_slope": obv_slope,
        "ema_cross_9_21": ema_cross_9_21,
        "price_vs_vwap": price_vs_vwap,
        "supertrend_dir": supertrend_dir,
        "atr_elevated": atr_elevated,
        "atr_compressed": atr_compressed,
        "trend_trending": trend_trending,
        "trend_ranging": trend_ranging,
        "hour_sin": hour_sin,
        "hour_cos": hour_cos,
        "dow_sin": dow_sin,
        "dow_cos": dow_cos,
        "high_low_range": high_low_range,
        "body_ratio": body_ratio,
        "upper_shadow": upper_shadow,
        "lower_shadow": lower_shadow,
        "close_z_20": close_z_20,
        "volume_z_20": volume_z_20,
        "consecutive_up": consecutive_up,
        "consecutive_down": consecutive_down,
        "is_buy_bias": is_buy_bias,
        "momentum_alignment": momentum_alignment,
    }


def signal_features_to_vector(features: dict[str, float]) -> np.ndarray:
    """Convert feature dict to a numpy vector in canonical order."""
    return np.array(
        [float(features.get(name, 0.0)) for name in SIGNAL_FEATURE_NAMES],
        dtype=np.float64,
    )
