"""Batch bot stats aggregation tests."""

import os
import tempfile
import unittest
import uuid

import app.db.connection as db_conn
from app.database import get_connection, init_db
from app.services.bots import analytics as bot_analytics


class BatchBotStatsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        db_conn.DB_PATH = os.path.join(self._tmpdir, "stats.db")
        db_conn._pool = None
        init_db()
        conn = get_connection()
        cursor = conn.cursor()
        self.bot_a = str(uuid.uuid4())
        self.bot_b = str(uuid.uuid4())
        for bid in (self.bot_a, self.bot_b):
            cursor.execute(
                "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (bid, "MACD_RSI", "BTCUSDT", "1m", "RUNNING", 1000, "{}"),
            )
        conn.commit()
        conn.close()

    def test_get_all_bot_stats_single_query(self):
        bot_analytics.record_trade(
            self.bot_a, "o1", "BTCUSDT", "BUY", 1.0, 100.0, is_exit=False,
        )
        bot_analytics.record_trade(
            self.bot_a, "o2", "BTCUSDT", "SELL", 1.0, 110.0, pnl=10.0, is_exit=True,
        )
        bot_analytics.record_trade(
            self.bot_b, "o3", "ETHUSDT", "BUY", 1.0, 50.0, is_exit=False,
        )

        stats = bot_analytics.get_all_bot_stats([self.bot_a, self.bot_b])
        self.assertEqual(stats[self.bot_a]["trade_count"], 2)
        self.assertEqual(stats[self.bot_a]["exit_count"], 1)
        self.assertEqual(stats[self.bot_a]["total_pnl"], 10.0)
        self.assertEqual(stats[self.bot_b]["trade_count"], 1)


if __name__ == "__main__":
    unittest.main()
