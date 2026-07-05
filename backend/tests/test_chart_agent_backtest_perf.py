"""CHART_AGENT backtest replay should stay linear-time per bar."""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from app.services.bots.backtester import BacktesterService
from app.services.bots.screener import MarketScreenerService
from tests.test_chart_agent_rules import make_trending_candles


class TestChartAgentBacktestPerf(unittest.TestCase):
    def test_replay_400_bars_completes_quickly_with_cached_sentiment(self):
        candles = make_trending_candles(420, drift=0.0005)
        backtester = BacktesterService(MarketScreenerService())
        cfg = {
            "allocation": 5000,
            "timeframe": "1m",
            "min_confidence": 0.55,
            "min_score": 2,
        }
        sentiment_calls = {"n": 0}

        def counted_sentiment(symbol, **kwargs):
            sentiment_calls["n"] += 1
            return {
                "symbol": symbol,
                "aggregate_score": 0.0,
                "mention_count": 0,
                "sources": [],
                "sample_headlines": [],
            }

        with patch(
            "app.services.altdata.store.get_aggregate_sentiment",
            side_effect=counted_sentiment,
        ):
            started = time.perf_counter()
            result = backtester.run_backtest("BTCUSDT", "CHART_AGENT", cfg, candles)
            elapsed = time.perf_counter() - started

        self.assertNotIn("error", result)
        self.assertLess(elapsed, 12.0, f"CHART_AGENT replay too slow: {elapsed:.2f}s")
        self.assertLessEqual(
            sentiment_calls["n"],
            2,
            f"sentiment DB queried {sentiment_calls['n']} times; expected cache (<=2)",
        )


if __name__ == "__main__":
    unittest.main()
