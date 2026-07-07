"""Tier 1 optimization — WFE, DSR, trades-per-param, OOS warm-up."""

import unittest

from app.services.bots.backtest_selection_bias import (
    apply_score_window,
    build_oos_candles_with_warmup,
    deflated_sharpe_ratio,
    effective_min_trades,
    expected_max_sharpe,
    walk_forward_efficiency,
)
from app.services.bots.backtest_sweep import count_sweep_axes, count_varying_param_axes
from app.services.bots.backtest_walk_forward import (
    aggregate_fold_oos,
    row_objective_value,
)
from app.services.agent.pipeline import validate_walk_forward_oos
from tests.test_backtest_align import _make_candles


class TestSelectionBiasMetrics(unittest.TestCase):
    def test_walk_forward_efficiency(self):
        self.assertAlmostEqual(walk_forward_efficiency(100.0, 60.0), 0.6)
        self.assertIsNone(walk_forward_efficiency(0.0, 10.0))
        self.assertIsNone(walk_forward_efficiency(-5.0, 10.0))

    def test_effective_min_trades_per_param(self):
        self.assertEqual(effective_min_trades(2, base_min=0), 60)
        self.assertEqual(effective_min_trades(2, base_min=100), 100)
        self.assertEqual(effective_min_trades(2, base_min=0, trades_per_param=5), 10)

    def test_deflated_sharpe_penalizes_many_trials(self):
        emax_few = expected_max_sharpe(5, 252)
        emax_many = expected_max_sharpe(100, 252)
        dsr = deflated_sharpe_ratio(0.25, num_trials=100, num_observations=252)
        self.assertGreater(emax_many, emax_few)
        self.assertIsNotNone(dsr)
        self.assertLessEqual(dsr, 0.99)

    def test_expected_max_sharpe_grows_with_trials(self):
        e5 = expected_max_sharpe(5, 100)
        e50 = expected_max_sharpe(50, 100)
        self.assertGreater(e50, e5)

    def test_count_sweep_axes(self):
        self.assertEqual(
            count_sweep_axes({"trailing_stop_percent": [1, 2], "min_confidence": [0.5, 0.6]}),
            2,
        )

    def test_count_varying_param_axes(self):
        configs = [
            {"a": 1, "b": 1},
            {"a": 2, "b": 1},
        ]
        self.assertEqual(count_varying_param_axes(configs), 1)


class TestOosWarmup(unittest.TestCase):
    def test_build_oos_candles_prepends_train_tail(self):
        candles = _make_candles(120)
        split = 84
        train = candles[:split]
        test = candles[split:]
        combined, score_from = build_oos_candles_with_warmup(train, test, warmup_bars=20)
        self.assertEqual(len(combined), len(test) + 20)
        self.assertEqual(score_from, test[0]["time"])

    def test_apply_score_window_filters_trades(self):
        score_from = 2000
        result = {
            "starting_equity": 1000,
            "allocation": 1000,
            "trades": [
                {"time": 1000, "side": "BUY", "is_exit": False},
                {"time": 1500, "side": "SELL", "is_exit": True, "pnl": 50},
                {"time": 2100, "side": "BUY", "is_exit": False},
                {"time": 2200, "side": "SELL", "is_exit": True, "pnl": 25},
            ],
            "equity_curve": [
                {"time": 1000, "equity": 1000},
                {"time": 1500, "equity": 1050},
                {"time": 2100, "equity": 1050},
                {"time": 2200, "equity": 1075},
            ],
            "summary": {},
        }
        trimmed = apply_score_window(result, score_from)
        self.assertEqual(trimmed["trade_count"], 1)
        self.assertEqual(trimmed["total_pnl"], 25.0)


class TestWalkForwardAggregate(unittest.TestCase):
    def test_aggregate_includes_wfe(self):
        folds = [
            {
                "in_sample": {"total_pnl": 100, "trade_count": 10, "summary": {"total_pnl": 100}},
                "out_of_sample": {"total_pnl": 60, "trade_count": 5, "summary": {"total_pnl": 60}},
            },
            {
                "in_sample": {"total_pnl": 80, "trade_count": 8, "summary": {"total_pnl": 80}},
                "out_of_sample": {"total_pnl": 40, "trade_count": 4, "summary": {"total_pnl": 40}},
            },
        ]
        agg = aggregate_fold_oos(folds, objective="total_pnl", num_trials=24)
        self.assertAlmostEqual(agg["walk_forward_efficiency"], 0.5556, places=2)
        self.assertIn("selection_bias", agg)

    def test_validate_wfe_blocks_low_efficiency(self):
        result = {
            "walk_forward": {
                "out_of_sample": {"total_pnl": 10, "trade_count": 5},
                "aggregate": {
                    "fold_count": 2,
                    "walk_forward_efficiency": 0.3,
                    "stability_score": 1.0,
                },
            },
        }
        ok, reason, metrics = validate_walk_forward_oos(result, min_oos_trades=1)
        self.assertFalse(ok)
        self.assertIn("efficiency", reason.lower())
        self.assertEqual(metrics["walk_forward_efficiency"], 0.3)


if __name__ == "__main__":
    unittest.main()
