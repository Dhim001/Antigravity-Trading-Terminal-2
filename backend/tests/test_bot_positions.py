"""Per-bot positions, SL/TP analytics, and pending-fill reconciliation."""

import asyncio
import os
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""

_TEST_DIR = tempfile.mkdtemp()
import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "bot_positions_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH

from app.database import get_connection, init_db  # noqa: E402
from app.services.bots import analytics as bot_analytics  # noqa: E402
from app.services.bots import positions as bot_positions  # noqa: E402


class BotPositionsTests(unittest.TestCase):
    def setUp(self):
        if os.path.exists(db_conn.DB_PATH):
            os.remove(db_conn.DB_PATH)
        init_db()
        conn = get_connection()
        cursor = conn.cursor()
        self.bot_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.bot_id, "ema_cross", "BTCUSDT", "1m", "RUNNING", 10000, "{}"),
        )
        conn.commit()
        conn.close()

    def test_apply_fill_tracks_long_and_exit(self):
        bot_positions.apply_fill(self.bot_id, "BTCUSDT", "BUY", 1.0, 100.0)
        pos = bot_positions.get_bot_position(self.bot_id, "BTCUSDT")
        self.assertAlmostEqual(pos["size"], 1.0)
        self.assertAlmostEqual(pos["avg_price"], 100.0)

        bot_positions.apply_fill(self.bot_id, "BTCUSDT", "SELL", 1.0, 110.0)
        pos = bot_positions.get_bot_position(self.bot_id, "BTCUSDT")
        self.assertAlmostEqual(pos["size"], 0.0)

    def test_symbol_owners_payload(self):
        bot_positions.apply_fill(self.bot_id, "BTCUSDT", "BUY", 2.5, 50.0)
        owners = bot_positions.owners_for_account_payload("BTCUSDT")
        self.assertEqual(len(owners), 1)
        self.assertEqual(owners[0]["bot_id"], self.bot_id)
        self.assertAlmostEqual(owners[0]["size"], 2.5)


class PendingFillReconcileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        if os.path.exists(db_conn.DB_PATH):
            os.remove(db_conn.DB_PATH)
        init_db()
        conn = get_connection()
        cursor = conn.cursor()
        self.bot_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.bot_id, "ema_cross", "BTCUSDT", "1m", "RUNNING", 10000, "{}"),
        )
        conn.commit()
        conn.close()

        self.oms = MagicMock()
        self.oms.get_trade_history.return_value = [
            {
                "id": "broker-123",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "quantity": 1.0,
                "filled_quantity": 1.0,
                "average_fill_price": 42000.0,
                "status": "FILLED",
            }
        ]
        self.oms.get_account_data.return_value = {"balances": {}, "positions": {}, "orders": []}
        from app.services.bots.manager import BotManagerService

        self.manager = BotManagerService(self.oms, MagicMock(), AsyncMock())

    async def test_reconcile_confirms_pending_by_order_id(self):
        bot_analytics.record_pending_fill(
            self.bot_id,
            "broker-123",
            "BTCUSDT",
            "BUY",
            1.0,
            41900.0,
            signal_id="sig-1",
            is_exit=False,
        )
        confirmed = await self.manager.reconcile_pending_fills()
        self.assertEqual(confirmed, 1)
        trades = bot_analytics.get_trades(self.bot_id)
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0]["price"], 42000.0)
        self.assertEqual(bot_analytics.list_pending_fills(), [])


class SignalBarTimeTests(unittest.TestCase):
    def test_signal_bar_time_from_id(self):
        sid = "550e8400-e29b-41d4-a716-446655440000:1739123460:BUY"
        self.assertEqual(bot_analytics.signal_bar_time_from_id(sid), 1739123460)
        self.assertIsNone(bot_analytics.signal_bar_time_from_id("bot:sltp:order-1"))
        self.assertIsNone(bot_analytics.signal_bar_time_from_id(None))


if __name__ == "__main__":
    unittest.main()
