"""Tests for data quality monitor."""

import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

_TEST_DIR = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = ""
os.environ["ARCHIVE_ENABLED"] = "true"
os.environ["DATA_QUALITY_ENABLED"] = "true"

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "dq_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH

from app.database import init_db  # noqa: E402
from app.services.data_quality import registry  # noqa: E402
from app.services.data_quality.monitor import evaluate_symbols, evaluate_and_act  # noqa: E402


class DataQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        registry.note_tick("AAPL", time_ms=int(time.time() * 1000))
        from app.services.data_quality.monitor import _dq_paused_bot_ids
        _dq_paused_bot_ids.clear()

    def test_snapshot_fresh_symbol(self):
        snap = registry.snapshot()
        self.assertIn("AAPL", snap["symbols"])
        stale = snap["symbols"]["AAPL"]["stale_sec"]
        self.assertIsNotNone(stale)
        self.assertLess(stale, 5)

    def test_evaluate_stale_severe(self):
        old_ms = int((time.time() - 120) * 1000)
        registry.note_tick("TSLA", time_ms=old_ms)
        report = evaluate_symbols(["TSLA"])
        self.assertIn("TSLA", report["stale_severe"])

    @patch("app.services.data_quality.monitor._MONITOR_START", 0.0)
    @patch("app.services.data_quality.monitor._STARTUP_GRACE_SEC", 0.0)
    def test_never_seen_symbol_is_severe(self):
        report = evaluate_symbols(["UNKNOWN"])
        self.assertIn("UNKNOWN", report["stale_severe"])

    @patch("app.services.data_quality.monitor.DATA_QUALITY_ACTIVE_PAUSE", True)
    @patch("app.services.data_quality.monitor.DATA_QUALITY_STALE_PAUSE_SEC", 30)
    async def _run_pause_test(self):
        old_ms = int((time.time() - 90) * 1000)
        registry.note_tick("NVDA", time_ms=old_ms)
        bot_manager = MagicMock()
        bot_manager.active_bots = {
            "b1": {"status": "RUNNING", "symbol": "NVDA"},
        }
        bot_manager.pause_bot = AsyncMock()
        bot_manager.log_bot_event = AsyncMock()
        feed = MagicMock()
        feed.symbols = ["NVDA"]
        report = await evaluate_and_act(feed, bot_manager)
        self.assertGreaterEqual(report.get("bots_paused", 0), 1)
        bot_manager.pause_bot.assert_called_once_with("b1")
        from app.services.data_quality.monitor import _dq_paused_bot_ids
        self.assertIn("b1", _dq_paused_bot_ids)

    def test_active_pause_on_stale(self):
        import asyncio
        asyncio.run(self._run_pause_test())

    @patch("app.services.data_quality.monitor.DATA_QUALITY_ACTIVE_PAUSE", True)
    @patch("app.services.data_quality.monitor.DATA_QUALITY_STALE_PAUSE_SEC", 30)
    async def _run_resume_test(self):
        from app.services.data_quality.monitor import _dq_paused_bot_ids

        _dq_paused_bot_ids.add("b1")
        registry.note_tick("DOTUSDT", time_ms=int(time.time() * 1000))
        bot_manager = MagicMock()
        bot_manager.active_bots = {
            "b1": {"status": "PAUSED", "symbol": "DOTUSDT"},
        }
        bot_manager.resume_bot = AsyncMock()
        bot_manager.log_bot_event = AsyncMock()
        feed = MagicMock()
        feed.symbols = ["DOTUSDT"]
        report = await evaluate_and_act(feed, bot_manager)
        self.assertGreaterEqual(report.get("bots_resumed", 0), 1)
        bot_manager.resume_bot.assert_called_once_with("b1")
        self.assertNotIn("b1", _dq_paused_bot_ids)

    def test_auto_resume_on_feed_recovery(self):
        import asyncio
        asyncio.run(self._run_resume_test())


if __name__ == "__main__":
    unittest.main()
