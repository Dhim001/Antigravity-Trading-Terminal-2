"""Trade journal persistence tests."""

import os
import tempfile
import unittest

import app.db.connection as db_conn
from app.database import init_db
from app.services.journal import store as journal_store
from app.services.journal.storage import LocalScreenshotStorage


class TradeJournalTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        db_conn.DB_PATH = os.path.join(self._tmpdir, "journal.db")
        db_conn._pool = None
        init_db()
        self.storage_dir = os.path.join(self._tmpdir, "shots")
        self.storage = LocalScreenshotStorage(base_dir=self.storage_dir)

    def test_upsert_list_delete(self):
        entry = journal_store.upsert_entry({
            "symbol": "BTCUSDT",
            "tags": ["breakout", "lesson"],
            "note": "Entered on MACD cross",
            "lesson": "Wait for volume confirmation",
        })
        self.assertTrue(entry["id"])
        self.assertEqual(entry["symbol"], "BTCUSDT")
        self.assertIn("breakout", entry["tags"])

        listed = journal_store.list_entries(symbol="BTCUSDT")
        self.assertEqual(len(listed), 1)

        found = journal_store.list_entries(query="volume")
        self.assertEqual(len(found), 1)

        deleted = journal_store.delete_entry(entry["id"])
        self.assertTrue(deleted)
        self.assertEqual(journal_store.list_entries(), [])

    def test_screenshot_data_url_round_trip(self):
        # Small PNG header stub — valid base64 image payload
        tiny = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        stored = self.storage.save(tiny)
        self.assertTrue(stored.startswith("data:image"))

        entry = journal_store.upsert_entry({
            "symbol": "ETHUSDT",
            "note": "with screenshot",
            "screenshot_url": tiny,
        })
        self.assertTrue(entry["screenshot_url"].startswith("data:image"))


if __name__ == "__main__":
    unittest.main()
