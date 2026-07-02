"""CHART_AGENT bar_time alignment — 5m cache must not use 1m timestamps."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app.services.agent.bar_time import (
    bar_times_match,
    candles_match_timeframe,
    coerce_bar_time,
    find_bar_index,
)
from app.services.agent.chart_analyst import ChartAnalystService, init_chart_analyst
from app.services.bots.candle_source import candles_for_timeframe
from app.services.bots.screener import MarketScreenerService
from app.services.bots.strategies_chart_agent import ChartAgentStrategy
from tests.test_chart_agent_rules import make_trending_candles


class TestBarTimeHelpers(unittest.TestCase):
    def test_coerce_bar_time_accepts_float(self):
        self.assertEqual(coerce_bar_time(1783024500.0), 1783024500)

    def test_bar_times_match_int_float(self):
        self.assertTrue(bar_times_match(1783024500, 1783024500.0))

    def test_candles_match_timeframe_rejects_1m_for_5m(self):
        raw_1m = make_trending_candles(120)
        self.assertFalse(candles_match_timeframe(raw_1m, "5m"))

    def test_candles_match_timeframe_accepts_resampled_5m(self):
        raw_1m = make_trending_candles(600)
        bars_5m = candles_for_timeframe(raw_1m, "5m", min_bars=50)
        self.assertTrue(candles_match_timeframe(bars_5m, "5m"))


class TestChartAnalystBarAlignment(unittest.TestCase):
    def setUp(self):
        self.raw_1m = make_trending_candles(600)
        self.candles_5m = candles_for_timeframe(self.raw_1m, "5m", min_bars=50)
        self.analyst = init_chart_analyst(
            screener=MarketScreenerService(),
            feed=None,
            broadcast_fn=None,
        )

    def test_ensure_for_bar_rejects_1m_candles_for_5m_target(self):
        async def _run():
            with patch.object(ChartAnalystService, "persist", lambda self, insight: None):
                with patch.object(ChartAnalystService, "_audit", lambda *a, **k: None):
                    target = int(self.candles_5m[-2]["time"])
                    return await self.analyst.ensure_for_bar(
                        "BTCUSDT",
                        self.raw_1m[-120:],
                        target,
                        timeframe="5m",
                    )

        insight = asyncio.run(_run())
        self.assertIsNone(insight)
        cached = self.analyst.get_cached("BTCUSDT", "5m")
        self.assertTrue(cached is None or bar_times_match(cached.get("bar_time"), self.candles_5m[-2]["time"]))

    def test_ensure_for_bar_scores_exact_closed_5m_bar(self):
        async def _run():
            with patch.object(ChartAnalystService, "persist", lambda self, insight: None):
                with patch.object(ChartAnalystService, "_audit", lambda *a, **k: None):
                    target = int(self.candles_5m[-2]["time"])
                    return await self.analyst.ensure_for_bar(
                        "BTCUSDT",
                        self.candles_5m,
                        target,
                        timeframe="5m",
                    )

        insight = asyncio.run(_run())
        self.assertIsNotNone(insight)
        self.assertTrue(bar_times_match(insight.bar_time, self.candles_5m[-2]["time"]))

    def test_strategy_accepts_aligned_cache(self):
        async def _run():
            with patch.object(ChartAnalystService, "persist", lambda self, insight: None):
                with patch.object(ChartAnalystService, "_audit", lambda *a, **k: None):
                    target = int(self.candles_5m[-2]["time"])
                    await self.analyst.ensure_for_bar(
                        "BTCUSDT",
                        self.candles_5m,
                        target,
                        timeframe="5m",
                    )

        asyncio.run(_run())
        strategy = ChartAgentStrategy({
            "symbol": "BTCUSDT",
            "timeframe": "5m",
            "min_confidence": 0.0,
        })
        target = int(self.candles_5m[-2]["time"])
        result = strategy.evaluate({"time": float(target), "close": 100})
        self.assertNotIn("bar_time mismatch", result.get("reject_reason") or "")


class TestFindBarIndex(unittest.TestCase):
    def test_find_bar_index_matches_row(self):
        import pandas as pd

        df = pd.DataFrame([
            {"time": 100, "close": 1},
            {"time": 200, "close": 2},
            {"time": 300, "close": 3},
        ])
        self.assertEqual(find_bar_index(df, 200), 1)


if __name__ == "__main__":
    unittest.main()
