"""Crypto perp positioning — funding rate + open interest scoring for CHART_AGENT."""

from __future__ import annotations

from typing import Any

from app.services.massive_symbols import is_crypto_terminal_symbol

# Binance 8h funding thresholds (fraction, not percent)
_FUNDING_CROWDED_LONG = 0.0005   # 0.05% / 8h
_FUNDING_CROWDED_SHORT = -0.0003  # -0.03% / 8h
_OI_CHANGE_SIGNIFICANT = 5.0      # % over 24h


def classify_quadrant(
    funding_rate: float | None,
    oi_change_24h_pct: float | None,
) -> str:
    """OI direction × funding sign (research-backed 2×2)."""
    fr = float(funding_rate or 0.0)
    oi_up = (oi_change_24h_pct or 0.0) > 1.0
    oi_down = (oi_change_24h_pct or 0.0) < -1.0
    if oi_up and fr > 0:
        return "bullish_leverage_build"
    if oi_up and fr < 0:
        return "bearish_leverage_build"
    if oi_down and fr < 0:
        return "capitulation"
    if oi_down and fr > 0:
        return "deleveraging_longs"
    return "neutral"


def score_derivatives_positioning(
    *,
    funding_rate: float | None,
    oi_change_24h_pct: float | None,
) -> tuple[int, list[str], str]:
    """
    Domain score for CHART_AGENT derivatives leg.

    Returns (score -1..+1, reasons, quadrant).
    """
    reasons: list[str] = []
    fr = float(funding_rate or 0.0)
    oi_chg = float(oi_change_24h_pct or 0.0)
    quadrant = classify_quadrant(fr, oi_chg)
    score = 0

    if fr >= _FUNDING_CROWDED_LONG:
        score -= 1
        reasons.append(f"Funding crowded long ({fr * 100:.3f}%/8h)")
    elif fr <= _FUNDING_CROWDED_SHORT:
        score += 1
        reasons.append(f"Funding crowded short ({fr * 100:.3f}%/8h) — squeeze risk")

    if oi_chg >= _OI_CHANGE_SIGNIFICANT and fr > 0:
        if score == 0:
            score -= 1
        reasons.append(f"OI +{oi_chg:.1f}%/24h with positive funding (leverage build)")
    elif oi_chg <= -_OI_CHANGE_SIGNIFICANT and fr < 0:
        if score == 0:
            score += 1
        reasons.append(f"OI {oi_chg:.1f}%/24h with negative funding (capitulation)")

    if not reasons:
        reasons.append(f"Derivatives neutral ({quadrant})")

    return max(-1, min(1, score)), reasons, quadrant


def get_derivatives_score_at(
    symbol: str,
    bar_time: float | int | None,
) -> tuple[int, list[str], dict[str, Any]]:
    """Score from nearest stored snapshot at or before bar_time (live uses latest)."""
    if not is_crypto_terminal_symbol(symbol):
        return 0, [], {}
    from app.services.altdata.store import get_crypto_derivatives_at

    snap = get_crypto_derivatives_at(symbol, bar_time)
    if not snap:
        return 0, [], {}
    score, reasons, quadrant = score_derivatives_positioning(
        funding_rate=snap.get("funding_rate"),
        oi_change_24h_pct=snap.get("oi_change_24h_pct"),
    )
    meta = {**snap, "quadrant": quadrant}
    return score, reasons, meta
