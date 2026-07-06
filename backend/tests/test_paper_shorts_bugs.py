"""Regression tests for paper-short edge cases."""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import MagicMock

import app.db.connection as db_conn
from app.database import get_connection, init_db
from app.services.fifo_pnl import apply_fill_to_queues
from app.services.sim_oms import SimulatedOMSService


class PaperShortsBugTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._old_path = db_conn.DB_PATH
        db_conn.DB_PATH = os.path.join(self._tmpdir, "paper_shorts_bugs.db")
        db_conn._pool = None
        init_db()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO accounts (asset, balance, locked) VALUES ('USDT', 1000, 0)"
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

    def test_cover_short_allowed_when_margin_is_locked(self):
        """Cover should succeed using margin that will be released on fill."""
        sell = asyncio.run(
            self.oms.place_order({
                "symbol": "BTCUSDT",
                "type": "MARKET",
                "side": "SELL",
                "quantity": 9.5,
            })
        )
        self.assertEqual(sell["status"], "success", sell.get("message"))

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT balance, locked FROM accounts WHERE asset = 'USDT'")
        row = cursor.fetchone()
        conn.close()
        self.assertAlmostEqual(float(row["locked"]), 950.0)
        self.assertAlmostEqual(float(row["balance"]) - float(row["locked"]), 50.0)

        cover = asyncio.run(
            self.oms.place_order({
                "symbol": "BTCUSDT",
                "type": "MARKET",
                "side": "BUY",
                "quantity": 1.0,
            })
        )
        self.assertEqual(cover["status"], "success", cover.get("message"))

    def test_flip_long_to_short_fifo_pnl_only_on_closed_long(self):
        queues: dict = {}
        apply_fill_to_queues(queues, "BTCUSDT", "BUY", 100.0, 1.0)
        cost_basis, realized_pnl = apply_fill_to_queues(queues, "BTCUSDT", "SELL", 110.0, 2.0)

        self.assertAlmostEqual(realized_pnl, 10.0)
        self.assertAlmostEqual(cost_basis, 100.0)
        self.assertEqual(len(queues["BTCUSDT"]["long"]), 0)
        self.assertEqual(len(queues["BTCUSDT"]["short"]), 1)
        self.assertAlmostEqual(queues["BTCUSDT"]["short"][0][1], 1.0)

    def test_flip_short_to_long_fifo_pnl_only_on_closed_short(self):
        queues: dict = {}
        apply_fill_to_queues(queues, "BTCUSDT", "SELL", 100.0, 1.0)
        cost_basis, realized_pnl = apply_fill_to_queues(queues, "BTCUSDT", "BUY", 90.0, 2.0)

        self.assertAlmostEqual(realized_pnl, 10.0)
        self.assertAlmostEqual(cost_basis, 100.0)
        self.assertEqual(len(queues["BTCUSDT"]["short"]), 0)
        self.assertEqual(len(queues["BTCUSDT"]["long"]), 1)
        self.assertAlmostEqual(queues["BTCUSDT"]["long"][0][1], 1.0)


if __name__ == "__main__":
    unittest.main()
