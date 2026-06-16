"""Build indicator-enriched DataFrames for the chart analyst rule engine."""

from __future__ import annotations

import pandas as pd

from app.services.bots.screener import MarketScreenerService

CHART_AGENT_STRATEGY = "CHART_AGENT"
DEFAULT_CONFIG: dict = {
    "rsi_length": 14,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "atr_length": 14,
}


class FeatureBuilder:
    """Reuse MarketScreenerService indicator columns for CHART_AGENT scoring."""

    def __init__(self, screener: MarketScreenerService | None = None):
        self.screener = screener or MarketScreenerService()

    def build(self, symbol: str, candles: list[dict]) -> pd.DataFrame:
        if not candles or len(candles) < 30:
            return pd.DataFrame()
        return self.screener.process_candles(
            symbol,
            candles,
            DEFAULT_CONFIG,
            CHART_AGENT_STRATEGY,
        )
