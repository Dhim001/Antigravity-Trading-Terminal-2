"""Alert rule condition types."""

from __future__ import annotations

PRICE_ABOVE = "price_above"
PRICE_BELOW = "price_below"
RSI_ABOVE = "rsi_above"
RSI_BELOW = "rsi_below"
MACD_CROSS_BULL = "macd_cross_bull"
MACD_CROSS_BEAR = "macd_cross_bear"
SIGNAL_IS = "signal_is"
PCT_CHANGE_ABOVE = "pct_change_above"
PCT_CHANGE_BELOW = "pct_change_below"

ALL_CONDITION_TYPES = (
    PRICE_ABOVE,
    PRICE_BELOW,
    RSI_ABOVE,
    RSI_BELOW,
    MACD_CROSS_BULL,
    MACD_CROSS_BEAR,
    SIGNAL_IS,
    PCT_CHANGE_ABOVE,
    PCT_CHANGE_BELOW,
)

NEEDS_THRESHOLD = frozenset({
    PRICE_ABOVE,
    PRICE_BELOW,
    RSI_ABOVE,
    RSI_BELOW,
    PCT_CHANGE_ABOVE,
    PCT_CHANGE_BELOW,
})

NEEDS_SIGNAL = frozenset({SIGNAL_IS})
