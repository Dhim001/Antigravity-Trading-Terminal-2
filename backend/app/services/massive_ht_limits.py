"""Configurable Massive higher-timeframe bar limits (chart vs bot analysis)."""

from __future__ import annotations

import os
from typing import Literal

from app.services.market.timeframes import TIMEFRAME_SECS, normalize_timeframe

Purpose = Literal["chart", "analysis"]

_HTF_KEYS = tuple(k for k in TIMEFRAME_SECS if k != "1m")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


def _env_int_optional(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return None


# Defaults (overridable via env)
MASSIVE_HT_LIMIT_CHART = _env_int("MASSIVE_HT_LIMIT_CHART", 600)
MASSIVE_HT_LIMIT_ANALYSIS = _env_int("MASSIVE_HT_LIMIT_ANALYSIS", 2000)
MASSIVE_HT_FETCH_MAX = _env_int("MASSIVE_HT_FETCH_MAX", 50000)

# Per-TF overrides: MASSIVE_HT_LIMIT_{TF}_{CHART|ANALYSIS} e.g. MASSIVE_HT_LIMIT_1H_ANALYSIS=2500
_TF_ENV_SUFFIX = {
    "5m": "5M",
    "15m": "15M",
    "1h": "1H",
    "4h": "4H",
    "1d": "1D",
}


def _per_tf_limit(tf: str, purpose: Purpose) -> int | None:
    suffix = _TF_ENV_SUFFIX.get(tf)
    if not suffix:
        return None
    key = f"MASSIVE_HT_LIMIT_{suffix}_{purpose.upper()}"
    return _env_int_optional(key)


def massive_ht_limit(
    timeframe: str,
    *,
    purpose: Purpose = "chart",
    explicit: int | None = None,
) -> int:
    """
    Resolve bar count for a Massive HT REST fetch.

    Priority: explicit request param > per-TF env > global chart/analysis default.
    """
    if explicit is not None and explicit > 0:
        return min(int(explicit), MASSIVE_HT_FETCH_MAX)

    tf = normalize_timeframe(timeframe)
    if tf == "1m":
        from app.config import MARKET_CANDLE_SNAPSHOT_LIMIT

        return MARKET_CANDLE_SNAPSHOT_LIMIT

    per_tf = _per_tf_limit(tf, purpose)
    if per_tf is not None:
        return min(per_tf, MASSIVE_HT_FETCH_MAX)

    default = MASSIVE_HT_LIMIT_CHART if purpose == "chart" else MASSIVE_HT_LIMIT_ANALYSIS
    return min(default, MASSIVE_HT_FETCH_MAX)


def massive_ht_store_cap(timeframe: str) -> int:
    """Max HT bars to retain in server cache for a timeframe (analysis depth)."""
    tf = normalize_timeframe(timeframe)
    if tf == "1m":
        return MASSIVE_HT_LIMIT_CHART
    analysis = massive_ht_limit(tf, purpose="analysis")
    return min(analysis, MASSIVE_HT_FETCH_MAX)


def massive_ht_limits_summary() -> dict[str, dict[str, int]]:
    """Expose resolved limits for health/settings."""
    out: dict[str, dict[str, int]] = {}
    for tf in _HTF_KEYS:
        out[tf] = {
            "chart": massive_ht_limit(tf, purpose="chart"),
            "analysis": massive_ht_limit(tf, purpose="analysis"),
        }
    return out
