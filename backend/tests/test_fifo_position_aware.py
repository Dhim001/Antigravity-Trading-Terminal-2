"""Position-aware FIFO — short opens must not inherit phantom long-lot PnL."""

import unittest

from app.services.fifo_pnl import (
    align_queues_to_position,
    apply_fill_to_queues,
    enrich_orders_with_pnl,
    rebuild_order_realized_pnl,
)


class PositionAwareFifoTests(unittest.TestCase):
    def test_short_open_flat_has_no_pnl(self):
        queues = {}
        # Stale long lot that should NOT book PnL when position is flat.
        align_queues_to_position(queues, "ADAUSDT", 0.0, 0.0)
        queues["ADAUSDT"]["long"].append([0.19, 10000.0])  # phantom
        cb, pnl = apply_fill_to_queues(
            queues, "ADAUSDT", "SELL", 0.19715, 15205.0, position_size=0.0,
        )
        self.assertIsNone(pnl)
        self.assertIsNone(cb)
        self.assertEqual(len(queues["ADAUSDT"]["short"]), 1)
        # Phantom long ignored for the open leg (not consumed).
        self.assertEqual(len(queues["ADAUSDT"]["long"]), 1)

    def test_long_close_still_realizes(self):
        queues = {}
        align_queues_to_position(queues, "BTCUSDT", 1.0, 100.0)
        cb, pnl = apply_fill_to_queues(
            queues, "BTCUSDT", "SELL", 110.0, 1.0, position_size=1.0,
        )
        self.assertAlmostEqual(pnl, 10.0)
        self.assertAlmostEqual(cb, 100.0)

    def test_short_cover_realizes(self):
        queues = {}
        align_queues_to_position(queues, "ADAUSDT", -1000.0, 0.20)
        cb, pnl = apply_fill_to_queues(
            queues, "ADAUSDT", "BUY", 0.19, 1000.0, position_size=-1000.0,
        )
        self.assertAlmostEqual(pnl, 10.0)
        self.assertAlmostEqual(cb, 0.20)

    def test_enrich_does_not_invent_pnl_on_null_entry(self):
        """Read path must not book phantom closes onto NULL entry fills."""
        orders = [
            {
                "symbol": "ADAUSDT", "side": "BUY",
                "average_fill_price": 0.19, "filled_quantity": 30000,
                "realized_pnl": None, "cost_basis": None,
                "price": None, "quantity": 30000,
            },
            {
                "symbol": "ADAUSDT", "side": "SELL",
                "average_fill_price": 0.197, "filled_quantity": 15000,
                "realized_pnl": None, "cost_basis": None,
                "price": None, "quantity": 15000,
            },
        ]
        enriched = enrich_orders_with_pnl(orders)
        self.assertIsNone(enriched[1]["realized_pnl"])
        self.assertGreater(enriched[1]["trade_value"], 0)


class RebuildOrderPnlTests(unittest.TestCase):
    def test_rebuild_nulls_open_and_keeps_exit(self):
        import os
        import tempfile

        import app.db.connection as db_conn
        from app.database import get_connection, init_db

        tmp = tempfile.mkdtemp()
        old = db_conn.DB_PATH
        db_conn.DB_PATH = os.path.join(tmp, "rebuild_pnl.db")
        db_conn._pool = None
        try:
            init_db()
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO orders (
                    id, symbol, type, side, price, quantity, status,
                    filled_quantity, average_fill_price, timestamp, realized_pnl
                ) VALUES
                ('o1', 'BTCUSDT', 'MARKET', 'BUY', 100, 1, 'FILLED', 1, 100, '2026-01-01 00:00:00', 999),
                ('o2', 'BTCUSDT', 'MARKET', 'SELL', 110, 1, 'FILLED', 1, 110, '2026-01-01 00:01:00', NULL),
                ('o3', 'BTCUSDT', 'MARKET', 'SELL', 105, 1, 'FILLED', 1, 105, '2026-01-01 00:02:00', 50)
                """
            )
            cur.execute(
                "INSERT INTO positions (symbol, size, avg_price) VALUES ('BTCUSDT', -1, 105)"
            )
            conn.commit()
            n = rebuild_order_realized_pnl(cur)
            conn.commit()
            self.assertGreater(n, 0)
            rows = {
                r["id"]: r
                for r in cur.execute(
                    "SELECT id, realized_pnl FROM orders ORDER BY id"
                )
            }
            self.assertIsNone(rows["o1"]["realized_pnl"])
            self.assertAlmostEqual(float(rows["o2"]["realized_pnl"]), 10.0)
            self.assertIsNone(rows["o3"]["realized_pnl"])
            conn.close()
        finally:
            db_conn._pool = None
            db_conn.DB_PATH = old

    def test_bot_trade_override_wins_over_phantom_fifo(self):
        import os
        import tempfile

        import app.db.connection as db_conn
        from app.database import get_connection, init_db

        tmp = tempfile.mkdtemp()
        old = db_conn.DB_PATH
        db_conn.DB_PATH = os.path.join(tmp, "bot_override_pnl.db")
        db_conn._pool = None
        try:
            init_db()
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config)
                VALUES ('bot1', 'x', 'ADAUSDT', '1m', 'STOPPED', 1000, '{}')
                """
            )
            cur.execute(
                """
                INSERT INTO orders (
                    id, symbol, type, side, price, quantity, status,
                    filled_quantity, average_fill_price, timestamp, realized_pnl
                ) VALUES
                ('buy_orphan', 'ADAUSDT', 'MARKET', 'BUY', 0.19, 30000, 'FILLED',
                 30000, 0.19, '2026-01-01 00:00:00', NULL),
                ('short_open', 'ADAUSDT', 'MARKET', 'SELL', 0.197, 15000, 'FILLED',
                 15000, 0.197, '2026-01-01 00:01:00', 99),
                ('cover', 'ADAUSDT', 'MARKET', 'BUY', 0.198, 15000, 'FILLED',
                 15000, 0.198, '2026-01-01 00:02:00', NULL)
                """
            )
            cur.execute(
                """
                INSERT INTO bot_trades (
                    bot_id, order_id, symbol, side, quantity, price, pnl, is_exit, timestamp
                ) VALUES
                ('bot1', 'short_open', 'ADAUSDT', 'SELL', 15000, 0.197, NULL, 0, '2026-01-01 00:01:00'),
                ('bot1', 'cover', 'ADAUSDT', 'BUY', 15000, 0.198, -15.0, 1, '2026-01-01 00:02:00')
                """
            )
            conn.commit()
            rebuild_order_realized_pnl(cur)
            conn.commit()
            rows = {
                r["id"]: r
                for r in cur.execute("SELECT id, realized_pnl FROM orders")
            }
            self.assertIsNone(rows["short_open"]["realized_pnl"])
            self.assertAlmostEqual(float(rows["cover"]["realized_pnl"]), -15.0)
            conn.close()
        finally:
            db_conn._pool = None
            db_conn.DB_PATH = old

    def test_drift_window_scrubbed_when_live_flat(self):
        import os
        import tempfile

        import app.db.connection as db_conn
        from app.database import get_connection, init_db

        tmp = tempfile.mkdtemp()
        old = db_conn.DB_PATH
        db_conn.DB_PATH = os.path.join(tmp, "drift_pnl.db")
        db_conn._pool = None
        try:
            init_db()
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config)
                VALUES ('bot1', 'x', 'ADAUSDT', '1m', 'STOPPED', 1000, '{}')
                """
            )
            # Natural flat, then orphan buy stack, then short open + cover.
            # Live position stays flat (orphan cleared without a sell fill).
            cur.execute(
                """
                INSERT INTO orders (
                    id, symbol, type, side, price, quantity, status,
                    filled_quantity, average_fill_price, timestamp, realized_pnl
                ) VALUES
                ('b1', 'ADAUSDT', 'MARKET', 'BUY', 0.19, 1000, 'FILLED',
                 1000, 0.19, '2026-01-01 00:00:00', NULL),
                ('s1', 'ADAUSDT', 'MARKET', 'SELL', 0.20, 1000, 'FILLED',
                 1000, 0.20, '2026-01-01 00:01:00', NULL),
                ('orphan', 'ADAUSDT', 'MARKET', 'BUY', 0.19, 30000, 'FILLED',
                 30000, 0.19, '2026-01-01 00:02:00', NULL),
                ('short_open', 'ADAUSDT', 'MARKET', 'SELL', 0.197, 15000, 'FILLED',
                 15000, 0.197, '2026-01-01 00:03:00', 99),
                ('cover', 'ADAUSDT', 'MARKET', 'BUY', 0.198, 15000, 'FILLED',
                 15000, 0.198, '2026-01-01 00:04:00', NULL)
                """
            )
            cur.execute(
                "INSERT INTO positions (symbol, size, avg_price) VALUES ('ADAUSDT', 0, 0)"
            )
            cur.execute(
                """
                INSERT INTO bot_trades (
                    bot_id, order_id, symbol, side, quantity, price, pnl, is_exit, timestamp
                ) VALUES
                ('bot1', 's1', 'ADAUSDT', 'SELL', 1000, 0.20, 10.0, 1, '2026-01-01 00:01:00'),
                ('bot1', 'short_open', 'ADAUSDT', 'SELL', 15000, 0.197, NULL, 0, '2026-01-01 00:03:00'),
                ('bot1', 'cover', 'ADAUSDT', 'BUY', 15000, 0.198, -15.0, 1, '2026-01-01 00:04:00')
                """
            )
            conn.commit()
            rebuild_order_realized_pnl(cur)
            conn.commit()
            rows = {
                r["id"]: r
                for r in cur.execute("SELECT id, realized_pnl FROM orders")
            }
            # Stream ≠ live book: only bot-exit PnL is trusted on this symbol.
            self.assertAlmostEqual(float(rows["s1"]["realized_pnl"]), 10.0)
            self.assertIsNone(rows["orphan"]["realized_pnl"])
            self.assertIsNone(rows["short_open"]["realized_pnl"])
            self.assertAlmostEqual(float(rows["cover"]["realized_pnl"]), -15.0)
            conn.close()
        finally:
            db_conn._pool = None
            db_conn.DB_PATH = old

    def test_drift_symbol_drops_non_bot_fifo(self):
        """Phantom leftover inventory must not keep FIFO PnL on a later short open."""
        import os
        import tempfile

        import app.db.connection as db_conn
        from app.database import get_connection, init_db

        tmp = tempfile.mkdtemp()
        old = db_conn.DB_PATH
        db_conn.DB_PATH = os.path.join(tmp, "drift_nobot_pnl.db")
        db_conn._pool = None
        try:
            init_db()
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO orders (
                    id, symbol, type, side, price, quantity, status,
                    filled_quantity, average_fill_price, timestamp, realized_pnl
                ) VALUES
                ('orphan', 'ADAUSDT', 'MARKET', 'BUY', 0.19, 30000, 'FILLED',
                 30000, 0.19, '2026-01-01 00:00:00', NULL),
                ('short_open', 'ADAUSDT', 'MARKET', 'SELL', 0.197, 15000, 'FILLED',
                 15000, 0.197, '2026-01-01 00:01:00', 99)
                """
            )
            cur.execute(
                "INSERT INTO positions (symbol, size, avg_price) VALUES ('ADAUSDT', 0, 0)"
            )
            conn.commit()
            rebuild_order_realized_pnl(cur)
            conn.commit()
            rows = {
                r["id"]: r
                for r in cur.execute("SELECT id, realized_pnl FROM orders")
            }
            self.assertIsNone(rows["orphan"]["realized_pnl"])
            self.assertIsNone(rows["short_open"]["realized_pnl"])
            conn.close()
        finally:
            db_conn._pool = None
            db_conn.DB_PATH = old


if __name__ == "__main__":
    unittest.main()
