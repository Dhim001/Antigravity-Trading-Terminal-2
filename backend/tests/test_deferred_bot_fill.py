"""Ensure bot fills are applied after OMS commit (no nested SQLite writers)."""

import os
import tempfile
import unittest
import uuid
from unittest.mock import MagicMock

import app.db.connection as db_conn
from app.database import get_connection, init_db
from app.services.bots import positions as bot_positions
from app.services.sim_oms import SimulatedOMSService


class DeferredBotFillTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._old_path = db_conn.DB_PATH
        db_conn.DB_PATH = os.path.join(self._tmpdir, "deferred_fill.db")
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
        self.bot_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.bot_id, "MACD_RSI", "BTCUSDT", "1m", "RUNNING", 1000, "{}"),
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

    def test_market_order_applies_bot_slice_after_commit(self):
        import asyncio

        result = asyncio.run(
            self.oms.place_order({
                "symbol": "BTCUSDT",
                "type": "MARKET",
                "side": "BUY",
                "quantity": 0.5,
                "bot_id": self.bot_id,
            })
        )
        self.assertEqual(result.get("status"), "success", result.get("message"))
        slice_pos = bot_positions.get_bot_position(self.bot_id, "BTCUSDT")
        self.assertAlmostEqual(slice_pos["size"], 0.5)
        self.assertAlmostEqual(slice_pos["avg_price"], 100.0)


if __name__ == "__main__":
    unittest.main()
