"""Tests for optional follow-up features."""

import os
import tempfile
import unittest

_TEST_DIR = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = ""
os.environ["ARCHIVE_ENABLED"] = "true"

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "optional_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH

from app.database import init_db  # noqa: E402
from app.services.reconciliation import (  # noqa: E402
    auto_reconcile_with_portfolio,
    list_ambiguous_orders,
    record_ambiguous_order,
    resolve_ambiguous_order,
)
from app.services.archive.tick_writer import record_tick, flush_ticks, query_ticks  # noqa: E402


class _StubOms:
    def get_account_data(self):
        return {"positions": [{"symbol": "AAPL", "size": 10}]}


class OptionalFeaturesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_ambiguous_order_lifecycle(self):
        oid = record_ambiguous_order(
            {"symbol": "AAPL", "side": "BUY", "type": "MARKET", "quantity": 1},
            "timeout",
            broker="LIVE_ETORO",
        )
        pending = list_ambiguous_orders()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], oid)

        result = auto_reconcile_with_portfolio(_StubOms())
        self.assertGreaterEqual(result.get("matched", 0), 1)
        self.assertEqual(len(list_ambiguous_orders()), 0)

        oid2 = record_ambiguous_order(
            {"symbol": "TSLA", "side": "SELL", "type": "MARKET", "quantity": 2},
            "network",
        )
        self.assertTrue(resolve_ambiguous_order(oid2, "dismissed"))
        self.assertEqual(len(list_ambiguous_orders()), 0)

    def test_tick_writer_roundtrip(self):
        app_config.ARCHIVE_TICKS_ENABLED = True
        record_tick("BTCUSDT", 50000.0, volume=0.5, time_ms=1_700_000_000_000)
        written = flush_ticks()
        self.assertGreaterEqual(written, 1)
        ticks = query_ticks("BTCUSDT", 1_699_999_000_000, 1_700_001_000_000)
        self.assertEqual(len(ticks), 1)
        self.assertEqual(ticks[0]["price"], 50000.0)


if __name__ == "__main__":
    unittest.main()
