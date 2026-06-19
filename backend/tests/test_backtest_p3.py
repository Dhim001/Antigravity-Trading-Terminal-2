"""Tests for backtest P3 — costs, sweep, jobs."""

import unittest

from app.services.bots.backtest_costs import (
    entry_fill_price,
    exit_fill_price,
    parse_cost_config,
    trade_fee,
)
from app.services.bots.backtest_jobs import _BacktestJob, cancel_job, clear_job, start_job
from app.services.bots.backtest_sweep import expand_sweep_grid, sweep_label


class TestBacktestCosts(unittest.TestCase):
    def test_entry_slippage_worsens_buy(self):
        fill = entry_fill_price(100.0, "BUY", 10)
        self.assertGreater(fill, 100.0)

    def test_exit_slippage_worsens_long_sell(self):
        fill = exit_fill_price(100.0, "SELL", 10)
        self.assertLess(fill, 100.0)

    def test_trade_fee(self):
        self.assertAlmostEqual(trade_fee(10_000, 5), 5.0)


class TestBacktestSweep(unittest.TestCase):
    def test_expand_grid_caps_combos(self):
        configs = expand_sweep_grid(
            {"allocation": 1000},
            {
                "trailing_stop_percent": [1, 2, 3, 4],
                "take_profit_percent": [2, 3, 4, 5, 6],
            },
        )
        self.assertLessEqual(len(configs), 24)
        self.assertGreater(len(configs), 1)

    def test_sweep_label(self):
        label = sweep_label({"trailing_stop_percent": 2, "take_profit_percent": 3})
        self.assertIn("SL", label)
        self.assertIn("TP", label)


class TestBacktestJobs(unittest.TestCase):
    def test_cancel_job(self):
        ws = object()
        job = start_job(ws)
        self.assertIsNotNone(job)
        self.assertTrue(cancel_job(ws))
        self.assertTrue(job.is_cancelled())
        clear_job(ws)


if __name__ == "__main__":
    unittest.main()
