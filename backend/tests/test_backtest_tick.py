"""Tests for tick-strategy backtest replay."""

from __future__ import annotations

import unittest

from app.services.bots.backtest_tick import simulate_ticks_from_candle, TickBacktester
from app.services.bots.backtester import BacktesterService
from app.services.bots.screener import MarketScreenerService
from tests.test_backtest_align import _make_candles


class TestSimulateTicks(unittest.TestCase):
    def test_bullish_bar_path(self):
        bar = {"time": 1000, "open": 100, "high": 102, "low": 99, "close": 101}
        ticks = simulate_ticks_from_candle(bar)
        self.assertEqual(len(ticks), 4)
        prices = [p for _, p in ticks]
        self.assertEqual(prices[0], 100)
        self.assertEqual(prices[-1], 101)

    def test_bearish_bar_path(self):
        bar = {"time": 1000, "open": 100, "high": 101, "low": 97, "close": 98}
        ticks = simulate_ticks_from_candle(bar)
        prices = [p for _, p in ticks]
        self.assertEqual(prices[0], 100)
        self.assertEqual(prices[-1], 98)


class TestTickBacktest(unittest.TestCase):
    def setUp(self):
        self.backtester = BacktesterService(MarketScreenerService())
        self.candles = _make_candles(120)

    def test_tick_momentum_backtest_via_backtester(self):
        result = self.backtester.run_backtest(
            "TEST",
            "TICK_MOMENTUM",
            {"allocation": 1000, "lookback_ticks": 10, "tick_cooldown_sec": 1, "momentum_threshold_pct": 0.01},
            self.candles,
        )
        self.assertNotIn("error", result)
        self.assertEqual(result.get("execution_mode"), "TICK")
        self.assertIn("summary", result)
        self.assertIn("ticks_replayed", result["summary"])

    def test_tick_backtester_direct(self):
        runner = TickBacktester()
        result = runner.run(
            "TEST",
            "TICK_MEAN_REVERT",
            {"allocation": 1000, "lookback_ticks": 10, "tick_cooldown_sec": 1},
            self.candles,
        )
        self.assertNotIn("error", result)
        self.assertIn("trades", result)


if __name__ == "__main__":
    unittest.main()
