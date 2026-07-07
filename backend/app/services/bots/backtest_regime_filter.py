"""Regime-conditional optimization — filter bars by ATR vol regime."""

from __future__ import annotations

from typing import Any

from app.services.bots.backtest_analytics import (
    ATR_PERIOD,
    COMPRESSED_ATR_RATIO,
    ELEVATED_ATR_RATIO,
    REGIME_MEDIAN_WINDOW,
    _atr_series,
)

VALID_REGIME_FILTERS = frozenset({
    "all",
    "elevated",
    "normal",
    "compressed",
    "trend",  # alias: normal + elevated (exclude compressed chop)
})


def _bar_regime(atrs: list[float | None], idx: int) -> str | None:
    atr = atrs[idx]
    if atr is None or atr <= 0:
        return None
    window = [
        a for a in atrs[max(0, idx - REGIME_MEDIAN_WINDOW + 1): idx + 1]
        if a is not None
    ]
    if not window:
        return None
    sorted_w = sorted(window)
    median_atr = sorted_w[len(sorted_w) // 2]
    ratio = atr / median_atr if median_atr > 0 else 1.0
    if ratio >= ELEVATED_ATR_RATIO:
        return "elevated"
    if ratio <= COMPRESSED_ATR_RATIO:
        return "compressed"
    return "normal"


def filter_candles_by_regime(
    candles: list[dict],
    regime_filter: str | None,
) -> tuple[list[dict], dict[str, Any]]:
    """Return candles whose bar regime matches filter (or all when 'all')."""
    filt = str(regime_filter or "all").lower()
    if filt not in VALID_REGIME_FILTERS or filt == "all":
        return list(candles or []), {"regime_filter": "all", "applied": False}

    if not candles or len(candles) < REGIME_MEDIAN_WINDOW + ATR_PERIOD:
        return [], {
            "regime_filter": filt,
            "applied": True,
            "note": "Insufficient bars for regime filter",
            "kept_bars": 0,
        }

    atrs = _atr_series(candles, ATR_PERIOD)
    kept: list[dict] = []
    counts = {"elevated": 0, "normal": 0, "compressed": 0}

    for i, bar in enumerate(candles):
        if i < REGIME_MEDIAN_WINDOW:
            continue
        regime = _bar_regime(atrs, i)
        if regime is None:
            continue
        counts[regime] = counts.get(regime, 0) + 1
        if filt == "trend":
            match = regime in ("elevated", "normal")
        else:
            match = regime == filt
        if match:
            kept.append(bar)

    return kept, {
        "regime_filter": filt,
        "applied": True,
        "kept_bars": len(kept),
        "total_bars": len(candles),
        "regime_counts": counts,
    }


def parse_optimize_regime(sweep: dict | None = None, msg: dict | None = None) -> str:
    sweep = sweep or {}
    msg = msg or {}
    raw = sweep.get("optimize_regime") or msg.get("optimize_regime") or "all"
    filt = str(raw).lower()
    return filt if filt in VALID_REGIME_FILTERS else "all"
