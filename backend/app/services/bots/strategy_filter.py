"""Strategy composition / filter gate.

Allows chaining a secondary strategy as a trend gate on the primary signal.
Configuration (in bot config):
    "filter_strategy": "SUPERTREND_ADX"   # gate strategy name
    "filter_config":   {}                  # optional overrides for the gate strategy
    "filter_mode":     "TREND_GATE"        # gate mode (only TREND_GATE supported now)

Behaviour:
    - Evaluates the filter strategy on the same bar data
    - TREND_GATE mode: BUY signals require the filter to agree with bullish bias,
      SELL signals require bearish bias. CLOSE signals always pass through.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.bots.strategies import BaseStrategy

logger = logging.getLogger(__name__)


class StrategyFilter:
    """Lightweight filter gate that wraps a secondary strategy evaluation."""

    def __init__(self, filter_strategy_instance: "BaseStrategy", mode: str = "TREND_GATE"):
        self.filter = filter_strategy_instance
        self.mode = mode.upper()

    def evaluate_gate(self, df_row: dict, primary_signal: str) -> tuple[bool, str]:
        """Check whether the primary signal passes the filter gate.

        Returns:
            (allowed: bool, reason: str)
        """
        # CLOSE signals always pass — never block exits
        if primary_signal == "CLOSE":
            return True, "exit_passthrough"

        if self.mode != "TREND_GATE":
            return True, "unknown_mode_passthrough"

        try:
            filter_result = self.filter.evaluate(df_row)
            filter_signal = filter_result.get("signal", "NONE")

            # Determine the filter's bias
            if filter_signal == "BUY":
                filter_bias = "BULL"
            elif filter_signal == "SELL":
                filter_bias = "BEAR"
            else:
                filter_bias = "NEUTRAL"

            # Gate logic
            if primary_signal == "BUY" and filter_bias == "BEAR":
                return False, f"Filter {self.mode}: bearish bias blocks BUY"
            if primary_signal == "SELL" and filter_bias == "BULL":
                return False, f"Filter {self.mode}: bullish bias blocks SELL"

            return True, f"Filter aligned ({filter_bias})"

        except Exception as exc:
            logger.warning("Strategy filter evaluation failed: %s", exc)
            # On error, allow the signal through — fail open
            return True, "filter_error_passthrough"


def build_filter_from_config(bot_config: dict) -> StrategyFilter | None:
    """Create a StrategyFilter from bot config, or None if not configured."""
    filter_name = (bot_config or {}).get("filter_strategy", "").strip()
    if not filter_name:
        return None

    from app.services.bots.strategies import get_strategy

    filter_config = (bot_config or {}).get("filter_config", {})
    mode = (bot_config or {}).get("filter_mode", "TREND_GATE")

    try:
        instance = get_strategy(filter_name, filter_config)
        return StrategyFilter(instance, mode)
    except Exception as exc:
        logger.warning("Failed to build strategy filter '%s': %s", filter_name, exc)
        return None
