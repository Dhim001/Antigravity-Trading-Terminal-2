"""Tests for bracket / OCO helpers."""

import unittest
import sqlite3

from app.services.order_bracket import (
    create_oco_exit_orders,
    is_bracket_request,
    resolve_bracket_levels,
    cancel_oco_group,
)


class OrderBracketTests(unittest.TestCase):
    def test_is_bracket_with_sl(self):
        self.assertTrue(is_bracket_request({"stop_loss_price": 99.0}))

    def test_is_bracket_disabled(self):
        self.assertFalse(is_bracket_request({"stop_loss_price": 99.0, "bracket": False}))

    def test_is_bracket_explicit_with_trailing_only(self):
        self.assertTrue(is_bracket_request({"bracket": True, "trailing_stop_percent": 2.0}))

    def test_is_bracket_trailing_field(self):
        self.assertTrue(is_bracket_request({"trailing_stop_percent": 2.0}))

    def test_resolve_levels_buy(self):
        sl_pct, tp_pct, sl_px, tp_px = resolve_bracket_levels(
            "BUY", 100.0, stop_loss_percent=2.0, take_profit_percent=4.0,
        )
        self.assertAlmostEqual(sl_px, 98.0)
        self.assertAlmostEqual(tp_px, 104.0)
        self.assertIsNotNone(sl_pct)
        self.assertIsNotNone(tp_pct)

    def test_create_oco_legs(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE orders (
                id TEXT PRIMARY KEY, symbol TEXT, type TEXT, side TEXT, price REAL,
                quantity REAL, status TEXT, filled_quantity REAL, average_fill_price REAL,
                stop_loss_percent REAL, take_profit_percent REAL, bot_id TEXT, signal_id TEXT,
                order_group_id TEXT, leg_type TEXT, oco_group_id TEXT,
                stop_loss_price REAL, take_profit_price REAL
            )
        """)
        ids = create_oco_exit_orders(
            cur,
            symbol="AAPL",
            quantity=10,
            position_size=10,
            oco_group_id="oco-1",
            order_group_id="grp-1",
            stop_loss_price=95.0,
            take_profit_price=110.0,
            parent_order_id="entry-1",
        )
        self.assertEqual(len(ids), 2)
        cur.execute("SELECT COUNT(*) AS c FROM orders WHERE status = 'OCO_ACTIVE'")
        self.assertEqual(cur.fetchone()["c"], 2)
        cancel_oco_group(cur, "oco-1", except_leg="SL")
        cur.execute("SELECT status, leg_type FROM orders ORDER BY leg_type")
        rows = {row["leg_type"]: row["status"] for row in cur.fetchall()}
        self.assertEqual(rows["SL"], "OCO_ACTIVE")
        self.assertEqual(rows["TP"], "CANCELED")


if __name__ == "__main__":
    unittest.main()
