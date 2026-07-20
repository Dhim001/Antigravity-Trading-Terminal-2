"""Tests for portfolio backtest result formatting / enrichment."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.bots.backtest_portfolio import (
    PortfolioBacktestConfig,
    format_portfolio_results,
    run_portfolio_backtest,
    _cap_symbol_trades,
    _merge_portfolio_trades,
)


def _fake_candles(n: int = 80) -> list[dict]:
    return [
        {"time": 1_700_000_000 + i * 60, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1}
        for i in range(n)
    ]


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
        self.assertAlmostEqual(shares["ETHUSDT"], 40 / 140 * 100, places=0)
        self.assertGreaterEqual(shares["ETHUSDT"], 0.0)

    def test_cap_symbol_trades_adds_symbol(self):
        trades = [{"time": i, "side": "BUY", "is_exit": False} for i in range(80)]
        capped = _cap_symbol_trades(trades, "SOLUSDT")
        self.assertEqual(len(capped), 50)
        self.assertEqual(capped[0]["symbol"], "SOLUSDT")
        self.assertEqual(capped[0]["time"], 30)


class PortfolioStreamingTests(unittest.TestCase):
    def test_resolve_batches_release_candles(self):
        """Resolver called per symbol; peak concurrent candle sets ≤ worker batch."""
        live_refs: list[int] = []
        max_live = [0]
        candles_by_sym = {
            "AAA": _fake_candles(),
            "BBB": _fake_candles(),
            "CCC": _fake_candles(),
            "DDD": _fake_candles(),
        }

        def resolve(sym: str):
            c = candles_by_sym[sym]
            live_refs.append(id(c))
            max_live[0] = max(max_live[0], len(live_refs))
            return c, {}

        def run_bt(symbol, strategy, config, candles, cancel_cb=None):
            # Simulate release of this batch's refs after run by clearing tracker of done ids
            if id(candles) in live_refs:
                live_refs.remove(id(candles))
            return {
                "total_pnl": 1.0,
                "trade_count": 1,
                "win_rate": 100.0,
                "max_drawdown": 0.1,
                "summary": {"sharpe_ratio": 1.0, "blocked_entries": 0},
                "equity_curve": [{"time": 1, "equity": 10000}, {"time": 2, "equity": 10001}],
                "trades": [],
            }

        backtester = MagicMock()
        backtester.run_backtest = run_bt
        cfg = PortfolioBacktestConfig(
            symbols=[
                {"symbol": s, "strategy": "CHART_AGENT", "config": {}, "weight": 1.0}
                for s in candles_by_sym
            ],
            total_capital=40_000.0,
        )

        with patch("app.services.bots.backtest_portfolio.parallel_worker_count", return_value=2):
            with patch(
                "app.services.bots.backtest_portfolio.thread_local_backtest_runner",
                return_value=run_bt,
            ):
                out = run_portfolio_backtest(
                    backtester,
                    cfg,
                    None,
                    resolve_candles=resolve,
                )

        self.assertEqual(out["symbols_traded"], 4)
        self.assertEqual(out["total_trades"], 4)
        # Batch size 2 → never more than 2 candle lists tracked mid-batch
        self.assertLessEqual(max_live[0], 2)

    def test_preloaded_candles_still_work(self):
        def run_bt(symbol, strategy, config, candles, cancel_cb=None):
            return {
                "total_pnl": 2.0,
                "trade_count": 2,
                "win_rate": 50.0,
                "max_drawdown": 1.0,
                "summary": {},
                "equity_curve": [{"time": 1, "equity": 5000}],
                "trades": [],
            }

        backtester = MagicMock()
        backtester.run_backtest = run_bt
        candles = {"X": _fake_candles(), "Y": _fake_candles()}
        cfg = PortfolioBacktestConfig(
            symbols=[
                {"symbol": "X", "strategy": "CHART_AGENT", "config": {}, "weight": 1.0},
                {"symbol": "Y", "strategy": "CHART_AGENT", "config": {}, "weight": 1.0},
            ],
            total_capital=20_000.0,
        )
        with patch("app.services.bots.backtest_portfolio.parallel_worker_count", return_value=1):
            out = run_portfolio_backtest(backtester, cfg, candles)
        self.assertEqual(out["symbols_traded"], 2)
        self.assertEqual(out["total_pnl"], 4.0)


if __name__ == "__main__":
    unittest.main()
