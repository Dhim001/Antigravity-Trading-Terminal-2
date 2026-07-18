"""Tests for portfolio backtest result formatting / enrichment."""

from __future__ import annotations

import unittest

from app.services.bots.backtest_portfolio import (
    format_portfolio_results,
    _cap_symbol_trades,
    _merge_portfolio_trades,
)


class FormatPortfolioResultsTests(unittest.TestCase):
    def test_enriches_summary_trades_and_drawdown(self):
        per_symbol = {
            "BTCUSDT": {
                "total_pnl": 100.0,
                "trade_count": 2,
                "win_rate": 50.0,
                "max_drawdown": 2.0,
                "sharpe_ratio": 1.1,
                "weight": 0.5,
                "allocation": 5000,
                "sparkline": [100, 110],
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "time": 100,
                        "side": "BUY",
                        "quantity": 1,
                        "price": 100,
                        "is_exit": False,
                    },
                    {
                        "symbol": "BTCUSDT",
                        "time": 200,
                        "side": "SELL",
                        "quantity": 1,
                        "price": 110,
                        "pnl": 10.0,
                        "is_exit": True,
                        "position_side": "BUY",
                        "hold_seconds": 3600,
                    },
                ],
            },
            "ETHUSDT": {
                "total_pnl": -40.0,
                "trade_count": 1,
                "win_rate": 0.0,
                "max_drawdown": 1.0,
                "sharpe_ratio": 0.2,
                "weight": 0.5,
                "allocation": 5000,
                "sparkline": [100, 90],
                "trades": [
                    {
                        "symbol": "ETHUSDT",
                        "time": 150,
                        "side": "BUY",
                        "quantity": 1,
                        "price": 50,
                        "pnl": -40.0,
                        "is_exit": True,
                        "position_side": "BUY",
                        "hold_seconds": 7200,
                    },
                ],
            },
        }
        equity = [
            {"time": 0, "equity": 10000},
            {"time": 100, "equity": 10100},
            {"time": 200, "equity": 10060},
        ]
        raw = {
            "total_pnl": 60.0,
            "total_trades": 3,
            "portfolio_win_rate": 33.33,
            "max_drawdown": 1.5,
            "starting_capital": 10000,
            "per_symbol": per_symbol,
            "equity_curve": equity,
            "trades": _merge_portfolio_trades(per_symbol),
        }
        out = format_portfolio_results(raw)
        self.assertTrue(out["portfolio"])
        self.assertEqual(out["trade_count"], 3)
        self.assertEqual(out["trades_total"], 3)
        self.assertGreaterEqual(len(out["trades"]), 2)
        self.assertIn("summary", out)
        self.assertIn("profit_factor", out["summary"])
        self.assertIn("sharpe_ratio", out["summary"])
        self.assertIn("largest_win", out["summary"])
        self.assertTrue(out.get("drawdown_curve"))
        self.assertEqual(out["drawdown_curve"][0].get("drawdown_pct"), 0.0)
        shares = {r["symbol"]: r.get("pnl_contribution_pct") for r in out["symbol_results"]}
        self.assertAlmostEqual(shares["BTCUSDT"], 100 / 140 * 100, places=0)
        self.assertAlmostEqual(shares["ETHUSDT"], -40 / 140 * 100, places=0)

    def test_cap_symbol_trades_adds_symbol(self):
        trades = [{"time": i, "side": "BUY", "is_exit": False} for i in range(80)]
        capped = _cap_symbol_trades(trades, "SOLUSDT")
        self.assertEqual(len(capped), 50)
        self.assertEqual(capped[0]["symbol"], "SOLUSDT")
        self.assertEqual(capped[0]["time"], 30)


if __name__ == "__main__":
    unittest.main()
