"""Retention purge for market_bars_1m."""

from __future__ import annotations

import os
import tempfile
import time
import unittest

_TEST_DIR = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = ""
os.environ["ARCHIVE_ENABLED"] = "true"
os.environ["ARCHIVE_RETENTION_1M_DAYS"] = "14"

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "purge_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH

from app.database import init_db  # noqa: E402
from app.services.archive.rollup import purge_expired_1m  # noqa: E402
from app.services.archive.writer import _upsert_1m_rows  # noqa: E402


class TestArchiveRetentionPurge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        conn = db_conn.get_connection()
        try:
            conn.cursor().execute("DELETE FROM market_bars_1m WHERE symbol = 'PURGETEST'")
            conn.commit()
        finally:
            conn.close()

    def test_purge_expired_1m_deletes_old_rows(self):
        now = int(time.time())
        old_ts = now - 40 * 86400
        fresh_ts = now - 3600
        _upsert_1m_rows([
            {
                "symbol": "PURGETEST",
                "time": old_ts,
                "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1,
                "source": "test",
            },
            {
                "symbol": "PURGETEST",
                "time": fresh_ts,
                "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1,
                "source": "test",
            },
        ])

        cutoff = now - 14 * 86400
        deleted = purge_expired_1m(cutoff, batch_limit=1000)
        self.assertGreaterEqual(deleted, 1)

        conn = db_conn.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT time FROM market_bars_1m WHERE symbol = 'PURGETEST' ORDER BY time",
            )
            rows = cur.fetchall()
            times = [int(r[0] if not isinstance(r, dict) else r["time"]) for r in rows]
            self.assertNotIn(old_ts, times)
            self.assertIn(fresh_ts, times)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
