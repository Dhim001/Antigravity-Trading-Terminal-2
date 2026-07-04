import unittest

from app.services.bots.backtest_parity import (
    build_htf_bias_lookup,
    htf_bias_at_time,
    htf_bias_from_bars,
)
from app.services.bots.backtest_payload import trim_results_for_wire
from app.services.bots.backtest_portfolio import format_portfolio_results


class TestBacktestParity(unittest.TestCase):
    def test_htf_bias_bull(self):
        bias = htf_bias_from_bars(
            {"close": 100, "high": 101, "low": 99},
            {"close": 105, "high": 106, "low": 100},
        )
        self.assertEqual(bias, "BULL")

    def test_htf_lookup_at_time(self):
        candles = [
            {"time": 100, "close": 1, "high": 1, "low": 1},
            {"time": 200, "close": 2, "high": 2, "low": 2},
            {"time": 300, "close": 4, "high": 5, "low": 3},
            {"time": 400, "close": 6, "high": 7, "low": 5},
        ]
        lookup = build_htf_bias_lookup(candles)
        self.assertTrue(len(lookup) >= 1)
        self.assertIn(htf_bias_at_time(lookup, 350), ("BULL", "BEAR", "NEUTRAL"))


class TestBacktestPayloadTrim(unittest.TestCase):
    def test_trims_equity_and_trades(self):
        equity = [{"time": i, "equity": 1000 + i} for i in range(5000)]
        trades = [{"time": i, "side": "BUY"} for i in range(200)]
        out = trim_results_for_wire({
            "equity_curve": equity,
            "trades": trades,
            "trades_total": 200,
        })
        self.assertLessEqual(len(out["equity_curve"]), 2000)
        self.assertEqual(out["trades_total"], 200)
        self.assertLessEqual(len(out["trades"]), 100)


class TestPortfolioFormat(unittest.TestCase):
    def test_format_includes_skipped(self):
        raw = {
            "total_pnl": 5,
            "total_trades": 1,
            "portfolio_win_rate": 100,
            "max_drawdown": 1,
            "starting_capital": 10000,
            "ending_capital": 10005,
            "return_pct": 0.05,
            "per_symbol": {
                "A": {"total_pnl": 5, "trade_count": 1, "win_rate": 100, "equity_curve": [], "allocation": 5000},
                "B": {"error": "Not enough data", "skipped": True, "allocation": 5000},
            },
            "equity_curve": [],
        }
        out = format_portfolio_results(raw)
        self.assertEqual(out["symbols_tested"], 1)
        self.assertEqual(out["symbols_failed"], 1)
        self.assertEqual(len(out["skipped_symbols"]), 1)

    def test_return_pct_uses_deployed_capital_only(self):
        raw = {
            "total_pnl": 100,
            "total_trades": 2,
            "portfolio_win_rate": 50,
            "max_drawdown": 1,
            "starting_capital": 10000,
            "per_symbol": {
                "A": {"total_pnl": 100, "trade_count": 2, "win_rate": 50, "allocation": 4000},
                "B": {"error": "Not enough data", "skipped": True, "allocation": 6000},
            },
            "equity_curve": [],
        }
        out = format_portfolio_results(raw)
        self.assertEqual(out["starting_capital"], 4000)
        self.assertEqual(out["ending_capital"], 4100)
        self.assertEqual(out["return_pct"], 2.5)


if __name__ == "__main__":
    unittest.main()
