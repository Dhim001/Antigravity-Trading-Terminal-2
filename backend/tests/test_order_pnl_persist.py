"""Persist FIFO realized PnL on fill — skip full replay in trade history."""

import asyncio
import os
import tempfile
import unittest
import uuid
from unittest.mock import MagicMock

import app.db.connection as db_conn
from app.database import get_connection, init_db
from app.services.fifo_pnl import backfill_missing_order_pnl, enrich_orders_with_pnl
from app.services.sim_oms import SimulatedOMSService


class OrderPnlPersistTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._old_path = db_conn.DB_PATH
        db_conn.DB_PATH = os.path.join(self._tmpdir, "pnl_persist.db")
        db_conn._pool = None
        init_db()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO accounts (asset, balance, locked) VALUES ('USDT', 100000, 0)"
        )
        cursor.execute(
            "INSERT OR REPLACE INTO accounts (asset, balance, locked) VALUES ('BTC', 0, 0)"
        )
        conn.commit()
        conn.close()

        self.feed = MagicMock()
        self.feed._symbols = {
            "BTCUSDT": {
                "asset": "BTC",
                "quote": "USDT",
                "price": 100.0,
            }
        }
        self.oms = SimulatedOMSService(self.feed)

    def tearDown(self):
        db_conn._pool = None
        db_conn.DB_PATH = self._old_path

    def _place(self, side, qty, price=None):
        if price is not None:
            self.feed._symbols["BTCUSDT"]["price"] = price
        return asyncio.run(
            self.oms.place_order({
                "symbol": "BTCUSDT",
                "type": "MARKET",
                "side": side,
                "quantity": qty,
            })
        )

    def test_second_buy_add_to_position(self):
        """Regression: sqlite3.Row has no .get() — add-to-long must not fail."""
        first = self._place("BUY", 1.0, 100.0)
        self.assertEqual(first["status"], "success")
        second = self._place("BUY", 0.5, 105.0)
        self.assertEqual(second["status"], "success")

        account = self.oms.get_account_data()
        pos = account["positions"]["BTCUSDT"]
        self.assertAlmostEqual(float(pos["size"]), 1.5)
        self.assertAlmostEqual(float(pos["avg_price"]), 101.66666666666667, places=4)

    def test_sell_persists_realized_pnl_on_fill(self):
        buy = self._place("BUY", 1.0, 100.0)
        self.assertEqual(buy["status"], "success")

        sell = self._place("SELL", 1.0, 110.0)
        self.assertEqual(sell["status"], "success")
        sell_id = sell["order_id"]

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT realized_pnl, cost_basis FROM orders WHERE id = ?",
            (sell_id,),
        )
        row = cursor.fetchone()
        conn.close()

        self.assertAlmostEqual(float(row["cost_basis"]), 100.0)
        self.assertAlmostEqual(float(row["realized_pnl"]), 10.0)

    def test_trade_history_uses_stored_pnl(self):
        self._place("BUY", 2.0, 50.0)
        self._place("SELL", 1.0, 60.0)

        history = self.oms.get_trade_history()
        sells = [t for t in history if t["side"] == "SELL"]
        self.assertEqual(len(sells), 1)
        self.assertAlmostEqual(sells[0]["realized_pnl"], 10.0)
        self.assertAlmostEqual(sells[0]["cost_basis"], 50.0)

    def test_backfill_legacy_null_sells(self):
        conn = get_connection()
        cursor = conn.cursor()
        buy_id = str(uuid.uuid4())
        sell_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO orders (id, symbol, type, side, price, quantity, status,
                                filled_quantity, average_fill_price, timestamp)
            VALUES (?, 'BTCUSDT', 'MARKET', 'BUY', NULL, 1, 'FILLED', 1, 80, 1)
            """,
            (buy_id,),
        )
        cursor.execute(
            """
            INSERT INTO orders (id, symbol, type, side, price, quantity, status,
                                filled_quantity, average_fill_price, timestamp)
            VALUES (?, 'BTCUSDT', 'MARKET', 'SELL', NULL, 1, 'FILLED', 1, 90, 2)
            """,
            (sell_id,),
        )
        conn.commit()

        updated = backfill_missing_order_pnl(cursor)
        conn.commit()

        cursor.execute(
            "SELECT realized_pnl, cost_basis FROM orders WHERE id = ?",
            (sell_id,),
        )
        row = cursor.fetchone()
        conn.close()

        self.assertEqual(updated, 1)
        self.assertAlmostEqual(float(row["realized_pnl"]), 10.0)
        self.assertAlmostEqual(float(row["cost_basis"]), 80.0)

    def test_enrich_skips_replay_when_stored(self):
        orders = [
            {
                "symbol": "BTCUSDT", "side": "BUY",
                "average_fill_price": 100, "filled_quantity": 1,
                "realized_pnl": None, "cost_basis": None,
                "price": None, "quantity": 1,
            },
            {
                "symbol": "BTCUSDT", "side": "SELL",
                "average_fill_price": 120, "filled_quantity": 1,
                "realized_pnl": 20.0, "cost_basis": 100.0,
                "price": None, "quantity": 1,
            },
        ]
        enriched = enrich_orders_with_pnl(orders)
        self.assertAlmostEqual(enriched[1]["realized_pnl"], 20.0)
        self.assertAlmostEqual(enriched[1]["cost_basis"], 100.0)


if __name__ == "__main__":
    unittest.main()
