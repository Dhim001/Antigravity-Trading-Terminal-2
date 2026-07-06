"""Tests for P0 alt-data: macro gates and crypto derivatives scoring."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.config as app_config
import app.db.connection as db_conn

_TEST_DIR = tempfile.mkdtemp()
db_conn.DB_PATH = os.path.join(_TEST_DIR, "altdata_p0_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
db_conn._pool = None
app_config.DB_PATH = db_conn.DB_PATH

from app.database import init_db  # noqa: E402
from app.db.connection import warm_pool  # noqa: E402
from app.services.altdata.crypto_derivatives import (  # noqa: E402
    classify_quadrant,
    score_derivatives_positioning,
)
from app.services.altdata.store import (  # noqa: E402
    insert_crypto_derivatives_snapshot,
    upsert_economic_events,
)


class TestAltdataP0(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_conn._pool = None
        warm_pool(max_attempts=2, base_delay_sec=0.01)
        init_db()

    def setUp(self):
        conn = db_conn.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM economic_events")
        cur.execute("DELETE FROM crypto_derivatives_history")
        conn.commit()
        conn.close()

    def test_quadrant_bullish_leverage(self):
        self.assertEqual(
            classify_quadrant(0.0006, 8.0),
            "bullish_leverage_build",
        )

    def test_crowded_long_negative_score(self):
        score, reasons, quad = score_derivatives_positioning(
            funding_rate=0.0008,
            oi_change_24h_pct=10.0,
        )
        self.assertEqual(quad, "bullish_leverage_build")
        self.assertLessEqual(score, 0)
        self.assertTrue(any("Funding" in r or "OI" in r for r in reasons))

    def test_crowded_short_positive_score(self):
        score, _, _ = score_derivatives_positioning(
            funding_rate=-0.0005,
            oi_change_24h_pct=-8.0,
        )
        self.assertGreaterEqual(score, 0)

    def test_macro_gate_blocks_near_release(self):
        from app.services.altdata.event_policy import check_entry_gates

        release_ts = datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc).timestamp()
        upsert_economic_events([{
            "event_id": "macro:test:cpi",
            "event_type": "macro_release",
            "title": "CPI YoY",
            "scheduled_at": datetime.fromtimestamp(
                release_ts, tz=timezone.utc,
            ).isoformat(),
            "impact": "high",
            "country": "US",
            "source": "TEST",
        }])
        # 10 minutes before release
        ts = release_ts - 600
        allowed, reason, gate = check_entry_gates("AAPL", ts, {}, is_exit=False)
        self.assertFalse(allowed)
        self.assertEqual(gate, "macro")
        self.assertIn("Macro", reason or "")

    def test_macro_gate_allows_crypto_calendar_exempt(self):
        from app.services.altdata.event_policy import check_entry_gates

        # Weekend — crypto calendar exempt but macro still applies if in window
        release_ts = datetime(2026, 7, 11, 14, 0, tzinfo=timezone.utc).timestamp()
        upsert_economic_events([{
            "event_id": "macro:test:fomc",
            "event_type": "macro_release",
            "title": "FOMC Rate Decision",
            "scheduled_at": datetime.fromtimestamp(
                release_ts, tz=timezone.utc,
            ).isoformat(),
            "impact": "high",
            "country": "US",
            "source": "TEST",
        }])
        ts = release_ts - 300
        allowed, _, gate = check_entry_gates("BTCUSDT", ts, {}, is_exit=False)
        self.assertFalse(allowed)
        self.assertEqual(gate, "macro")

    def test_derivatives_score_from_snapshot(self):
        from app.services.altdata.crypto_derivatives import get_derivatives_score_at

        now = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc).timestamp()
        insert_crypto_derivatives_snapshot({
            "symbol": "BTCUSDT",
            "recorded_at": now,
            "funding_rate": 0.0009,
            "open_interest": 1e9,
            "oi_change_24h_pct": 12.0,
            "quadrant": "bullish_leverage_build",
            "score": -1,
            "source": "TEST",
            "metadata": {},
        })
        score, reasons, meta = get_derivatives_score_at("BTCUSDT", now + 60)
        self.assertLessEqual(score, 0)
        self.assertTrue(reasons)
        self.assertEqual(meta.get("quadrant"), "bullish_leverage_build")


if __name__ == "__main__":
    unittest.main()
