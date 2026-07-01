"""Concurrent DB reads during a write — smoke test for WAL + pool."""

import os
import tempfile
import threading
import unittest

import app.config as app_config
import app.db.connection as db_conn

_TEST_DIR = tempfile.mkdtemp()
db_conn.DB_PATH = os.path.join(_TEST_DIR, "conc_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
db_conn._pool = None
app_config.DB_PATH = db_conn.DB_PATH

from app.database import init_db  # noqa: E402
from app.db.connection import db_session, warm_pool  # noqa: E402


class TestDbConcurrency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        warm_pool(max_attempts=2, base_delay_sec=0.01)
        init_db()

    def test_parallel_reads_during_write(self):
        errors: list[Exception] = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    with db_session(commit=False) as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT COUNT(*) FROM orders")
                        cur.fetchone()
                except Exception as exc:
                    errors.append(exc)
                    stop.set()

        def writer():
            for i in range(20):
                try:
                    with db_session() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            """
                            INSERT INTO orders
                                (id, symbol, type, side, price, quantity, status)
                            VALUES (?, 'BTCUSDT', 'MARKET', 'BUY', 100, 0.01, 'PENDING')
                            """,
                            (f"conc-{i}",),
                        )
                except Exception as exc:
                    errors.append(exc)
                    stop.set()

        readers = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
        for t in readers:
            t.start()
        writer()
        stop.set()
        for t in readers:
            t.join(timeout=2)

        self.assertEqual(errors, [], msg=f"concurrency errors: {errors}")


if __name__ == "__main__":
    unittest.main()
