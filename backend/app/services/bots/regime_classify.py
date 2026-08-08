"""Shared ATR/ADX regime classification for RegimeRotation + REGIME_STRATEGY_AGENT."""

from __future__ import annotations

import math
from typing import Any, Mapping

from app.services.bots.indicators import adx_col, atr_col

# Matches RegimeRotationAgent historical thresholds / strategy targets.
DEFAULT_REGIME_STRATEGY_MAP: dict[str, str] = {
    "elevated_vol": "VWAP_PULLBACK",
    "trending": "SUPERTREND_ADX",
    "ranging": "BRS_SCALPING",
}

DEFAULT_ATR_RATIO_ELEVATED = 1.5
DEFAULT_ADX_TREND = 25.0
DEFAULT_ATR_LENGTH = 14
DEFAULT_ADX_LENGTH = 14

ALLOWLISTED_CHILDREN = frozenset(DEFAULT_REGIME_STRATEGY_MAP.values())


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        out = float(val)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except (TypeError, ValueError):
        return default


def classify_atr_adx_regime(
    row: Mapping[str, Any],
    *,
    atr_length: int = DEFAULT_ATR_LENGTH,
    adx_length: int = DEFAULT_ADX_LENGTH,
    atr_ratio_elevated: float = DEFAULT_ATR_RATIO_ELEVATED,
    adx_trend: float = DEFAULT_ADX_TREND,
) -> str:
    """Classify bar into elevated_vol | trending | ranging.

    Same decision order as RegimeRotationAgent:
    1. ATR / ATR_median_20 >= atr_ratio_elevated → elevated_vol
    2. else ADX > adx_trend → trending
    3. else ranging
    """
    atr_name = atr_col(int(atr_length))
    adx_name = adx_col(int(adx_length))
    median_name = f"{atr_name}_median_20"

    atr_val = _safe_float(row.get(atr_name), 0.0)
    median_atr = _safe_float(row.get(median_name), 0.0)
    adx_val = _safe_float(row.get(adx_name), 0.0)

    ratio = (atr_val / median_atr) if median_atr > 0 else 1.0
    if ratio >= float(atr_ratio_elevated):
        return "elevated_vol"
    if adx_val > float(adx_trend):
        return "trending"
    return "ranging"


def resolve_regime_strategy_map(cfg: Mapping[str, Any] | None) -> dict[str, str]:
    """Merge config overrides onto the default regime→strategy map."""
    out = dict(DEFAULT_REGIME_STRATEGY_MAP)
    raw = (cfg or {}).get("regime_strategy_map")
    if isinstance(raw, dict):
        for regime, strat in raw.items():
            key = str(regime or "").strip().lower()
            val = str(strat or "").strip().upper()
            if key and val:
                out[key] = val
    return out
