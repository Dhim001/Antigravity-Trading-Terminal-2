"""ML feature engineering — expanded features for HistGBM signal classifier.

Extracts numeric features from a prepared indicator row (pandas Series or dict)
for use with the ML_SIGNAL_BOOST strategy (HistGradientBoostingClassifier).
Designed to work with the same df_row format that all BaseStrategy.evaluate()
methods receive.

Research / backtest: prefer :func:`compute_signal_feature_matrix_vectorized`
(``BACKTEST_VECTORIZED_FEATURES=true``, default on). Live ``evaluate()`` keeps
per-bar :func:`bar_to_signal_features`.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ── Feature schema ────────────────────────────────────────────────────────

SIGNAL_FEATURE_VERSION = 4

# Opt-in trade-state features (schema v5). Existing models keep v4 dims.
# Enable with config ``ml_include_trade_state`` and retrain — do not mix dims.
TRADE_STATE_FEATURE_VERSION = 5
TRADE_STATE_FEATURE_NAMES: tuple[str, ...] = (
    "bot_loss_streak",
    "bot_win_rate_24h",
    "hours_since_last_loss",
)

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
    # Volume (3)
    "volume_ratio",
    "obv_slope",
    "volume_momentum",
    # Trend (3)
    "ema_cross_9_21",
    "price_vs_vwap",
    "supertrend_dir",
    # Regime (4)
    "atr_elevated",
    "atr_compressed",
    "trend_trending",
    "trend_ranging",
    # Cyclical time (4) — UTC
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    # Equity session (4) — crypto: is_rth=1, others neutral
    "is_rth",
    "minutes_from_open_norm",
    "et_hour_sin",
    "et_hour_cos",
    # Candle shape (5)
    "high_low_range",
    "body_ratio",
    "upper_shadow",
    "lower_shadow",
    "spread_ratio",
    # Rolling z-scores (2)
    "close_z_20",
    "volume_z_20",
    # Pattern (2)
    "consecutive_up",
    "consecutive_down",
    # Phase 3.7: Microstructure (3)
    "cvd_z",
    "cvd_slope",
    "vpin",
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


def bar_to_signal_features(
    df_row,
    *,
    lookback_rows: list | None = None,
    symbol: str | None = None,
) -> dict[str, float]:
    """Extract ML features from a single indicator-enriched bar row.

    Parameters
    ----------
    df_row : dict-like
        The current bar row with indicators attached (same as BaseStrategy.evaluate receives).
    lookback_rows : list, optional
        Previous bar rows for computing rolling features.  If None, rolling/lag
        features default to 0.
    symbol : str, optional
        Instrument for equity session features. Falls back to ``_symbol`` / ``symbol``
        on the row. Crypto → always-open session defaults.

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
    volume_momentum = 0.0
    if len(prev_closes) >= 5 and len(prev_volumes) >= 5:
        obv_changes = []
        all_c = prev_closes[-5:] + [close]
        all_v = prev_volumes[-5:] + [volume]
        for i in range(1, len(all_c)):
            direction = 1.0 if all_c[i] > all_c[i - 1] else -1.0 if all_c[i] < all_c[i - 1] else 0.0
            obv_changes.append(direction * all_v[i])
        obv_slope = sum(obv_changes) / (max(1.0, sum(abs(v) for v in obv_changes)) or 1.0)
        v5_avg = sum(prev_volumes[-5:]) / 5.0
        volume_momentum = (volume - v5_avg) / v5_avg if v5_avg > 0 else 0.0

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

    # ── Cyclical time (UTC) + equity session (ET) ──────────────────────
    dt = _parse_bar_time(df_row)
    hour = dt.hour if dt else 12
    dow = dt.weekday() if dt else 2
    hour_sin, hour_cos = _cyclical(hour, 24.0)
    dow_sin, dow_cos = _cyclical(dow, 7.0)

    sym = symbol or df_row.get("_symbol") or df_row.get("symbol") or ""
    raw_ts = df_row.get("time")
    try:
        from app.services.altdata.calendar import session_features_for_bar

        sess = session_features_for_bar(str(sym) if sym else None, raw_ts)
    except Exception:
        sess = {
            "is_rth": 1.0,
            "minutes_from_open_norm": 0.0,
            "et_hour_sin": 0.0,
            "et_hour_cos": 1.0,
        }
    is_rth = float(sess.get("is_rth", 1.0))
    minutes_from_open_norm = float(sess.get("minutes_from_open_norm", 0.0))
    et_hour_sin = float(sess.get("et_hour_sin", 0.0))
    et_hour_cos = float(sess.get("et_hour_cos", 1.0))

    # ── Candle shape ──────────────────────────────────────────────────
    hl_range = high - low
    high_low_range = hl_range / close if close > 0 else 0.0
    spread_ratio = hl_range / open_ if open_ > 0 else 0.0

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

    # ── Phase 3.7: Microstructure (CVD + VPIN) ───────────────────────
    # Replay the lookback bars through the trackers, then update with the
    # current bar. Falls back to 0 when no lookback is provided (cold start).
    cvd_z = 0.0
    cvd_slope = 0.0
    vpin = 0.0
    if lb:
        try:
            from app.services.bots.microstructure_features import CVDTracker, VPINTracker

            cvd_t = CVDTracker(lookback=max(20, len(lb)))
            vpin_t = VPINTracker(n_buckets=50)
            for r in lb:
                cvd_t.update_bar(
                    open_=_safe_float(r.get("open")),
                    close=_safe_float(r.get("close")),
                    high=_safe_float(r.get("high")),
                    low=_safe_float(r.get("low")),
                    volume=_safe_float(r.get("volume")),
                )
                vpin_t.update_bar(
                    open_=_safe_float(r.get("open")),
                    close=_safe_float(r.get("close")),
                    high=_safe_float(r.get("high")),
                    low=_safe_float(r.get("low")),
                    volume=_safe_float(r.get("volume")),
                )
            cvd_t.update_bar(open_=open_, close=close, high=high, low=low, volume=volume)
            vpin_t.update_bar(open_=open_, close=close, high=high, low=low, volume=volume)
            cvd_z = cvd_t.cvd_z
            cvd_slope = cvd_t.cvd_slope
            vpin = vpin_t.vpin
        except Exception:
            pass

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
        "volume_momentum": volume_momentum,
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
        "is_rth": is_rth,
        "minutes_from_open_norm": minutes_from_open_norm,
        "et_hour_sin": et_hour_sin,
        "et_hour_cos": et_hour_cos,
        "high_low_range": high_low_range,
        "body_ratio": body_ratio,
        "upper_shadow": upper_shadow,
        "lower_shadow": lower_shadow,
        "spread_ratio": spread_ratio,
        "close_z_20": close_z_20,
        "volume_z_20": volume_z_20,
        "consecutive_up": consecutive_up,
        "consecutive_down": consecutive_down,
        "cvd_z": cvd_z,
        "cvd_slope": cvd_slope,
        "vpin": vpin,
    }


# Strategy evaluate() keeps ``deque(maxlen=25)`` and passes ``list(hist)[:-1]``
# (up to 24 prior bars). Batch/train feature matrices must use the same window
# or ``consecutive_up`` / CVD features diverge from per-bar evaluate.
EVAL_FEATURE_LOOKBACK = 24


def vectorized_features_enabled() -> bool:
    """Research/backtest columnar path (default on)."""
    return os.environ.get("BACKTEST_VECTORIZED_FEATURES", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _col_from_rows(
    candles: Any,
    key: str,
    *,
    default: float = 0.0,
    n: int | None = None,
) -> np.ndarray:
    """Extract a float column from list[dict] or DataFrame."""
    if hasattr(candles, "columns"):
        if key in candles.columns:
            arr = np.asarray(candles[key].to_numpy(), dtype=np.float64)
        else:
            arr = np.full(len(candles), default, dtype=np.float64)
        arr = np.nan_to_num(arr, nan=default, posinf=default, neginf=default)
        return arr
    nn = int(n if n is not None else len(candles))
    out = np.empty(nn, dtype=np.float64)
    for i in range(nn):
        row = candles[i]
        if hasattr(row, "get"):
            out[i] = _safe_float(row.get(key), default)
        else:
            out[i] = default
    return out


def _times_from_rows(candles: Any, n: int) -> np.ndarray:
    if hasattr(candles, "columns") and "time" in candles.columns:
        raw = candles["time"].to_numpy()
        out = np.empty(n, dtype=np.float64)
        for i in range(n):
            try:
                out[i] = float(raw[i])
            except (TypeError, ValueError):
                out[i] = np.nan
        return out
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        row = candles[i]
        raw = row.get("time") if hasattr(row, "get") else None
        try:
            out[i] = float(raw) if raw is not None else np.nan
        except (TypeError, ValueError):
            out[i] = np.nan
    return out


def _symbol_from_rows(candles: Any) -> str:
    if hasattr(candles, "columns"):
        for key in ("_symbol", "symbol"):
            if key in candles.columns and len(candles):
                try:
                    return str(candles[key].iloc[-1] or "")
                except Exception:
                    pass
        return ""
    if not candles:
        return ""
    row = candles[-1]
    if hasattr(row, "get"):
        return str(row.get("_symbol") or row.get("symbol") or "")
    return ""


def _rolling_mean_prior(x: np.ndarray, window: int) -> np.ndarray:
    """Mean of the previous ``window`` samples (excludes current)."""
    n = len(x)
    out = np.zeros(n, dtype=np.float64)
    if n == 0 or window <= 0:
        return out
    csum = np.concatenate([[0.0], np.cumsum(x)])
    for i in range(n):
        start = max(0, i - window)
        count = i - start
        if count > 0:
            out[i] = (csum[i] - csum[start]) / count
    return out


def _rolling_std_prior(x: np.ndarray, window: int) -> np.ndarray:
    """Population std of the previous ``window`` samples (excludes current)."""
    n = len(x)
    out = np.zeros(n, dtype=np.float64)
    if n == 0 or window <= 0:
        return out
    for i in range(n):
        start = max(0, i - window)
        if i - start < 1:
            continue
        seg = x[start:i]
        out[i] = float(np.std(seg)) if len(seg) else 0.0
    return out


def _consecutive_streaks(close: np.ndarray, flb: int) -> tuple[np.ndarray, np.ndarray]:
    """Match bar_to_signal_features consecutive_* with capped prior lookback."""
    n = len(close)
    up = np.zeros(n, dtype=np.float64)
    down = np.zeros(n, dtype=np.float64)
    for j in range(n):
        start = max(0, j - flb)
        # all_closes = priors[start:j] + [close[j]]
        count = 0
        for i in range(j, start, -1):
            if close[i] > close[i - 1]:
                if count >= 0:
                    count += 1
                else:
                    break
            elif close[i] < close[i - 1]:
                if count <= 0:
                    count -= 1
                else:
                    break
            else:
                break
        up[j] = max(0.0, float(count)) / 10.0
        down[j] = max(0.0, float(-count)) / 10.0
    return up, down


def _safe_progress(progress_cb: Any | None, done: int, total: int) -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(done, total)
    except Exception:
        pass


def _microstructure_windowed_python(
    open_: np.ndarray,
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    flb: int,
    *,
    cancel_cb: Any | None = None,
    progress_cb: Any | None = None,
    progress_base: int = 0,
    progress_total: int | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-bar CVD/VPIN with tracker reset over [j-flb, j] (evaluate parity)."""
    import time

    from app.services.bots.microstructure_features import CVDTracker, VPINTracker

    n = len(close)
    cvd_z = np.zeros(n, dtype=np.float64)
    cvd_slope = np.zeros(n, dtype=np.float64)
    vpin = np.zeros(n, dtype=np.float64)
    tot = max(1, int(progress_total if progress_total is not None else n))
    report_every = max(512, n // 20) if n else 512
    last_prog_t = 0.0
    for j in range(n):
        if cancel_cb is not None and j % 512 == 0 and cancel_cb():
            raise InterruptedError("ml_cancel_requested")
        now = time.monotonic()
        # Stride *or* wall-clock: pure-Python O(n·lookback) can hold the GIL for
        # minutes between stride ticks, starving HTTP job polls (FE 8s timeout).
        if progress_cb is not None and (
            (j + 1) % report_every == 0 or (now - last_prog_t) >= 2.0
        ):
            last_prog_t = now
            # Microstructure is typically the longest vectorized phase — keep the
            # UI stall fingerprint advancing while this O(n·lookback) work runs.
            span = max(0, tot - progress_base)
            done = progress_base + int(((j + 1) / max(n, 1)) * span)
            _safe_progress(progress_cb, min(done, tot), tot)
        start = max(0, j - flb)
        if j == start and flb > 0 and j == 0:
            # No lookback → cold-start zeros (matches bar_to_signal_features).
            continue
        # Need at least one prior bar in lookback for lb truthiness in per-bar path.
        if j == 0:
            continue
        cvd_t = CVDTracker(lookback=max(20, j - start))
        vpin_t = VPINTracker(n_buckets=50)
        for i in range(start, j):
            cvd_t.update_bar(
                open_=float(open_[i]), close=float(close[i]),
                high=float(high[i]), low=float(low[i]), volume=float(volume[i]),
            )
            vpin_t.update_bar(
                open_=float(open_[i]), close=float(close[i]),
                high=float(high[i]), low=float(low[i]), volume=float(volume[i]),
            )
        cvd_t.update_bar(
            open_=float(open_[j]), close=float(close[j]),
            high=float(high[j]), low=float(low[j]), volume=float(volume[j]),
        )
        vpin_t.update_bar(
            open_=float(open_[j]), close=float(close[j]),
            high=float(high[j]), low=float(low[j]), volume=float(volume[j]),
        )
        cvd_z[j] = cvd_t.cvd_z
        cvd_slope[j] = cvd_t.cvd_slope
        vpin[j] = vpin_t.vpin
    return cvd_z, cvd_slope, vpin


def compute_signal_feature_matrix_vectorized(
    candles: Any,
    *,
    feature_lookback: int = EVAL_FEATURE_LOOKBACK,
    progress_cb: Any | None = None,
    cancel_cb: Any | None = None,
) -> np.ndarray:
    """Columnar NumPy path — same schema as :func:`bar_to_signal_features`.

    Accepts ``list[dict]`` or a pandas DataFrame with OHLC + indicator columns.
    """
    n = len(candles)
    n_feat = len(SIGNAL_FEATURE_NAMES)
    out = np.zeros((n, n_feat), dtype=np.float32)
    if n == 0:
        return out
    flb = max(1, int(feature_lookback))

    close = _col_from_rows(candles, "close", n=n)
    open_ = _col_from_rows(candles, "open", n=n)
    high = _col_from_rows(candles, "high", n=n)
    low = _col_from_rows(candles, "low", n=n)
    volume = _col_from_rows(candles, "volume", n=n)

    atr = _col_from_rows(candles, "ATR_14", n=n)
    atr_alt = _col_from_rows(candles, "ATRr_14", n=n)
    atr = np.where(atr > 0, atr, atr_alt)
    bb_upper = _col_from_rows(candles, "BBU_20_2.0", n=n)
    bb_lower = _col_from_rows(candles, "BBL_20_2.0", n=n)
    bb_mid = _col_from_rows(candles, "BBM_20_2.0", n=n)
    rsi_raw = _col_from_rows(candles, "RSI_14", default=50.0, n=n)
    macd_hist_raw = _col_from_rows(candles, "MACDh_12_26_9", n=n)
    stoch_raw = _col_from_rows(candles, "STOCHk_14_3_3", default=50.0, n=n)
    adx_raw = _col_from_rows(candles, "ADX_14", default=20.0, n=n)
    ema_9 = _col_from_rows(candles, "EMA_9", n=n)
    ema_21 = _col_from_rows(candles, "EMA_21", n=n)
    vwap = _col_from_rows(candles, "VWAP", n=n)
    st_dir = _col_from_rows(candles, "SUPERTd_14_3.0", n=n)
    atr_median = _col_from_rows(candles, "ATR_14_median_20", n=n)

    # Lagged closes for returns (missing lag → current close → return 0).
    idx = np.arange(n)
    close_1 = np.array(
        [close[j - 1] if j >= 1 else close[j] for j in range(n)], dtype=np.float64,
    )
    close_5 = np.array(
        [close[j - 5] if j >= 5 else close[j] for j in range(n)], dtype=np.float64,
    )
    close_15 = np.array(
        [close[j - 15] if j >= 15 else close[j] for j in range(n)], dtype=np.float64,
    )

    with np.errstate(divide="ignore", invalid="ignore"):
        returns_1 = np.where(close_1 > 0, (close - close_1) / close_1, 0.0)
        returns_5 = np.where(close_5 > 0, (close - close_5) / close_5, 0.0)
        returns_15 = np.where(close_15 > 0, (close - close_15) / close_15, 0.0)
        log_return = np.where(
            (close > 0) & (close_1 > 0), np.log(close / close_1), 0.0,
        )
        atr_ratio = np.where((close > 0) & (atr > 0), atr / close, 0.0)
        bb_width = np.where(bb_mid > 0, (bb_upper - bb_lower) / bb_mid, 0.0)

    import time as _time

    report_every = max(2_000, n // 20)
    _safe_progress(progress_cb, 0, n)
    last_prog_t = _time.monotonic()

    # rolling_vol_20: std of returns over prev20 + current when >=20 priors.
    rolling_vol_20 = atr_ratio.copy()
    for j in range(20, n):
        if cancel_cb is not None and j % 1024 == 0 and cancel_cb():
            raise InterruptedError("ml_cancel_requested")
        now = _time.monotonic()
        if progress_cb is not None and (j % report_every == 0 or (now - last_prog_t) >= 2.0):
            last_prog_t = now
            # First ~20% of the progress span (columnar prep before microstructure).
            _safe_progress(progress_cb, max(1, int(0.20 * j)), n)
        recent = close[j - 20 : j + 1]
        rets = []
        for i in range(1, len(recent)):
            if recent[i - 1] > 0:
                rets.append((recent[i] - recent[i - 1]) / recent[i - 1])
        rolling_vol_20[j] = float(np.std(rets)) if rets else 0.0

    rsi_14 = rsi_raw / 100.0
    macd_hist = np.where(close > 0, macd_hist_raw / close, 0.0)
    stoch_k = stoch_raw / 100.0
    adx_val = adx_raw / 100.0

    from app.services.bots.ml_feature_kernels import (
        consecutive_streaks_fast,
        microstructure_windowed_fast,
        rolling_mean_prior_fast,
        rolling_std_prior_fast,
    )

    vol_mean_20 = rolling_mean_prior_fast(volume, 20)
    volume_ratio = np.ones(n, dtype=np.float64)
    enough_vol = idx >= 20
    volume_ratio = np.where(
        enough_vol & (vol_mean_20 > 0), volume / np.maximum(vol_mean_20, 1e-12), 1.0,
    )

    obv_slope = np.zeros(n, dtype=np.float64)
    volume_momentum = np.zeros(n, dtype=np.float64)
    for j in range(5, n):
        all_c = close[j - 5 : j + 1]
        all_v = volume[j - 5 : j + 1]
        obv_changes = []
        for i in range(1, 6):
            if all_c[i] > all_c[i - 1]:
                direction = 1.0
            elif all_c[i] < all_c[i - 1]:
                direction = -1.0
            else:
                direction = 0.0
            obv_changes.append(direction * all_v[i])
        denom = max(1.0, sum(abs(v) for v in obv_changes)) or 1.0
        obv_slope[j] = sum(obv_changes) / denom
        v5_avg = float(np.mean(volume[j - 5 : j]))
        volume_momentum[j] = (volume[j] - v5_avg) / v5_avg if v5_avg > 0 else 0.0

    with np.errstate(divide="ignore", invalid="ignore"):
        ema_cross = np.where(
            (close > 0) & (ema_9 > 0) & (ema_21 > 0),
            (ema_9 - ema_21) / close,
            0.0,
        )
        price_vs_vwap = np.where(
            (close > 0) & (vwap > 0), (close - vwap) / close, 0.0,
        )
    supertrend_dir = np.where(st_dir > 0, 1.0, np.where(st_dir < 0, -1.0, 0.0))

    with np.errstate(divide="ignore", invalid="ignore"):
        atr_regime = np.where(
            (atr_median > 0) & (atr > 0), atr / atr_median, 1.0,
        )
    atr_elevated = (atr_regime >= 1.5).astype(np.float64)
    atr_compressed = (atr_regime <= 0.6).astype(np.float64)
    trend_trending = (adx_raw >= 25.0).astype(np.float64)
    trend_ranging = (adx_raw < 20.0).astype(np.float64)

    # Cyclical time + session features
    times = _times_from_rows(candles, n)
    hour_sin = np.zeros(n, dtype=np.float64)
    hour_cos = np.ones(n, dtype=np.float64)
    dow_sin = np.zeros(n, dtype=np.float64)
    dow_cos = np.ones(n, dtype=np.float64)
    is_rth = np.ones(n, dtype=np.float64)
    minutes_from_open_norm = np.zeros(n, dtype=np.float64)
    et_hour_sin = np.zeros(n, dtype=np.float64)
    et_hour_cos = np.ones(n, dtype=np.float64)
    symbol = _symbol_from_rows(candles)
    try:
        from app.services.altdata.calendar import is_crypto_symbol, session_features_for_bar

        crypto = (not symbol) or is_crypto_symbol(symbol)
    except Exception:
        crypto = True
        session_features_for_bar = None  # type: ignore[assignment]

    last_prog_t = _time.monotonic()
    for j in range(n):
        if cancel_cb is not None and j % 1024 == 0 and cancel_cb():
            raise InterruptedError("ml_cancel_requested")
        now = _time.monotonic()
        if progress_cb is not None and (
            (j + 1) % report_every == 0 or (now - last_prog_t) >= 2.0
        ):
            last_prog_t = now
            _safe_progress(progress_cb, max(1, int(0.20 * n + 0.15 * (j + 1))), n)
        ts = times[j]
        if not np.isnan(ts):
            try:
                tsv = float(ts)
                if tsv > 1e12:
                    tsv /= 1000.0
                dt = datetime.fromtimestamp(tsv, tz=timezone.utc)
                hs, hc = _cyclical(dt.hour, 24.0)
                ds, dc = _cyclical(dt.weekday(), 7.0)
                hour_sin[j], hour_cos[j] = hs, hc
                dow_sin[j], dow_cos[j] = ds, dc
            except (TypeError, ValueError, OSError, OverflowError):
                hour_sin[j], hour_cos[j] = _cyclical(12, 24.0)
                dow_sin[j], dow_cos[j] = _cyclical(2, 7.0)
        else:
            hour_sin[j], hour_cos[j] = _cyclical(12, 24.0)
            dow_sin[j], dow_cos[j] = _cyclical(2, 7.0)
        if crypto or session_features_for_bar is None:
            continue
        try:
            sess = session_features_for_bar(symbol, None if np.isnan(ts) else float(ts))
            is_rth[j] = float(sess.get("is_rth", 1.0))
            minutes_from_open_norm[j] = float(sess.get("minutes_from_open_norm", 0.0))
            et_hour_sin[j] = float(sess.get("et_hour_sin", 0.0))
            et_hour_cos[j] = float(sess.get("et_hour_cos", 1.0))
        except Exception:
            pass

    hl_range = high - low
    with np.errstate(divide="ignore", invalid="ignore"):
        high_low_range = np.where(close > 0, hl_range / close, 0.0)
        spread_ratio = np.where(open_ > 0, hl_range / open_, 0.0)
        body = np.abs(close - open_)
        body_ratio = np.where(hl_range > 0, body / hl_range, 0.0)
        bull = close >= open_
        upper_shadow = np.where(
            hl_range > 0,
            np.where(bull, (high - close) / hl_range, (high - open_) / hl_range),
            0.0,
        )
        lower_shadow = np.where(
            hl_range > 0,
            np.where(bull, (open_ - low) / hl_range, (close - low) / hl_range),
            0.0,
        )

    close_mean_20 = rolling_mean_prior_fast(close, 20)
    close_std_20 = rolling_std_prior_fast(close, 20)
    vol_std_20 = rolling_std_prior_fast(volume, 20)
    with np.errstate(divide="ignore", invalid="ignore"):
        close_z_20 = np.where(
            (idx >= 20) & (close_std_20 > 0),
            (close - close_mean_20) / close_std_20,
            0.0,
        )
        volume_z_20 = np.where(
            (idx >= 20) & (vol_std_20 > 0),
            (volume - vol_mean_20) / vol_std_20,
            0.0,
        )

    consecutive_up, consecutive_down = consecutive_streaks_fast(close, flb)
    # Microstructure owns roughly the last 65% of the feature-progress span.
    micro_base = int(0.35 * n)
    cvd_z, cvd_slope, vpin = microstructure_windowed_fast(
        open_,
        close,
        high,
        low,
        volume,
        flb,
        cancel_cb=cancel_cb,
        progress_cb=progress_cb,
        progress_base=micro_base,
        progress_total=n,
    )

    cols = [
        returns_1, returns_5, returns_15, log_return,
        atr_ratio, bb_width, rolling_vol_20,
        rsi_14, macd_hist, stoch_k, adx_val,
        volume_ratio, obv_slope, volume_momentum,
        ema_cross, price_vs_vwap, supertrend_dir,
        atr_elevated, atr_compressed, trend_trending, trend_ranging,
        hour_sin, hour_cos, dow_sin, dow_cos,
        is_rth, minutes_from_open_norm, et_hour_sin, et_hour_cos,
        high_low_range, body_ratio, upper_shadow, lower_shadow, spread_ratio,
        close_z_20, volume_z_20,
        consecutive_up, consecutive_down,
        cvd_z, cvd_slope, vpin,
    ]
    assert len(cols) == n_feat, f"feature col count {len(cols)} != {n_feat}"
    stacked = np.column_stack(cols).astype(np.float32, copy=False)
    np.nan_to_num(stacked, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    out[:] = stacked
    _safe_progress(progress_cb, n, n)
    return out


def precompute_signal_feature_matrix_loop(
    candles: list[dict] | list,
    *,
    feature_lookback: int = EVAL_FEATURE_LOOKBACK,
    progress_cb: Any | None = None,
    cancel_cb: Any | None = None,
) -> np.ndarray:
    """Legacy per-bar Python loop (parity reference / A/B bench)."""
    n = len(candles)
    n_feat = len(SIGNAL_FEATURE_NAMES)
    out = np.zeros((n, n_feat), dtype=np.float32)
    if n == 0:
        return out
    flb = max(1, int(feature_lookback))
    report_every = max(2_000, n // 20)
    for j in range(n):
        if cancel_cb is not None and j % 512 == 0 and cancel_cb():
            raise InterruptedError("ml_cancel_requested")
        lb_start = max(0, j - flb)
        row = candles[j]
        if hasattr(candles, "iloc"):
            row = candles.iloc[j].to_dict()
            lb = [candles.iloc[k].to_dict() for k in range(lb_start, j)]
        else:
            lb = candles[lb_start:j]
        features = bar_to_signal_features(row, lookback_rows=lb)
        out[j] = signal_features_to_vector(features)
        if progress_cb is not None and (j + 1) % report_every == 0:
            try:
                progress_cb(j + 1, n)
            except Exception:
                pass
    if progress_cb is not None:
        try:
            progress_cb(n, n)
        except Exception:
            pass
    return out


def precompute_signal_feature_matrix(
    candles: list[dict] | list | Any,
    *,
    feature_lookback: int = EVAL_FEATURE_LOOKBACK,
    progress_cb: Any | None = None,
    cancel_cb: Any | None = None,
    vectorized: bool | None = None,
) -> np.ndarray:
    """Compute signal features once per bar (O(n)), not once per window cell.

    Default uses the columnar NumPy path when ``BACKTEST_VECTORIZED_FEATURES``
    is on (research/backtest). Pass ``vectorized=False`` for the legacy loop.
    Accepts ``list[dict]`` or a pandas DataFrame.
    """
    use_vec = vectorized_features_enabled() if vectorized is None else bool(vectorized)
    if use_vec:
        try:
            return compute_signal_feature_matrix_vectorized(
                candles,
                feature_lookback=feature_lookback,
                progress_cb=progress_cb,
                cancel_cb=cancel_cb,
            )
        except InterruptedError:
            raise
        except Exception:
            logger.warning(
                "Vectorized feature matrix failed — falling back to per-bar loop",
                exc_info=True,
            )
    if hasattr(candles, "iloc"):
        # Loop path expects list[dict].
        rows = candles.to_dict("records")
        return precompute_signal_feature_matrix_loop(
            rows,
            feature_lookback=feature_lookback,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )
    return precompute_signal_feature_matrix_loop(
        candles,
        feature_lookback=feature_lookback,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
    )


def resolve_feature_names(*, include_trade_state: bool = False) -> tuple[str, ...]:
    if include_trade_state:
        return SIGNAL_FEATURE_NAMES + TRADE_STATE_FEATURE_NAMES
    return SIGNAL_FEATURE_NAMES


def resolve_feature_version(*, include_trade_state: bool = False) -> int:
    return TRADE_STATE_FEATURE_VERSION if include_trade_state else SIGNAL_FEATURE_VERSION


def merge_trade_state_features(
    features: dict[str, float],
    trade_state: dict | None = None,
    *,
    enabled: bool = False,
) -> dict[str, float]:
    """Append bounded trade-state features when ``ml_include_trade_state`` is on.

    Historical train without live trade history should pass ``trade_state=None``
    (zeros) so the schema is stable; live inference fills from Pre-Trade context.
    Retrain is required after enabling — feature dim changes to schema v5.
    """
    out = dict(features or {})
    if not enabled:
        return out
    from app.services.bots.pretrade_context import empty_trade_state

    ts = trade_state if isinstance(trade_state, dict) else empty_trade_state()
    out["bot_loss_streak"] = _safe_float(ts.get("bot_loss_streak"), 0.0)
    out["bot_win_rate_24h"] = _safe_float(ts.get("bot_win_rate_24h"), 0.5)
    out["hours_since_last_loss"] = _safe_float(ts.get("hours_since_last_loss"), 1.0)
    return out


def signal_features_to_vector(
    features: dict[str, float],
    *,
    include_trade_state: bool = False,
    feature_names: tuple[str, ...] | list[str] | None = None,
) -> np.ndarray:
    """Convert feature dict to a numpy vector in canonical order."""
    names = feature_names or resolve_feature_names(include_trade_state=include_trade_state)
    return np.array(
        [float(features.get(name, 0.0)) for name in names],
        dtype=np.float64,
    )


_ALIGN_WARNED: set[str] = set()


def align_features_to_scaler_dim(
    arr: np.ndarray,
    n_expected: int,
    *,
    log_label: str | None = None,
) -> np.ndarray:
    """Pad/truncate the last axis so ``arr`` matches a trained scaler / ONNX width.

    New signal features are always appended to :data:`SIGNAL_FEATURE_NAMES`, so
    older models (narrower scaler) are served by keeping the leading columns.
    Wider padding (zeros) covers rare downgrade cases.
    """
    if arr is None or n_expected <= 0:
        return arr
    a = np.asarray(arr)
    if a.ndim < 1:
        return a
    n_got = int(a.shape[-1])
    if n_got == n_expected:
        return a
    if log_label:
        # Once per label — batch backtests would otherwise spam every chunk.
        warn_key = f"{log_label}:{n_got}->{n_expected}"
        if warn_key not in _ALIGN_WARNED:
            _ALIGN_WARNED.add(warn_key)
            logger.warning(
                "%s feature-dim mismatch: live=%d model=%d — aligning by %s "
                "(retrain recommended for full schema)",
                log_label,
                n_got,
                n_expected,
                "truncate" if n_got > n_expected else "zero-pad",
            )
    if n_got > n_expected:
        return a[..., :n_expected]
    pad_width = [(0, 0)] * (a.ndim - 1) + [(0, n_expected - n_got)]
    return np.pad(a, pad_width, mode="constant", constant_values=0.0)


def apply_feature_scaler(
    arr: np.ndarray,
    mean: np.ndarray | list[float],
    std: np.ndarray | list[float],
    *,
    log_label: str | None = None,
) -> np.ndarray:
    """Z-score normalize ``arr`` after aligning the feature axis to scaler length."""
    m = np.asarray(mean, dtype=np.float32).reshape(-1)
    s = np.asarray(std, dtype=np.float32).reshape(-1)
    if m.size == 0:
        return np.asarray(arr, dtype=np.float32)
    if s.size != m.size:
        s = np.ones_like(m, dtype=np.float32)
    s = np.where(s < 1e-8, 1.0, s)
    aligned = align_features_to_scaler_dim(
        np.asarray(arr, dtype=np.float32),
        int(m.size),
        log_label=log_label,
    )
    return (aligned - m) / s
