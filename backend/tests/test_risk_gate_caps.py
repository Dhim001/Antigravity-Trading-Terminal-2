"""Risk gate notional caps — allocation vs global MAX_ORDER_VALUE."""

import unittest
from unittest.mock import patch

from app.config import MAX_ORDER_VALUE
from app.services.bots.risk_gate import RiskGate


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


if __name__ == "__main__":
    unittest.main()
