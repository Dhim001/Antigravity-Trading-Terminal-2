"""Tests for bot runtime checkpoint restore."""

import unittest

from app.database import get_connection, init_db
from app.services.bots.manager import BotManagerService


class _StubOms:
    def get_account_data(self):
        return {"balances": {"USD": {"balance": 10000}}, "positions": {}}


class TestCheckpointRestore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM bots")
        cur.execute(
            """
            INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config, execution_mode)
            VALUES ('bot-cp', 'MACD_RSI', 'AAPL', '1m', 'PAUSED', 1000, '{}', 'BAR_CLOSE')
            """
        )
        conn.commit()
        conn.close()

    def test_restore_resumes_running_bots(self):
        mgr = BotManagerService(_StubOms(), None, None)
        mgr.load_bots_from_db()
        restored = mgr.restore_runtime_checkpoint({
            "bot-cp": {
                "status": "RUNNING",
                "last_signal_bar_time": 12345,
                "last_signal_at": "2026-01-01T00:00:00Z",
            },
        })
        self.assertEqual(restored, 1)
        self.assertEqual(mgr.active_bots["bot-cp"]["status"], "RUNNING")
        self.assertEqual(mgr.active_bots["bot-cp"]["last_signal_bar_time"], 12345)

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT status FROM bots WHERE id = 'bot-cp'")
        row = cur.fetchone()
        conn.close()
        status = row["status"] if isinstance(row, dict) else row[0]
        self.assertEqual(status, "RUNNING")


if __name__ == "__main__":
    unittest.main()
