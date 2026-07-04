"""HTTP tests for optimization run persistence API."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.services.bots.optimization_store import (
    get_optimization_run,
    list_optimization_runs,
    prune_optimization_runs,
    save_optimization_run,
)


class TestOptimizationStore(unittest.TestCase):
    def test_save_list_get_roundtrip(self):
        run_id = save_optimization_run(
            symbol="BTCUSDT",
            strategy="CHART_AGENT",
            objective="sharpe_ratio",
            request={"days": 7, "walk_forward": False},
            results=[{"total_pnl": 10, "config": {"min_confidence": 0.6}}],
            best_config={"min_confidence": 0.6},
            walk_forward={"train_pct": 70},
        )
        self.assertTrue(run_id)

        listed = list_optimization_runs(limit=5, symbol="BTCUSDT")
        self.assertTrue(any(r["id"] == run_id for r in listed))

        full = get_optimization_run(run_id)
        self.assertIsNotNone(full)
        self.assertEqual(full["symbol"], "BTCUSDT")
        self.assertEqual(full["strategy"], "CHART_AGENT")
        self.assertEqual(full["objective"], "sharpe_ratio")
        self.assertIsInstance(full.get("results"), list)
        self.assertEqual(full["best_config"].get("min_confidence"), 0.6)
        self.assertIsInstance(full.get("walk_forward"), dict)

    def test_prune_no_crash(self):
        deleted = prune_optimization_runs(365)
        self.assertGreaterEqual(deleted, 0)


class TestMonteCarlo(unittest.TestCase):
    def test_bands_from_trades(self):
        from app.services.bots.monte_carlo import monte_carlo_trade_bands

        trades = [
            {"is_exit": True, "pnl": 5.0},
            {"is_exit": True, "pnl": -2.0},
            {"is_exit": True, "pnl": 3.0},
            {"is_exit": True, "pnl": 1.0},
        ]
        bands = monte_carlo_trade_bands(trades, starting_equity=10_000, simulations=200)
        self.assertIsNotNone(bands)
        self.assertLessEqual(bands["pnl_p5"], bands["pnl_p95"])


class TestExtendedObjectives(unittest.TestCase):
    def test_sortino_and_calmar_ranking(self):
        from app.services.bots.backtest_walk_forward import row_objective_value, sort_sweep_rows

        rows = [
            {"total_pnl": 100, "summary": {"sortino_ratio": 1.2, "return_pct": 10, "max_drawdown": 5}},
            {"total_pnl": 50, "summary": {"sortino_ratio": 2.5, "return_pct": 8, "max_drawdown": 2}},
        ]
        ranked = sort_sweep_rows(rows, objective="sortino_ratio")
        self.assertEqual(ranked[0]["total_pnl"], 50)
        calmar = row_objective_value(rows[1], "calmar_ratio")
        self.assertGreater(calmar, 0)


class TestPortfolioBacktest(unittest.TestCase):
    def test_aggregate_results(self):
        from app.services.bots.backtest_portfolio import run_portfolio_backtest

        def fake_run(sym, strategy, config, candles, **kw):
            return {
                "total_pnl": 10 if sym == "A" else 5,
                "trade_count": 2,
                "summary": {"win_rate": 50, "sharpe_ratio": 1.0},
            }

        def resolve(sym):
            return [{"time": i, "close": 100 + i} for i in range(60)], {}

        out = run_portfolio_backtest(
            run_backtest=fake_run,
            symbols=["A", "B"],
            strategy="MACD_RSI",
            config={},
            resolve_candles=resolve,
        )
        self.assertTrue(out.get("portfolio"))
        self.assertEqual(out["total_pnl"], 15)
        self.assertEqual(out["symbols_tested"], 2)
        self.assertEqual(len(out["symbol_results"]), 2)


if __name__ == "__main__":
    unittest.main()
