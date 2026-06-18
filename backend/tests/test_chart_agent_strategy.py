"""CHART_AGENT strategy integration with cached insights."""

from __future__ import annotations

import unittest

from app.database import init_db
from app.services.agent.chart_analyst import init_chart_analyst
from app.services.agent.models import insight_cache_key
from app.services.bots.screener import MarketScreenerService
from app.services.bots.strategies_chart_agent import ChartAgentStrategy
from tests.test_chart_agent_rules import make_trending_candles


class TestChartAgentStrategy(unittest.TestCase):
    def setUp(self):
        init_db()
        self.analyst = init_chart_analyst(screener=MarketScreenerService(), feed=None, broadcast_fn=None)
        self.candles = make_trending_candles(220)
        self.strategy = ChartAgentStrategy({"symbol": "BTCUSDT", "timeframe": "1m", "min_confidence": 0.55})

    def test_evaluate_none_without_cache(self):
        row = {"time": 1, "close": 100}
        result = self.strategy.evaluate(row)
        self.assertEqual(result["signal"], "NONE")

    def test_evaluate_maps_cached_buy_sell(self):
        import asyncio

        insight = asyncio.run(
            self.analyst.analyze("BTCUSDT", candles=self.candles, broadcast=False)
        )
        self.assertIsNotNone(insight)

        eval_row = {"time": insight.bar_time, "close": 100}
        result = self.strategy.evaluate(eval_row)
        if insight.confidence >= 0.55 and insight.signal in ("BUY", "SELL"):
            self.assertEqual(result["signal"], insight.signal)
            if insight.levels.get("stop_loss_distance"):
                self.assertIn("stop_loss_distance", result)
        else:
            self.assertEqual(result["signal"], "NONE")

    def test_low_confidence_blocks_signal(self):
        self.analyst._cache[insight_cache_key("BTCUSDT", "1m")] = (0, {
            "symbol": "BTCUSDT",
            "bar_time": 99,
            "signal": "BUY",
            "confidence": 0.4,
            "score": 1,
            "reasons": [],
            "levels": {},
            "insight_id": "BTCUSDT:1m:99",
        })
        result = self.strategy.evaluate({"time": 99, "close": 100})
        self.assertEqual(result["signal"], "NONE")


if __name__ == "__main__":
    unittest.main()
