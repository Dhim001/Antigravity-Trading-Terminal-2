"""Tests for backtest / live-bot alignment."""

import unittest
from unittest.mock import MagicMock

from app.services.bots.backtester import BacktesterService
from app.services.bots.screener import MarketScreenerService
from app.services.bots.strategies import BaseStrategy


def _make_candles(count: int = 120, start: float = 100.0, drift: float = 0.0005) -> list:
    candles = []
    base_time = 1_700_000_000
    price = start
    for i in range(count):
        o = price
        h = price * 1.01
        l = price * 0.99
        c = price * (1 + drift)
        candles.append({
            "time": base_time + i * 60,
            "open": round(o, 4),
            "high": round(h, 4),
            "low": round(l, 4),
            "close": round(c, 4),
            "volume": 10_000,
        })
        price = c
    return candles


class AlwaysSellStrategy(BaseStrategy):
    def evaluate(self, df_row) -> dict:
        return {"signal": "SELL", "stop_loss_distance": 2.0}


class AlwaysBuyStrategy(BaseStrategy):
    def evaluate(self, df_row) -> dict:
        return {"signal": "BUY", "stop_loss_distance": 2.0}


class BacktestLiveAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.screener = MarketScreenerService()
        self.backtester = BacktesterService(self.screener)
        self.candles = _make_candles(120)

    def test_screener_full_history_uses_all_bars(self):
        long_series = _make_candles(400)
        df_live = self.screener.process_candles("TEST", long_series, {}, "MACD_RSI")
        df_full = self.screener.process_candles(
            "TEST", long_series, {}, "MACD_RSI", full_history=True,
        )
        self.assertEqual(len(df_live), 300)
        self.assertEqual(len(df_full), 400)

    def test_backtest_uses_allocation_baseline(self):
        result = self.backtester.run_backtest(
            "TEST",
            "MACD_RSI",
            {"allocation": 500, "rsi_length": 14},
            self.candles,
        )
        self.assertNotIn("error", result)
        self.assertEqual(result["starting_equity"], 500.0)
        self.assertEqual(result["allocation"], 500.0)
        self.assertIn("summary", result)
        self.assertIn("profit_factor", result["summary"])
        self.assertIn("expectancy", result["summary"])
        self.assertIn("sharpe_ratio", result["summary"])
        self.assertIn("time_in_market_pct", result["summary"])
        self.assertIn("blocked_entries", result["summary"])
        self.assertIn("max_consecutive_losses", result["summary"])
        self.assertEqual(result["trades_total"], len(result["trades"]))

    def test_backtest_long_only_skips_short_entries(self):
        strategy = AlwaysSellStrategy({})
        self.backtester.screener.process_candles = MagicMock(
            return_value=self.screener.process_candles(
                "TEST", self.candles, {}, "MACD_RSI", full_history=True,
            )
        )
        original_get = __import__(
            "app.services.bots.backtester", fromlist=["get_strategy"],
        ).get_strategy

        import app.services.bots.backtester as bt_mod
        bt_mod.get_strategy = lambda _name, _cfg: strategy
        try:
            result = self.backtester.run_backtest(
                "TEST", "MACD_RSI", {"allocation": 1000}, self.candles,
            )
        finally:
            bt_mod.get_strategy = original_get

        entries = [t for t in result.get("trades", []) if not t.get("is_exit")]
        self.assertEqual(len(entries), 0)

    def test_backtest_intrabar_stop_loss(self):
        """Long position stopped out when bar low breaches SL."""
        candles = _make_candles(80, start=100.0, drift=0.0)
        for i in range(55, 80):
            candles[i]["close"] = 100.0
            candles[i]["open"] = 100.0
            candles[i]["high"] = 100.5
            candles[i]["low"] = 99.5
        candles[65]["low"] = 97.0
        candles[65]["high"] = 100.2

        strategy = AlwaysBuyStrategy({})
        import app.services.bots.backtester as bt_mod
        original_get = bt_mod.get_strategy
        bt_mod.get_strategy = lambda _name, _cfg: strategy
        try:
            result = self.backtester.run_backtest(
                "TEST",
                "MACD_RSI",
                {"allocation": 5000, "trailing_stop_percent": 0},
                candles,
            )
        finally:
            bt_mod.get_strategy = original_get

        sl_exits = [t for t in result.get("trades", []) if t.get("reason") == "SL"]
        self.assertGreater(len(sl_exits), 0)

    def test_backtest_progress_callback(self):
        seen: list[tuple[int, int]] = []

        def progress_cb(done: int, total: int) -> None:
            seen.append((done, total))

        result = self.backtester.run_backtest(
            "TEST",
            "MACD_RSI",
            {"allocation": 500, "rsi_length": 14},
            self.candles,
            progress_cb=progress_cb,
        )
        self.assertNotIn("error", result)
        self.assertTrue(seen)
        self.assertEqual(seen[-1][0], seen[-1][1])


if __name__ == "__main__":
    unittest.main()
