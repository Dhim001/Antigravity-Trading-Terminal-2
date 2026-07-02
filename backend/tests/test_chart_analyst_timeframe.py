"""Phase 5 — chart analyst resampled timeframe support."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app.services.agent.chart_analyst import ChartAnalystService, init_chart_analyst
from app.services.agent.models import insight_cache_key
from app.services.bots.candle_source import candles_for_timeframe
from app.services.bots.screener import MarketScreenerService
from app.services.bots.strategies_chart_agent import ChartAgentStrategy
from tests.test_chart_agent_rules import make_trending_candles


class TestChartAnalystTimeframe(unittest.TestCase):
    def setUp(self):
        self.raw_1m = make_trending_candles(600)
        self.candles_5m = candles_for_timeframe(self.raw_1m, "5m", min_bars=50)
        self.analyst = init_chart_analyst(
            screener=MarketScreenerService(),
            feed=None,
            broadcast_fn=None,
        )

    def test_insight_cache_key_includes_timeframe(self):
        self.assertEqual(insight_cache_key("btcusdt", "5m"), "BTCUSDT:5m")
        self.assertEqual(insight_cache_key("BTCUSDT", "1H"), "BTCUSDT:1h")

    def test_analyze_5m_uses_resampled_bar_time(self):
        async def _run():
            with patch.object(ChartAnalystService, "persist", lambda self, insight: None):
                with patch.object(ChartAnalystService, "_audit", lambda *a, **k: None):
                    insight_1m = await self.analyst.analyze(
                        "BTCUSDT",
                        candles=self.raw_1m,
                        timeframe="1m",
                        broadcast=False,
                    )
                    insight_5m = await self.analyst.analyze(
                        "BTCUSDT",
                        candles=self.candles_5m,
                        timeframe="5m",
                        broadcast=False,
                    )
            return insight_1m, insight_5m

        insight_1m, insight_5m = asyncio.run(_run())
        self.assertIsNotNone(insight_1m)
        self.assertIsNotNone(insight_5m)
        self.assertNotEqual(insight_1m.bar_time, insight_5m.bar_time)
        self.assertEqual(insight_5m.timeframe, "5m")
        self.assertIn(":5m:", insight_5m.insight_id)

        cached_1m = self.analyst.get_cached("BTCUSDT", "1m")
        cached_5m = self.analyst.get_cached("BTCUSDT", "5m")
        self.assertIsNotNone(cached_1m)
        self.assertIsNotNone(cached_5m)
        self.assertNotEqual(cached_1m["insight_id"], cached_5m["insight_id"])

    def test_chart_agent_strategy_uses_timeframe_cache(self):
        async def _run():
            with patch.object(ChartAnalystService, "persist", lambda self, insight: None):
                with patch.object(ChartAnalystService, "_audit", lambda *a, **k: None):
                    return await self.analyst.analyze(
                        "BTCUSDT",
                        candles=self.candles_5m,
                        timeframe="5m",
                        broadcast=False,
                    )

        insight = asyncio.run(_run())
        self.assertIsNotNone(insight)

        strategy = ChartAgentStrategy({
            "symbol": "BTCUSDT",
            "timeframe": "5m",
            "min_confidence": 0.0,
        })
        wrong_tf = strategy.evaluate({"time": insight.bar_time, "close": 100})
        if insight.confidence >= 0.55 and insight.signal in ("BUY", "SELL"):
            self.assertEqual(wrong_tf["signal"], insight.signal)
        else:
            self.assertEqual(wrong_tf["signal"], "NONE")

        strategy_1m = ChartAgentStrategy({
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "min_confidence": 0.0,
        })
        mismatch = strategy_1m.evaluate({"time": insight.bar_time, "close": 100})
        self.assertEqual(mismatch["signal"], "NONE")

    def test_ensure_for_bar_respects_timeframe(self):
        async def _run():
            with patch.object(ChartAnalystService, "persist", lambda self, insight: None):
                with patch.object(ChartAnalystService, "_audit", lambda *a, **k: None):
                    bar_time = self.candles_5m[-2]["time"]
                    first = await self.analyst.ensure_for_bar(
                        "BTCUSDT",
                        self.candles_5m,
                        bar_time,
                        timeframe="5m",
                    )
                    second = await self.analyst.ensure_for_bar(
                        "BTCUSDT",
                        self.candles_5m,
                        bar_time,
                        timeframe="5m",
                    )
            return first, second

        first, second = asyncio.run(_run())
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.insight_id, second.insight_id)


if __name__ == "__main__":
    unittest.main()
