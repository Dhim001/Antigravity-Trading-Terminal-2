"""Tests for backtest P4+ — analytics, walk-forward, research sim_mode."""

import unittest

from app.services.bots.backtest_analytics import (
    buy_and_hold_benchmark,
    drawdown_curve,
    enrich_summary,
    sortino_ratio,
)
from app.services.bots.backtest_walk_forward import pick_best_config, split_train_test
from app.services.bots.backtester import BacktesterService
from app.services.bots.screener import MarketScreenerService
from tests.test_backtest_align import _make_candles


class TestBacktestAnalytics(unittest.TestCase):
    def test_sortino_from_equity_curve(self):
        curve = [
            {"time": 1, "equity": 1000},
            {"time": 2, "equity": 1010},
            {"time": 3, "equity": 1005},
            {"time": 4, "equity": 1020},
        ]
        ratio = sortino_ratio(curve)
        self.assertIsNotNone(ratio)

    def test_buy_and_hold_benchmark(self):
        candles = _make_candles(10, start=100.0, drift=0.001)
        bench = buy_and_hold_benchmark(candles, 1000.0)
        self.assertIsNotNone(bench)
        self.assertIn("pnl", bench)
        self.assertIn("return_pct", bench)

    def test_drawdown_curve(self):
        curve = [
            {"time": 1, "equity": 1000},
            {"time": 2, "equity": 1100},
            {"time": 3, "equity": 990},
        ]
        dd = drawdown_curve(curve)
        self.assertEqual(len(dd), 3)
        self.assertEqual(dd[0]["drawdown_pct"], 0.0)
        self.assertGreater(dd[2]["drawdown_pct"], 0)

    def test_enrich_summary_adds_alpha(self):
        candles = _make_candles(20, start=100.0, drift=0.002)
        curve = [{"time": c["time"], "equity": 1000 + i * 2} for i, c in enumerate(candles)]
        summary = enrich_summary(
            {"total_pnl": 40.0, "return_pct": 4.0},
            equity_curve=curve,
            candles=candles,
            starting_equity=1000.0,
        )
        self.assertIn("sortino_ratio", summary)
        self.assertIn("benchmark", summary)
        self.assertIn("alpha_pnl", summary)
        self.assertIn("alpha_return_pct", summary)


class TestWalkForward(unittest.TestCase):
    def test_split_train_test(self):
        candles = _make_candles(200)
        meta = {"symbol": "TEST", "oldest": candles[0]["time"], "newest": candles[-1]["time"]}
        train, test, train_meta, test_meta = split_train_test(candles, meta, 70.0)
        self.assertEqual(len(train) + len(test), len(candles))
        self.assertGreaterEqual(len(train), 50)
        self.assertGreaterEqual(len(test), 50)
        self.assertEqual(train_meta.get("window"), "in_sample")
        self.assertEqual(test_meta.get("window"), "out_of_sample")

    def test_pick_best_config(self):
        rows = [
            {"config": {"a": 1}, "total_pnl": 10},
            {"config": {"a": 2}, "total_pnl": 25, "summary": {"total_pnl": 25}},
            {"config": {"a": 3}, "error": "fail"},
        ]
        cfg, row = pick_best_config(rows)
        self.assertEqual(cfg, {"a": 2})
        self.assertEqual(row["total_pnl"], 25)


class TestResearchSimMode(unittest.TestCase):
    def setUp(self):
        self.backtester = BacktesterService(MarketScreenerService())
        self.candles = _make_candles(120)

    def test_research_mode_skips_risk_gates(self):
        live = self.backtester.run_backtest(
            "TEST",
            "MACD_RSI",
            {"allocation": 1000, "sim_mode": "live_aligned"},
            self.candles,
        )
        research = self.backtester.run_backtest(
            "TEST",
            "MACD_RSI",
            {"allocation": 1000, "sim_mode": "research"},
            self.candles,
        )
        self.assertNotIn("error", live)
        self.assertNotIn("error", research)
        self.assertEqual(live["sim_mode"], "live_aligned")
        self.assertEqual(research["sim_mode"], "research")
        self.assertIn("drawdown_curve", research)
        self.assertIn("sortino_ratio", research["summary"])
        self.assertIn("benchmark", research["summary"])


if __name__ == "__main__":
    unittest.main()
