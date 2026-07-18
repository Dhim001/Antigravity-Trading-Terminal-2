"""Risk gate notional caps — allocation vs global MAX_ORDER_VALUE."""

import unittest
from unittest.mock import patch

from app.config import MAX_ORDER_VALUE
from app.services.bots.risk_gate import RiskGate, get_bot_entry_hold


class RiskGateNotionalCapTests(unittest.TestCase):
    def setUp(self):
        self.gate = RiskGate()
        self._window_patcher = patch(
            "app.services.bots.risk_gate.is_no_trade_window",
            return_value=(False, ""),
        )
        self._gates_patcher = patch(
            "app.services.altdata.event_policy.check_entry_gates",
            return_value=(True, None, None),
        )
        self._window_patcher.start()
        self._gates_patcher.start()

    def tearDown(self):
        self._window_patcher.stop()
        self._gates_patcher.stop()

    def test_allocation_cap_applies_before_max_order_value(self):
        """Small bot allocation must win over the global $50k order ceiling."""
        bot = {
            "id": "bot-1",
            "status": "RUNNING",
            "allocation": 1000.0,
            "config": {"direction_mode": "BOTH"},
        }
        price = 1767.80
        qty = 28.2837  # ~$50k notional — oversized for $1k allocation

        decision = self.gate.validate_trade(
            bot,
            "SELL",
            qty,
            price,
            is_exit=False,
            daily_pnl=0.0,
            position_size=0.0,
        )

        self.assertTrue(decision.allowed)
        self.assertIsNotNone(decision.quantity)
        self.assertAlmostEqual(decision.quantity, 1000.0 / price, places=4)
        self.assertLess(decision.quantity * price, MAX_ORDER_VALUE)
        self.assertIn("allocation", decision.reason.lower())

    def test_max_order_value_cap_when_allocation_higher(self):
        bot = {
            "id": "bot-2",
            "status": "RUNNING",
            "allocation": 100_000.0,
            "config": {},
        }
        price = 100.0
        qty = 600.0  # $60k notional

        decision = self.gate.validate_trade(
            bot,
            "BUY",
            qty,
            price,
            is_exit=False,
            daily_pnl=0.0,
            position_size=0.0,
        )

        self.assertTrue(decision.allowed)
        self.assertAlmostEqual(decision.quantity, MAX_ORDER_VALUE / price, places=6)
        self.assertIn("MAX_ORDER_VALUE", decision.reason)


class BotEntryHoldTests(unittest.TestCase):
    # 2026-07-10T12:00:00Z — 120s after 11:58Z exit fixtures below
    _NOW = 1_783_684_800.0

    @patch("app.services.bots.analytics.get_recent_consecutive_losses", return_value=3)
    @patch("app.services.bots.analytics.last_exit_timestamp", return_value="2026-07-10T11:58:00Z")
    def test_cooloff_hold_payload(self, _last_exit, _streak):
        bot = {
            "id": "bot-cool",
            "status": "RUNNING",
            "config": {"loss_cooloff_sec": 300, "max_consecutive_losses": 5},
        }
        with patch("app.services.bots.risk_gate.time.time", return_value=self._NOW):
            hold = get_bot_entry_hold(bot)
        self.assertIsNotNone(hold)
        self.assertEqual(hold["kind"], "cooloff")
        self.assertGreater(hold["remaining_sec"], 0)
        self.assertIn("cooloff_until", hold)

    @patch("app.services.bots.analytics.get_recent_consecutive_losses", return_value=3)
    @patch("app.services.bots.analytics.last_exit_timestamp", return_value="2026-07-10T11:58:00Z")
    def test_cooloff_hold_while_paused(self, _last_exit, _streak):
        bot = {
            "id": "bot-paused-cool",
            "status": "PAUSED",
            "config": {"loss_cooloff_sec": 300, "max_consecutive_losses": 5},
        }
        with patch("app.services.bots.risk_gate.time.time", return_value=self._NOW):
            hold = get_bot_entry_hold(bot)
        self.assertIsNotNone(hold)
        self.assertEqual(hold["kind"], "cooloff")

    @patch("app.services.bots.analytics.get_recent_consecutive_losses", return_value=5)
    @patch(
        "app.services.bots.analytics.last_exit_timestamp",
        return_value="2026-07-10T11:58:00Z",
    )
    def test_streak_limit_hold_payload(self, _last_exit, _streak):
        bot = {
            "id": "bot-streak",
            "status": "PAUSED",
            "config": {"max_consecutive_losses": 5, "loss_cooloff_sec": 300},
        }
        with patch("app.services.bots.risk_gate.time.time", return_value=self._NOW):
            hold = get_bot_entry_hold(bot)
        self.assertIsNotNone(hold)
        self.assertEqual(hold["kind"], "streak_limit")
        self.assertEqual(hold["consecutive_losses"], 5)
        self.assertGreater(hold["remaining_sec"], 0)

    @patch("app.services.bots.analytics.get_recent_consecutive_losses", return_value=5)
    @patch(
        "app.services.bots.analytics.last_exit_timestamp",
        return_value="2026-07-10T11:00:00Z",
    )
    def test_streak_limit_clears_after_cooloff(self, _last_exit, _streak):
        bot = {
            "id": "bot-streak-expired",
            "status": "RUNNING",
            "config": {"max_consecutive_losses": 5, "loss_cooloff_sec": 300},
        }
        with patch("app.services.bots.risk_gate.time.time", return_value=self._NOW):
            hold = get_bot_entry_hold(bot)
        self.assertIsNone(hold)

    @patch("app.services.bots.analytics.get_recent_consecutive_losses", return_value=5)
    @patch(
        "app.services.bots.analytics.last_exit_timestamp",
        return_value="2026-07-10T11:58:00Z",
    )
    def test_streak_limit_cleared_by_resume_ack(self, _last_exit, _streak):
        bot = {
            "id": "bot-streak-ack",
            "status": "RUNNING",
            "config": {
                "max_consecutive_losses": 5,
                "loss_cooloff_sec": 300,
                "streak_hold_cleared_at": self._NOW,
            },
        }
        with patch("app.services.bots.risk_gate.time.time", return_value=self._NOW):
            hold = get_bot_entry_hold(bot)
        self.assertIsNone(hold)

    @patch("app.services.bots.analytics.get_recent_consecutive_losses", return_value=0)
    def test_drawdown_hold_payload(self, _streak):
        bot = {
            "id": "bot-dd",
            "status": "PAUSED",
            "allocation": 1000.0,
            "config": {"max_drawdown_pct": 10.0},
        }
        hold = get_bot_entry_hold(bot, total_pnl=-150.0)
        self.assertIsNotNone(hold)
        self.assertEqual(hold["kind"], "drawdown")
        self.assertAlmostEqual(hold["drawdown_pct"], 15.0)
        self.assertEqual(hold["max_drawdown_pct"], 10.0)

    @patch("app.services.bots.analytics.get_recent_consecutive_losses", return_value=0)
    def test_drawdown_hold_skipped_when_under_limit(self, _streak):
        bot = {
            "id": "bot-ok",
            "status": "RUNNING",
            "allocation": 1000.0,
            "config": {"max_drawdown_pct": 20.0},
        }
        hold = get_bot_entry_hold(bot, total_pnl=-50.0)
        self.assertIsNone(hold)


class RiskGateBacktestModeTests(unittest.TestCase):
    """Backtests must not inherit live desk concentration / kill-switch latches."""

    def setUp(self):
        self.gate = RiskGate()
        self._window_patcher = patch(
            "app.services.bots.risk_gate.is_no_trade_window",
            return_value=(False, ""),
        )
        self._gates_patcher = patch(
            "app.services.altdata.event_policy.check_entry_gates",
            return_value=(True, None, None),
        )
        self._window_patcher.start()
        self._gates_patcher.start()

    def tearDown(self):
        self._window_patcher.stop()
        self._gates_patcher.stop()

    def test_backtest_skips_live_symbol_concentration(self):
        bot = {
            "status": "RUNNING",
            "allocation": 1000.0,
            "config": {"direction_mode": "BOTH", "max_bots_per_symbol": 1},
            "symbol": "BNBUSDT",
        }
        with patch(
            "app.services.bots.analytics.get_active_bots_for_symbol",
            return_value=3,
        ) as active:
            live = self.gate.validate_trade(
                bot,
                "BUY",
                1.0,
                600.0,
                is_exit=False,
                daily_pnl=0.0,
                position_size=0.0,
                backtest=False,
            )
            bt = self.gate.validate_trade(
                bot,
                "BUY",
                1.0,
                600.0,
                is_exit=False,
                daily_pnl=0.0,
                position_size=0.0,
                backtest=True,
            )

        self.assertFalse(live.allowed)
        self.assertIn("Per-symbol limit", live.reason)
        self.assertTrue(bt.allowed)
        self.assertEqual(active.call_count, 1)

    def test_backtest_skips_kill_switch(self):
        bot = {
            "status": "RUNNING",
            "allocation": 1000.0,
            "config": {"direction_mode": "BOTH"},
            "symbol": "BNBUSDT",
        }
        with patch(
            "app.services.bots.risk_gate.risk_state_store.is_kill_switch_tripped",
            return_value=True,
        ):
            live = self.gate.validate_trade(
                bot,
                "BUY",
                1.0,
                600.0,
                is_exit=False,
                daily_pnl=0.0,
                position_size=0.0,
                backtest=False,
            )
            bt = self.gate.validate_trade(
                bot,
                "BUY",
                1.0,
                600.0,
                is_exit=False,
                daily_pnl=0.0,
                position_size=0.0,
                backtest=True,
            )
        self.assertFalse(live.allowed)
        self.assertTrue(bt.allowed)


if __name__ == "__main__":
    unittest.main()
