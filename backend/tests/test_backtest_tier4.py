"""Tier 4 UX & workflow — regime filter, portfolio sweep, deploy gate."""

from __future__ import annotations

import unittest

from app.services.bots.backtest_portfolio_sweep import (
    aggregate_portfolio_rows,
    rank_portfolio_sweep_rows,
)
from app.services.bots.backtest_regime_filter import (
    filter_candles_by_regime,
    parse_optimize_regime,
)
from app.services.bots.deploy_gate import evaluate_deploy_gate


def _candles(n: int, *, vol: float = 1.0) -> list[dict]:
    out = []
    price = 100.0
    for i in range(n):
        wiggle = vol * (0.5 if i % 7 == 0 else 0.1)
        out.append({
            "time": 1_000_000 + i * 60_000,
            "open": price,
            "high": price + wiggle,
            "low": price - wiggle,
            "close": price + (0.02 if i % 2 == 0 else -0.02),
            "volume": 1000 + i,
        })
        price += 0.01
    return out


class TestRegimeFilter(unittest.TestCase):
    def test_all_returns_full_series(self):
        candles = _candles(200)
        kept, meta = filter_candles_by_regime(candles, "all")
        self.assertEqual(len(kept), 200)
        self.assertFalse(meta.get("applied"))

    def test_filter_reduces_bars(self):
        candles = _candles(300, vol=3.0)
        kept, meta = filter_candles_by_regime(candles, "elevated")
        self.assertTrue(meta.get("applied"))
        self.assertLessEqual(len(kept), len(candles))

    def test_parse_regime_default(self):
        self.assertEqual(parse_optimize_regime({}, msg={}), "all")
        self.assertEqual(parse_optimize_regime({"optimize_regime": "trend"}, msg={}), "trend")


class TestPortfolioSweep(unittest.TestCase):
    def test_aggregate_sums_pnl(self):
        rows = [
            {"total_pnl": 10, "trade_count": 2, "summary": {"total_pnl": 10, "sharpe_ratio": 1.0}},
            {"total_pnl": 5, "trade_count": 3, "summary": {"total_pnl": 5, "sharpe_ratio": 0.5}},
        ]
        agg = aggregate_portfolio_rows(rows, objective="total_pnl")
        self.assertEqual(agg["total_pnl"], 15)
        self.assertEqual(agg["trade_count"], 5)

    def test_rank_portfolio_rows(self):
        rows = [
            {"config": {"a": 1}, "total_pnl": 1, "trade_count": 5, "summary": {"total_pnl": 1}},
            {"config": {"a": 2}, "total_pnl": 10, "trade_count": 5, "summary": {"total_pnl": 10}},
        ]
        ranked = rank_portfolio_sweep_rows(rows, objective="total_pnl", min_trades=1)
        self.assertEqual(ranked[0]["total_pnl"], 10)


class TestDeployGateExploratory(unittest.TestCase):
    def test_blocks_sweep_only(self):
        results = {
            "sweep": {"results": [{"config": {}}], "best_config": {}},
        }
        gate = evaluate_deploy_gate(results, min_trades=1, min_pnl=0)
        exploratory = [c for c in gate["checks"] if c["id"] == "exploratory_sweep"]
        self.assertEqual(len(exploratory), 1)
        self.assertFalse(exploratory[0]["ok"])
        self.assertTrue(gate["blocking"])


if __name__ == "__main__":
    unittest.main()
