"""Unit tests for time-based risk controls."""

from __future__ import annotations

import os
import sys
import unittest
import unittest.mock
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.bots import time_windows as tw  # noqa: E402


NY = ZoneInfo("America/New_York")


def _ny(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=NY)


class TestNoTradeWindows(unittest.TestCase):
    def test_crypto_exempt_from_no_trade(self):
        blocked, _ = tw.is_no_trade_window(_ny(2026, 6, 23, 9, 32), "BTCUSDT")
        self.assertFalse(blocked)

    def test_equity_blocked_at_open_window(self):
        blocked, reason = tw.is_no_trade_window(_ny(2026, 6, 23, 9, 32), "AAPL")
        self.assertTrue(blocked)
        self.assertIn("No-trade window", reason)

    def test_equity_allowed_mid_session(self):
        blocked, _ = tw.is_no_trade_window(_ny(2026, 6, 23, 11, 0), "AAPL")
        self.assertFalse(blocked)

    def test_equity_blocked_at_close_window(self):
        blocked, _ = tw.is_no_trade_window(_ny(2026, 6, 23, 15, 58), "MSFT")
        self.assertTrue(blocked)

    def test_weekend_no_equity_no_trade(self):
        blocked, _ = tw.is_no_trade_window(_ny(2026, 6, 27, 9, 32), "AAPL")
        self.assertFalse(blocked)


class TestWeekendFlatten(unittest.TestCase):
    def test_crypto_never_flattens(self):
        self.assertFalse(tw.should_flatten_symbol("ETHUSDT", _ny(2026, 6, 27, 12, 0)))

    def test_equity_flattens_on_saturday(self):
        self.assertTrue(tw.should_flatten_symbol("AAPL", _ny(2026, 6, 27, 12, 0)))

    def test_equity_flattens_friday_after_cutoff(self):
        self.assertTrue(tw.should_flatten_symbol("AAPL", _ny(2026, 6, 26, 16, 0)))

    def test_equity_not_flatten_friday_before_cutoff(self):
        self.assertFalse(tw.should_flatten_symbol("AAPL", _ny(2026, 6, 26, 10, 0)))

    def test_equity_not_flatten_weekday(self):
        self.assertFalse(tw.should_flatten_symbol("AAPL", _ny(2026, 6, 24, 16, 0)))

    def test_weekend_bar_time_stable(self):
        sat = _ny(2026, 6, 27, 12, 0)
        sun = _ny(2026, 6, 28, 12, 0)
        self.assertEqual(tw.weekend_flatten_bar_time(sat), tw.weekend_flatten_bar_time(sun))
        self.assertEqual(tw.weekend_flatten_bar_time(sat), 20260626)


class TestRiskGateTimeWindow(unittest.TestCase):
    def test_entry_blocked_during_no_trade(self):
        from app.services.bots.risk_gate import RiskGate

        gate = RiskGate()
        bot = {
            "id": "b1",
            "symbol": "AAPL",
            "status": "RUNNING",
            "allocation": 1000,
        }
        with unittest.mock.patch.object(gate, "_kill_switch_block", return_value=None), unittest.mock.patch(
            "app.services.bots.risk_gate.is_no_trade_window",
            return_value=(True, "No-trade window (test)."),
        ):
            decision = gate.validate_trade(
                bot, "BUY", 1.0, 100.0, is_exit=False, daily_pnl=0, position_size=0
            )
        self.assertFalse(decision.allowed)
        self.assertIn("No-trade window", decision.reason)

    def test_exit_allowed_when_bot_stopped(self):
        from app.services.bots.risk_gate import RiskGate

        gate = RiskGate()
        bot = {
            "id": "b1",
            "symbol": "AAPL",
            "status": "STOPPED",
            "allocation": 1000,
        }
        decision = gate.validate_trade(
            bot, "SELL", 1.0, 100.0, is_exit=True, daily_pnl=0, position_size=5.0
        )
        self.assertTrue(decision.allowed)


if __name__ == "__main__":
    unittest.main()
