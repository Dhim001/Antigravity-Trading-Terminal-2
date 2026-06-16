"""Unit tests for pre-trade order preview."""

import unittest
from types import SimpleNamespace

from app.config import MAX_ORDER_VALUE
from app.services.order_preview import preview_order


class _Feed:
    def __init__(self, prices: dict[str, float]):
        self._symbols = {sym: {"price": price} for sym, price in prices.items()}


class _Oms:
    def __init__(self, *, prices=None, balances=None, positions=None):
        self.feed = _Feed(prices or {"BTCUSDT": 50_000.0})
        self._balances = balances or {"USDT": {"balance": 10_000, "locked": 0}}
        self._positions = positions or {"BTCUSDT": {"size": 0.5}}

    def get_account_data(self):
        return {
            "balances": self._balances,
            "positions": self._positions,
            "tickers": {k: {"price": v["price"]} for k, v in self.feed._symbols.items()},
        }


class OrderPreviewTests(unittest.TestCase):
    def test_market_buy_allowed_with_sl_tp(self):
        oms = _Oms()
        result = preview_order(oms, {
            "symbol": "BTCUSDT",
            "type": "MARKET",
            "side": "BUY",
            "quantity": 0.01,
            "stop_loss_price": 49_000,
            "take_profit_price": 52_000,
        })
        self.assertTrue(result["allowed"])
        self.assertEqual(result["notional"], 500.0)
        self.assertEqual(result["stop_loss_price"], 49_000)
        self.assertEqual(result["take_profit_price"], 52_000)
        self.assertIsNotNone(result["risk_reward_ratio"])

    def test_blocked_insufficient_quote(self):
        oms = _Oms(balances={"USDT": {"balance": 100, "locked": 0}})
        result = preview_order(oms, {
            "symbol": "BTCUSDT",
            "type": "MARKET",
            "side": "BUY",
            "quantity": 1,
        })
        self.assertFalse(result["allowed"])
        self.assertIn("Insufficient", result["block_reason"])

    def test_blocked_insufficient_base_on_sell(self):
        oms = _Oms(positions={"BTCUSDT": {"size": 0.01}})
        result = preview_order(oms, {
            "symbol": "BTCUSDT",
            "type": "MARKET",
            "side": "SELL",
            "quantity": 0.5,
        })
        self.assertFalse(result["allowed"])
        self.assertIn("Insufficient", result["block_reason"])

    def test_blocked_max_order_value(self):
        oms = _Oms(balances={"USDT": {"balance": MAX_ORDER_VALUE * 2, "locked": 0}})
        qty = (MAX_ORDER_VALUE / 50_000) + 1
        result = preview_order(oms, {
            "symbol": "BTCUSDT",
            "type": "MARKET",
            "side": "BUY",
            "quantity": qty,
        })
        self.assertFalse(result["allowed"])
        self.assertIn("maximum risk limit", result["block_reason"])

    def test_limit_requires_price(self):
        oms = _Oms()
        result = preview_order(oms, {
            "symbol": "BTCUSDT",
            "type": "LIMIT",
            "side": "BUY",
            "quantity": 0.01,
        })
        self.assertFalse(result["allowed"])
        self.assertIn("Limit price", result["block_reason"])


if __name__ == "__main__":
    unittest.main()
