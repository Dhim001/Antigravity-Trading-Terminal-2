"""Database connection pool, health checks, and db_session."""

import os
import tempfile
import unittest

import app.config as app_config
import app.db.connection as db_conn

_TEST_DIR = tempfile.mkdtemp()
db_conn.DB_PATH = os.path.join(_TEST_DIR, "conn_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
db_conn._pool = None
app_config.DB_PATH = db_conn.DB_PATH

from app.database import init_db  # noqa: E402
from app.db.connection import (  # noqa: E402
    check_db_health,
    db_session,
    pool_stats,
    warm_pool,
)
from app.db.migrations import BASELINE_REVISION, get_applied_revisions  # noqa: E402


class TestDbConnection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_conn._pool = None
        warm_pool(max_attempts=2, base_delay_sec=0.01)
        init_db()

    def test_warm_pool_and_health(self):
        health = check_db_health()
        self.assertTrue(health["ok"])
        self.assertEqual(health["driver"], "sqlite")
        self.assertEqual(health["journal_mode"], "wal")
        self.assertIn("pool", health)
        self.assertTrue(health["pool"]["initialized"])

    def test_db_session_commits(self):
        with db_session() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM accounts")
            row = cur.fetchone()
        self.assertIsNotNone(row)

    def test_pool_stats(self):
        stats = pool_stats()
        self.assertTrue(stats["initialized"])
        self.assertEqual(stats["driver"], "sqlite")

    def test_baseline_migration_recorded(self):
        applied = get_applied_revisions()
        self.assertIn(BASELINE_REVISION, applied)


if __name__ == "__main__":
    unittest.main()
