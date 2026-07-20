"""Tests for archive WAL durability."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEST_DIR = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = ""
os.environ["ARCHIVE_ENABLED"] = "true"

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "wal_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH
app_config.ARCHIVE_WAL_DIR = os.path.join(_TEST_DIR, "wal")

from app.database import init_db  # noqa: E402
from app.services.archive import wal as wal_mod  # noqa: E402
from app.services.archive.writer import ArchiveWriter, _upsert_1m_rows, get_archive_writer  # noqa: E402


class TestArchiveWal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        conn = db_conn.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM market_bars_1m")
        conn.commit()
        conn.close()
        import app.services.archive.writer as wmod
        wmod._writer = ArchiveWriter()
        wal_mod.WAL_DIR = Path(app_config.ARCHIVE_WAL_DIR)
        wal_mod.WAL_FILE = wal_mod.WAL_DIR / "pending.jsonl"
        if wal_mod.WAL_FILE.exists():
            wal_mod.WAL_FILE.unlink()

    def test_flush_failure_writes_wal_and_replay(self):
        writer = get_archive_writer()
        bar = {"time": 1718006400, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100}
        writer.record_bar("BTCUSDT", bar, "SIMULATED")

        with patch("app.services.archive.writer._upsert_1m_rows", side_effect=RuntimeError("db down")):
            flushed = writer.flush()
        self.assertEqual(flushed, 0)
        self.assertEqual(writer.pending_count, 0)  # cleared after WAL (no RAM leak)
        self.assertTrue(wal_mod.WAL_FILE.exists())

        replayed = wal_mod.replay_wal(_upsert_1m_rows)
        self.assertEqual(replayed, 1)
        self.assertFalse(wal_mod.WAL_FILE.exists())

    def test_flush_success_does_not_write_wal(self):
        writer = get_archive_writer()
        bar = {"time": 1718006500, "open": 2, "high": 3, "low": 1.5, "close": 2.5, "volume": 50}
        writer.record_bar("ETHUSDT", bar, "SIMULATED")
        flushed = writer.flush()
        self.assertEqual(flushed, 1)
        self.assertEqual(writer.pending_count, 0)
        self.assertFalse(wal_mod.WAL_FILE.exists())

    def test_buffer_hard_cap_drops_oldest(self):
        writer = get_archive_writer()
        with patch("app.services.archive.writer.ARCHIVE_BUFFER_MAX_ROWS", 3):
            for i in range(5):
                bar = {
                    "time": 1718006400 + i * 60,
                    "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10,
                }
                writer.record_bar("BTCUSDT", bar, "SIMULATED")
            self.assertLessEqual(writer.pending_count, 3)
            self.assertGreaterEqual(writer.total_dropped, 2)


if __name__ == "__main__":
    unittest.main()
