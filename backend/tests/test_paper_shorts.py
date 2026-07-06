"""Paper short selling on Sim / Massive OMS."""

import asyncio
import os
import tempfile
import unittest
import uuid
from unittest.mock import MagicMock, patch

import app.db.connection as db_conn
from app.database import get_connection, init_db
from app.services.bots import positions as bot_positions
from app.services.sim_oms import SimulatedOMSService


class PaperShortsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._old_path = db_conn.DB_PATH
        db_conn.DB_PATH = os.path.join(self._tmpdir, "paper_shorts.db")
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
        cursor.execute(
            "INSERT OR REPLACE INTO accounts (asset, balance, locked) VALUES ('DOT', 0, 0)"
        )
        self.bot_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.bot_id, "CHART_AGENT", "DOTUSDT", "1m", "RUNNING", 1000, "{}"),
        )
        conn.commit()
        conn.close()

        self.feed = MagicMock()
        self.feed._symbols = {
            "DOTUSDT": {
                "asset": "DOT",
                "quote": "USDT",
                "price": 0.87,
            },
            "BTCUSDT": {
                "asset": "BTC",
                "quote": "USDT",
                "price": 100.0,
            },
        }
        self.oms = SimulatedOMSService(self.feed)

    def tearDown(self):
        db_conn._pool = None
        db_conn.DB_PATH = self._old_path

    def _place(self, symbol, side, qty, order_type="MARKET", price=None):
        if price is not None:
            self.feed._symbols[symbol]["price"] = price
        return asyncio.run(
            self.oms.place_order({
                "symbol": symbol,
                "type": order_type,
                "side": side,
                "quantity": qty,
                "price": price,
            })
        )

    def _position_size(self, symbol):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT size, avg_price FROM positions WHERE symbol = ?", (symbol,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return 0.0, 0.0
        return float(row["size"]), float(row["avg_price"])

    def test_short_entry_from_flat_creates_negative_position(self):
        result = self._place("DOTUSDT", "SELL", 100.0)
        self.assertEqual(result.get("status"), "success", result.get("message"))

        size, avg = self._position_size("DOTUSDT")
        self.assertAlmostEqual(size, -100.0)
        self.assertAlmostEqual(avg, 0.87)

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT balance, locked FROM accounts WHERE asset = 'USDT'")
        row = cursor.fetchone()
        conn.close()
        self.assertAlmostEqual(float(row["balance"]), 100000.0)
        self.assertAlmostEqual(float(row["locked"]), 87.0)

    def test_short_entry_rejected_when_disabled(self):
        with patch("app.services.sim_oms.PAPER_SHORTS_ENABLED", False):
            result = self._place("DOTUSDT", "SELL", 10.0)
        self.assertEqual(result.get("status"), "error")
        self.assertIn("disabled", result.get("message", "").lower())

    def test_short_entry_rejected_insufficient_margin(self):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE accounts SET balance = 10, locked = 0 WHERE asset = 'USDT'")
        conn.commit()
        conn.close()

        result = self._place("DOTUSDT", "SELL", 100.0)
        self.assertEqual(result.get("status"), "error")
        self.assertIn("margin", result.get("message", "").lower())

    def test_cover_short_realizes_pnl(self):
        sell = self._place("BTCUSDT", "SELL", 1.0, price=100.0)
        self.assertEqual(sell["status"], "success")

        buy = self._place("BTCUSDT", "BUY", 1.0, price=90.0)
        self.assertEqual(buy["status"], "success")

        size, _ = self._position_size("BTCUSDT")
        self.assertAlmostEqual(size, 0.0)

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT realized_pnl, cost_basis FROM orders WHERE id = ?",
            (buy["order_id"],),
        )
        row = cursor.fetchone()
        conn.close()

        self.assertAlmostEqual(float(row["cost_basis"]), 100.0)
        self.assertAlmostEqual(float(row["realized_pnl"]), 10.0)

    def test_short_entry_applies_bot_slice(self):
        import asyncio

        result = asyncio.run(
            self.oms.place_order({
                "symbol": "DOTUSDT",
                "type": "MARKET",
                "side": "SELL",
                "quantity": 50.0,
                "bot_id": self.bot_id,
            })
        )
        self.assertEqual(result.get("status"), "success", result.get("message"))

        slice_pos = bot_positions.get_bot_position(self.bot_id, "DOTUSDT")
        self.assertAlmostEqual(slice_pos["size"], -50.0)
        self.assertAlmostEqual(slice_pos["avg_price"], 0.87)

    def test_long_close_still_requires_inventory(self):
        self._place("BTCUSDT", "BUY", 0.5, price=100.0)

        with patch("app.services.sim_oms.PAPER_SHORTS_ENABLED", False):
            result = self._place("BTCUSDT", "SELL", 1.0, price=100.0)
        self.assertEqual(result.get("status"), "error")

    def test_limit_short_entry_locks_margin(self):
        result = self._place("DOTUSDT", "SELL", 20.0, order_type="LIMIT", price=0.90)
        self.assertEqual(result.get("status"), "success", result.get("message"))

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT locked FROM accounts WHERE asset = 'USDT'")
        locked = float(cursor.fetchone()["locked"])
        conn.close()
        self.assertAlmostEqual(locked, 18.0)

        self.feed._symbols["DOTUSDT"]["price"] = 0.95
        self.oms.match_pending_orders()

        size, _ = self._position_size("DOTUSDT")
        self.assertAlmostEqual(size, -20.0)


if __name__ == "__main__":
    unittest.main()
