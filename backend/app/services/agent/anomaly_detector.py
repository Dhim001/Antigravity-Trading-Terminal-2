"""Lightweight statistical anomaly detection on OHLCV bars."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

_DEFAULT_WINDOW = 20
_VOLUME_Z_THRESHOLD = 2.5
_RETURN_Z_THRESHOLD = 2.5
_GAP_PCT_THRESHOLD = 1.5


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _rolling_zscore(series: pd.Series, window: int) -> float | None:
    if len(series) < window + 1:
        return None
    tail = series.iloc[-window:]
    val = float(series.iloc[-1])
    mean = float(tail.mean())
    std = float(tail.std(ddof=0))
    if std <= 1e-12:
        return 0.0
    return (val - mean) / std


def detect_bar_anomaly(
    df: pd.DataFrame,
    idx: int,
    *,
    window: int = _DEFAULT_WINDOW,
) -> dict[str, Any]:
    """Flag unusual volume or price behavior at bar index."""
    out: dict[str, Any] = {
        "is_anomaly": False,
        "kinds": [],
        "volume_z": None,
        "return_z": None,
        "gap_pct": None,
    }
    if df is None or df.empty or idx < 1 or idx >= len(df):
        return out

    row = df.iloc[idx]
    prev = df.iloc[idx - 1]
    close = _safe_float(row.get("close"))
    prev_close = _safe_float(prev.get("close"))
    volume = _safe_float(row.get("volume"))

    if close is not None and prev_close is not None and prev_close > 0:
        gap_pct = abs((close / prev_close - 1.0) * 100.0)
        out["gap_pct"] = round(gap_pct, 3)
        if gap_pct >= _GAP_PCT_THRESHOLD:
            out["is_anomaly"] = True
            out["kinds"].append("price_gap")

    start = max(0, idx - window)
    window_df = df.iloc[start: idx + 1]
    if len(window_df) < window + 1:
        return out

    if "close" in window_df.columns:
        closes = window_df["close"].astype(float)
        returns = closes.pct_change().dropna()
        if len(returns) >= window:
            ret_z = _rolling_zscore(returns, window)
            if ret_z is not None:
                out["return_z"] = round(ret_z, 3)
                if abs(ret_z) >= _RETURN_Z_THRESHOLD:
                    out["is_anomaly"] = True
                    if "return_spike" not in out["kinds"]:
                        out["kinds"].append("return_spike")

    if volume is not None and "volume" in window_df.columns:
        vols = window_df["volume"].astype(float)
        if len(vols) >= window:
            vol_z = _rolling_zscore(vols, window)
            if vol_z is not None:
                out["volume_z"] = round(vol_z, 3)
                if vol_z >= _VOLUME_Z_THRESHOLD:
                    out["is_anomaly"] = True
                    if "volume_spike" not in out["kinds"]:
                        out["kinds"].append("volume_spike")

    if out["kinds"]:
        out["summary"] = ", ".join(out["kinds"])
    return out
