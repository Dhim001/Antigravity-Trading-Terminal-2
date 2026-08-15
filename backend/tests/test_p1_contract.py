"""Contract tests — Phase 1 bot identity/reliability.

Covers:
- bot_trades journal idempotency (UNIQUE(order_id) / UNIQUE(signal_id, is_exit))
- PnL authority integrity report (bot_trades exits vs orders.realized_pnl cache)
"""

import os
import tempfile
import unittest


def _fresh_db(name):
    import app.db.connection as db_conn

    tmp = tempfile.mkdtemp()
    old = db_conn.DB_PATH
    db_conn.DB_PATH = os.path.join(tmp, name)
    db_conn._pool = None
    return old


def _restore_db(old):
    import app.db.connection as db_conn

    db_conn._pool = None
    db_conn.DB_PATH = old


class BotTradeIdempotencyTests(unittest.TestCase):
    def test_duplicate_order_id_ignored(self):
        from app.database import get_connection, init_db
        from app.services.bots.analytics import get_trades, record_trade

        old = _fresh_db("idem_order.db")
        try:
            init_db()
            conn = get_connection()
            conn.cursor().execute(
                "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
                "VALUES ('b1', 'x', 'BTCUSDT', '1m', 'RUNNING', 1000, '{}')"
            )
            conn.commit()
            conn.close()

            first = record_trade("b1", "ord-1", "BTCUSDT", "BUY", 1.0, 100.0, signal_id="s1")
            dup = record_trade("b1", "ord-1", "BTCUSDT", "BUY", 1.0, 100.0, signal_id="s1")
            self.assertTrue(first)
            self.assertFalse(dup)

            trades = get_trades("b1")
            self.assertEqual(len(trades), 1)
        finally:
            _restore_db(old)

    def test_duplicate_signal_leg_ignored(self):
        from app.database import get_connection, init_db
        from app.services.bots.analytics import get_trades, record_trade

        old = _fresh_db("idem_signal.db")
        try:
            init_db()
            conn = get_connection()
            conn.cursor().execute(
                "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
                "VALUES ('b2', 'x', 'ETHUSDT', '1m', 'RUNNING', 1000, '{}')"
            )
            conn.commit()
            conn.close()

            # Same signal+is_exit twice (reconcile + live path double-record).
            record_trade("b2", None, "ETHUSDT", "SELL", 2.0, 50.0, signal_id="sig-9", is_exit=True, pnl=3.0)
            dup = record_trade("b2", "ord-9", "ETHUSDT", "SELL", 2.0, 50.0, signal_id="sig-9", is_exit=True, pnl=3.0)
            self.assertFalse(dup)

            trades = get_trades("b2")
            self.assertEqual(len(trades), 1)
            # Exit + entry share a signal_id but differ on is_exit — both allowed.
            record_trade("b2", "ord-9b", "ETHUSDT", "BUY", 2.0, 48.0, signal_id="sig-9", is_exit=False)
            self.assertEqual(len(get_trades("b2")), 2)
        finally:
            _restore_db(old)


class PnlAuthorityIntegrityTests(unittest.TestCase):
    def test_integrity_ok_when_cache_covers_bot_exits(self):
        from app.database import get_connection, init_db
        from app.services.fifo_pnl import pnl_authority_integrity

        old = _fresh_db("integrity_ok.db")
        try:
            init_db()
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
                "VALUES ('b1', 'x', 'ADAUSDT', '1m', 'STOPPED', 1000, '{}')"
            )
            cur.execute(
                "INSERT INTO bot_trades (bot_id, order_id, symbol, side, quantity, price, pnl, is_exit) "
                "VALUES ('b1', 'o1', 'ADAUSDT', 'SELL', 100, 1.0, 5.0, 1)"
            )
            cur.execute(
                "INSERT INTO orders (id, symbol, type, side, price, quantity, status, filled_quantity, average_fill_price, realized_pnl) "
                "VALUES ('o1', 'ADAUSDT', 'MARKET', 'SELL', 1.0, 100, 'FILLED', 100, 1.0, 5.0)"
            )
            conn.commit()
            report = pnl_authority_integrity(cur)
            self.assertTrue(report["ok"])
            self.assertEqual(report["diverged_symbols"], {})
            conn.close()
        finally:
            _restore_db(old)

    def test_integrity_flags_cache_below_bot_truth(self):
        from app.database import get_connection, init_db
        from app.services.fifo_pnl import pnl_authority_integrity

        old = _fresh_db("integrity_bad.db")
        try:
            init_db()
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
                "VALUES ('b1', 'x', 'ADAUSDT', '1m', 'STOPPED', 1000, '{}')"
            )
            # Bot journal says +25 realized; orders cache only shows +2.
            cur.execute(
                "INSERT INTO bot_trades (bot_id, order_id, symbol, side, quantity, price, pnl, is_exit) "
                "VALUES ('b1', 'o1', 'ADAUSDT', 'SELL', 100, 1.0, 25.0, 1)"
            )
            cur.execute(
                "INSERT INTO orders (id, symbol, type, side, price, quantity, status, filled_quantity, average_fill_price, realized_pnl) "
                "VALUES ('o1', 'ADAUSDT', 'MARKET', 'SELL', 1.0, 100, 'FILLED', 100, 1.0, 2.0)"
            )
            conn.commit()
            report = pnl_authority_integrity(cur)
            self.assertFalse(report["ok"])
            self.assertIn("ADAUSDT", report["diverged_symbols"])
            self.assertAlmostEqual(report["diverged_symbols"]["ADAUSDT"]["delta"], 23.0)
            conn.close()
        finally:
            _restore_db(old)


if __name__ == "__main__":
    unittest.main()
