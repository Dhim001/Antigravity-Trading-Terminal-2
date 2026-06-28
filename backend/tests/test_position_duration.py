"""Unit tests for per-bot max position duration."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
import unittest.mock
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""

_TEST_DIR = tempfile.mkdtemp()
_DB_PATH = os.path.join(_TEST_DIR, "position_duration_test.db")
import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = _DB_PATH
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH


def _reset_db() -> None:
    db_conn._pool = None
    path = db_conn.DB_PATH
    if os.path.exists(path):
        try:
            os.remove(path)
        except PermissionError:
            conn = get_connection()
            cursor = conn.cursor()
            for table in (
                "bot_pending_fills",
                "bot_positions",
                "bot_trades",
                "bot_snapshots",
                "bot_logs",
                "orders",
                "positions",
                "bots",
            ):
                cursor.execute(f"DELETE FROM {table}")
            conn.commit()
            conn.close()
            return
    init_db()


from app.database import get_connection, init_db  # noqa: E402
from app.services.bots import analytics as bot_analytics  # noqa: E402
from app.services.bots import positions as bot_positions  # noqa: E402
from app.services.bots.position_duration import (  # noqa: E402
    duration_close_bar_time,
    is_position_stale,
    resolve_max_position_hours,
)


class PositionDurationLogicTests(unittest.TestCase):
    def test_global_default_when_no_bot_override(self):
        with unittest.mock.patch("app.services.bots.position_duration.RISK_MAX_POSITION_HOURS", 48.0):
            self.assertEqual(resolve_max_position_hours({}), 48.0)

    def test_bot_override_wins(self):
        self.assertEqual(resolve_max_position_hours({"max_position_hours": 12}), 12.0)

    def test_zero_bot_override_disables(self):
        self.assertIsNone(resolve_max_position_hours({"max_position_hours": 0}))

    def test_stale_when_hold_exceeds_limit(self):
        opened = time.time() - (25 * 3600)
        stale, reason, limit = is_position_stale(opened, {"max_position_hours": 24})
        self.assertTrue(stale)
        self.assertIn("24.0h", reason)
        self.assertEqual(limit, 24.0)

    def test_not_stale_within_limit(self):
        opened = time.time() - (2 * 3600)
        stale, _, _ = is_position_stale(opened, {"max_position_hours": 24})
        self.assertFalse(stale)

    def test_duration_bar_time_stable(self):
        opened = 1_700_000_000.0
        self.assertEqual(duration_close_bar_time(opened, 24), int(opened + 24 * 3600))


class PositionOpenedAtTests(unittest.TestCase):
    def setUp(self):
        _reset_db()
        conn = get_connection()
        cursor = conn.cursor()
        self.bot_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.bot_id, "ema_cross", "AAPL", "1m", "RUNNING", 10000, '{"max_position_hours": 1}'),
        )
        conn.commit()
        conn.close()

    def test_apply_fill_sets_opened_at_on_entry(self):
        bot_positions.apply_fill(self.bot_id, "AAPL", "BUY", 1.0, 150.0)
        pos = bot_positions.get_bot_position(self.bot_id, "AAPL")
        self.assertIsNotNone(pos["opened_at"])
        self.assertAlmostEqual(pos["opened_at"], time.time(), delta=5)

    def test_scale_in_preserves_opened_at(self):
        bot_positions.apply_fill(self.bot_id, "AAPL", "BUY", 1.0, 150.0)
        first = bot_positions.get_bot_position(self.bot_id, "AAPL")["opened_at"]
        time.sleep(0.01)
        bot_positions.apply_fill(self.bot_id, "AAPL", "BUY", 1.0, 151.0)
        second = bot_positions.get_bot_position(self.bot_id, "AAPL")["opened_at"]
        self.assertEqual(first, second)

    def test_infer_opened_at_from_entry_trade(self):
        bot_positions.apply_fill(self.bot_id, "AAPL", "BUY", 1.0, 150.0)
        original = bot_positions.get_bot_position(self.bot_id, "AAPL")["opened_at"]
        bot_analytics.record_trade(
            self.bot_id, "ord-1", "AAPL", "BUY", 1.0, 150.0, is_exit=False
        )
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bot_positions SET opened_at = NULL WHERE bot_id = ? AND symbol = ?",
            (self.bot_id, "AAPL"),
        )
        conn.commit()
        conn.close()

        inferred = bot_positions.ensure_opened_at(self.bot_id, "AAPL")
        self.assertIsNotNone(inferred)
        refreshed = bot_positions.get_bot_position(self.bot_id, "AAPL")
        self.assertEqual(refreshed["opened_at"], inferred)
        if original is not None:
            self.assertAlmostEqual(inferred, original, delta=3600)


if __name__ == "__main__":
    unittest.main()
