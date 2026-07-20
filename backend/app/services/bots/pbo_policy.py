"""Shared PBO (probability of backtest overfitting) pass/fail policy.

CSCV convention: PBO >= 0.5 means coin-flip or worse (block deploy / fail readiness).
Exactly 0.5 is treated as failing — same as ``pbo < 0.5`` for ``ok``.
"""

from __future__ import annotations

PBO_BLOCK_THRESHOLD = 0.5
PBO_MODERATE_THRESHOLD = 0.35


def pbo_passes(pbo: float | None) -> bool:
    """True when a numeric PBO is strictly below the block threshold."""
    if pbo is None:
        return False
    try:
        return float(pbo) < PBO_BLOCK_THRESHOLD
    except (TypeError, ValueError):
        return False


def pbo_is_block(pbo: float | None) -> bool:
    """True when PBO is high enough to hard-block deploy (includes 0.5)."""
    if pbo is None:
        return False
    try:
        return float(pbo) >= PBO_BLOCK_THRESHOLD
    except (TypeError, ValueError):
        return False


def pbo_is_moderate(pbo: float | None) -> bool:
    """True for elevated but non-blocking PBO (warn band)."""
    if pbo is None:
        return False
    try:
        val = float(pbo)
    except (TypeError, ValueError):
        return False
    return PBO_MODERATE_THRESHOLD <= val < PBO_BLOCK_THRESHOLD
