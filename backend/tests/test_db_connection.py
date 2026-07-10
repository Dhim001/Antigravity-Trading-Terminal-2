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

    def test_sqlite_lock_retry_backoff_between_attempts(self):
        """Wrapper retries must sleep between lock errors, not spin."""
        import sqlite3
        from unittest.mock import MagicMock, patch

        from app.db.connection import _CursorWrapper

        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = [
            sqlite3.OperationalError("database is locked"),
            sqlite3.OperationalError("database is locked"),
            mock_cursor,
        ]
        wrapper = _CursorWrapper(mock_cursor)
        with patch("app.db.connection.time.sleep") as sleep_mock:
            wrapper.execute("SELECT 1")
        self.assertEqual(mock_cursor.execute.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)
        sleep_mock.assert_any_call(0.025)
        sleep_mock.assert_any_call(0.05)


if __name__ == "__main__":
    unittest.main()
