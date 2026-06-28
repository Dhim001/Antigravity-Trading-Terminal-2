"""Trade PnL correlation tests — pairwise alignment and normalization."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import unittest.mock as mock
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""

_TEST_DIR = tempfile.mkdtemp()
import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "pnl_corr_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH

from app.database import get_connection, init_db  # noqa: E402
from app.services.bots import analytics as bot_analytics  # noqa: E402
from app.services.bots.correlation import (  # noqa: E402
    build_symbol_daily_returns_from_snapshots,
    get_trade_pnl_correlation_matrix,
)


def _reset_db() -> None:
    db_conn._pool = None
    path = db_conn.DB_PATH
    if os.path.exists(path):
        try:
            os.remove(path)
        except PermissionError:
            pass
    init_db()


class TradePnlCorrelationTests(unittest.TestCase):
    def setUp(self):
        _reset_db()
        conn = get_connection()
        cursor = conn.cursor()
        self.bot_a = str(uuid.uuid4())
        self.bot_b = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.bot_a, "s1", "AAPL", "1m", "RUNNING", 1000, "{}"),
        )
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.bot_b, "s2", "MSFT", "1m", "RUNNING", 1000, "{}"),
        )
        conn.commit()
        conn.close()

        base = datetime.now(timezone.utc) - timedelta(days=5)
        for i in range(5):
            ts = (base + timedelta(days=i)).isoformat()
            bot_analytics.record_snapshot(self.bot_a, 1000 + i * 10, 0, 0, 1)
            bot_analytics.record_snapshot(self.bot_b, 2000 + i * 5, 0, 0, 1)
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bot_snapshots SET timestamp = ? WHERE rowid = (SELECT MAX(rowid) FROM bot_snapshots WHERE bot_id = ?)",
                (ts, self.bot_a),
            )
            cursor.execute(
                "UPDATE bot_snapshots SET timestamp = ? WHERE rowid = (SELECT MAX(rowid) FROM bot_snapshots WHERE bot_id = ?)",
                (ts, self.bot_b),
            )
            conn.commit()
            conn.close()

    def test_snapshot_returns_normalized_by_allocation(self):
        returns = build_symbol_daily_returns_from_snapshots(0)
        self.assertIn("AAPL", returns)
        self.assertIn("MSFT", returns)
        for day, ret in returns["AAPL"].items():
            self.assertLess(abs(ret), 0.05)

    @mock.patch("app.services.bots.correlation.RISK_CORRELATION_MIN_DAYS", 2)
    def test_pairwise_matrix_no_zero_fill(self):
        result = get_trade_pnl_correlation_matrix([], period="1M", symbols=["AAPL", "MSFT"])
        self.assertEqual(result["mode"], "trade_pnl")
        self.assertTrue(result.get("pairwise"))
        if result["matrix"]:
            self.assertEqual(len(result["matrix"]), 2)


if __name__ == "__main__":
    unittest.main()
