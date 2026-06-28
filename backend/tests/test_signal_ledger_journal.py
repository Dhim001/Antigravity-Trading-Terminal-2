"""Tests for extended bot_signal_ledger fill journal."""

import unittest

from app.database import get_connection, init_db
from app.services.bots import signal_ledger


class TestSignalLedgerJournal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        signal_ledger.clear_signal_ledger()
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO bots (id, strategy, symbol, timeframe, status, allocation, config)
            VALUES ('bot-j', 'MACD_RSI', 'AAPL', '1m', 'RUNNING', 1000, '{}')
            """
        )
        conn.commit()
        conn.close()

    def test_submitted_to_filled_lifecycle(self):
        sid = "bot-j:1718006400:BUY"
        self.assertTrue(signal_ledger.claim_signal(sid, "bot-j", 1718006400, "BUY"))
        signal_ledger.mark_signal_submitted(
            sid,
            order_id="ord-1",
            broker="LIVE_ALPACA",
            payload={"symbol": "AAPL", "side": "BUY", "quantity": 1.0},
        )
        incomplete = signal_ledger.list_incomplete_signals()
        self.assertEqual(len(incomplete), 1)
        self.assertEqual(incomplete[0]["status"], "submitted")

        signal_ledger.mark_signal_filled(sid, order_id="ord-1")
        self.assertEqual(len(signal_ledger.list_incomplete_signals()), 0)

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT status, order_id FROM bot_signal_ledger WHERE signal_id = ?", (sid,))
        row = cur.fetchone()
        conn.close()
        status = row["status"] if isinstance(row, dict) else row[0]
        order_id = row["order_id"] if isinstance(row, dict) else row[1]
        self.assertEqual(status, "filled")
        self.assertEqual(order_id, "ord-1")

    def test_ambiguous_and_failed_are_terminal(self):
        sid_a = "bot-j:1718006500:BUY"
        sid_f = "bot-j:1718006600:SELL"
        signal_ledger.claim_signal(sid_a, "bot-j", 1718006500, "BUY")
        signal_ledger.mark_signal_ambiguous(sid_a, "timeout")
        signal_ledger.claim_signal(sid_f, "bot-j", 1718006600, "SELL")
        signal_ledger.mark_signal_failed(sid_f, "rejected")

        self.assertFalse(signal_ledger.claim_signal(sid_a, "bot-j", 1718006500, "BUY"))
        self.assertFalse(signal_ledger.claim_signal(sid_f, "bot-j", 1718006600, "SELL"))
        self.assertEqual(len(signal_ledger.list_incomplete_signals()), 0)

    def test_orphaned_claims_reconciled(self):
        sid = "bot-j:1718006700:BUY"
        signal_ledger.claim_signal(sid, "bot-j", 1718006700, "BUY")
        count = signal_ledger.reconcile_orphaned_claims()
        self.assertEqual(count, 1)
        self.assertEqual(len(signal_ledger.list_incomplete_signals()), 0)

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT status FROM bot_signal_ledger WHERE signal_id = ?", (sid,))
        row = cur.fetchone()
        conn.close()
        status = row["status"] if isinstance(row, dict) else row[0]
        self.assertEqual(status, "failed")


if __name__ == "__main__":
    unittest.main()
