"""Tests for DB-backed bot signal ledger."""

import unittest

from app.database import get_connection, init_db
from app.services.bots import signal_ledger


class TestSignalLedger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        signal_ledger.clear_signal_ledger()
        conn = get_connection()
        cur = conn.cursor()
        for bot_id in ("bot-1", "bot-2", "bot-3"):
            cur.execute(
                """
                INSERT OR IGNORE INTO bots (id, strategy, symbol, timeframe, status, allocation, config)
                VALUES (?, 'MACD_RSI', 'AAPL', '1m', 'RUNNING', 1000, '{}')
                """,
                (bot_id,),
            )
        conn.commit()
        conn.close()

    def test_claim_is_idempotent(self):
        sid = "bot-1:1718006400:BUY"
        self.assertTrue(signal_ledger.claim_signal(sid, "bot-1", 1718006400, "BUY"))
        self.assertFalse(signal_ledger.claim_signal(sid, "bot-1", 1718006400, "BUY"))

    def test_release_allows_retry(self):
        sid = "bot-2:1718006500:SELL"
        self.assertTrue(signal_ledger.claim_signal(sid, "bot-2", 1718006500, "SELL"))
        signal_ledger.release_signal(sid)
        self.assertTrue(signal_ledger.claim_signal(sid, "bot-2", 1718006500, "SELL"))

    def test_filled_not_released(self):
        sid = "bot-3:1718006600:BUY"
        signal_ledger.claim_signal(sid, "bot-3", 1718006600, "BUY")
        signal_ledger.mark_signal_filled(sid)
        signal_ledger.release_signal(sid)
        self.assertFalse(signal_ledger.claim_signal(sid, "bot-3", 1718006600, "BUY"))

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT status FROM bot_signal_ledger WHERE signal_id = ?", (sid,))
        row = cur.fetchone()
        conn.close()
        self.assertEqual(row[0] if not isinstance(row, dict) else row["status"], "filled")


if __name__ == "__main__":
    unittest.main()
