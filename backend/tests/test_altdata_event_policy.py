"""Tests for alt-data calendar, event policy, and price adjustments."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.config as app_config
import app.db.connection as db_conn

_TEST_DIR = tempfile.mkdtemp()
db_conn.DB_PATH = os.path.join(_TEST_DIR, "altdata_policy_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
db_conn._pool = None
app_config.DB_PATH = db_conn.DB_PATH

from app.database import init_db  # noqa: E402
from app.db.connection import warm_pool  # noqa: E402
from app.services.altdata.store import (  # noqa: E402
    upsert_corporate_events,
    upsert_economic_events,
)


class TestAltdataEventPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_conn._pool = None
        warm_pool(max_attempts=2, base_delay_sec=0.01)
        init_db()

    def setUp(self):
        conn = db_conn.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM corporate_events")
        cur.execute("DELETE FROM economic_events")
        conn.commit()
        conn.close()

    def test_calendar_gate_weekend(self):
        from app.services.altdata.calendar import calendar_gate

        # Saturday 2026-07-04 15:00 UTC ≈ Saturday morning ET
        ts = datetime(2026, 7, 4, 15, 0, tzinfo=timezone.utc).timestamp()
        blocked, reason = calendar_gate("AAPL", ts)
        self.assertTrue(blocked)
        self.assertIn("Weekend", reason or "")

    def test_calendar_gate_crypto_exempt(self):
        from app.services.altdata.calendar import is_equity_rth_open

        ts = datetime(2026, 7, 4, 15, 0, tzinfo=timezone.utc).timestamp()
        open_ok, _ = is_equity_rth_open("BTCUSDT", ts)
        self.assertTrue(open_ok)

    def test_holiday_blocks_entry(self):
        from app.services.altdata.event_policy import check_entry_gates

        upsert_economic_events([{
            "event_id": "holiday:2026-07-03:July 3",
            "event_type": "market_holiday",
            "title": "July 3 close",
            "scheduled_at": "2026-07-03",
            "impact": "closed",
            "country": "US",
            "source": "TEST",
        }])
        ts = datetime(2026, 7, 3, 14, 30, tzinfo=timezone.utc).timestamp()
        allowed, reason, gate = check_entry_gates(
            "AAPL", ts, {"event_policy": {"calendar_gate": True}},
        )
        self.assertFalse(allowed)
        self.assertEqual(gate, "calendar")

    def test_split_blackout(self):
        from app.services.altdata.event_policy import check_entry_gates

        upsert_corporate_events([{
            "id": "split:AAPL:2026-06-01:2:1",
            "symbol": "AAPL",
            "event_type": "split",
            "event_date": "2026-06-01",
            "title": "Split 2:1",
            "metadata": {"split_from": 1, "split_to": 2},
            "source": "TEST",
        }])
        ts = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc).timestamp()
        cfg = {"event_policy": {"corp_split_blackout_days": 1, "calendar_gate": False}}
        allowed, reason, gate = check_entry_gates("AAPL", ts, cfg)
        self.assertFalse(allowed)
        self.assertEqual(gate, "corporate")
        self.assertIn("Split", reason or "")

    def test_split_price_adjustment(self):
        from app.services.altdata.adjustments import apply_price_adjustments

        upsert_corporate_events([{
            "id": "split:TEST:2026-01-15:2:1",
            "symbol": "TEST",
            "event_type": "split",
            "event_date": "2026-01-15",
            "title": "2:1",
            "metadata": {"split_from": 1, "split_to": 2},
            "source": "TEST",
        }])
        pre_ts = int(datetime(2026, 1, 10, tzinfo=timezone.utc).timestamp())
        post_ts = int(datetime(2026, 1, 20, tzinfo=timezone.utc).timestamp())
        bars = [
            {"time": pre_ts, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
            {"time": post_ts, "open": 50, "high": 51, "low": 49, "close": 50, "volume": 2000},
        ]
        adjusted = apply_price_adjustments(bars, "TEST", mode="split_only")
        self.assertAlmostEqual(adjusted[0]["close"], 50.0, places=2)
        self.assertAlmostEqual(adjusted[1]["close"], 50.0, places=2)

    def test_split_blackout_adjacent_calendar_day(self):
        from app.services.altdata.event_policy import check_entry_gates

        upsert_corporate_events([{
            "id": "split:AAPL:2026-06-01:2:1",
            "symbol": "AAPL",
            "event_type": "split",
            "event_date": "2026-06-01",
            "title": "Split 2:1",
            "metadata": {"split_from": 1, "split_to": 2},
            "source": "TEST",
        }])
        # Next calendar day — should still be inside ±1 day blackout
        ts = datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc).timestamp()
        cfg = {"event_policy": {"corp_split_blackout_days": 1, "calendar_gate": False}}
        allowed, reason, gate = check_entry_gates("AAPL", ts, cfg)
        self.assertFalse(allowed)
        self.assertEqual(gate, "corporate")

    def test_rth_close_at_1600_et(self):
        from app.services.altdata.calendar import is_equity_rth_open

        # 2026-07-01 20:00 UTC = 16:00 ET (EDT)
        ts = datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc).timestamp()
        open_ok, reason = is_equity_rth_open("AAPL", ts)
        self.assertFalse(open_ok)
        self.assertIn("close", (reason or "").lower())

    def test_backtest_risk_gate_crypto_weekend(self):
        """Backtest risk gate must not block crypto when run on a weekend."""
        from datetime import datetime, timezone
        from app.services.bots.risk_gate import RiskGate

        ts = datetime(2026, 7, 4, 15, 0, tzinfo=timezone.utc).timestamp()
        gate = RiskGate()
        bot = {
            "status": "RUNNING",
            "symbol": "BTCUSDT",
            "allocation": 10_000,
            "config": {},
        }
        with mock.patch.object(gate, "_kill_switch_block", return_value=None):
            decision = gate.validate_trade(
                bot, "BUY", 0.01, 68000.0,
                is_exit=False, daily_pnl=0, position_size=0,
                at_ts=ts,
            )
        self.assertTrue(decision.allowed, decision.reason)

    def test_upcoming_events(self):
        from app.services.altdata.event_policy import get_upcoming_corporate_events

        future = (datetime.now(timezone.utc).date().isoformat())
        upsert_corporate_events([{
            "id": f"div:MSFT:{future}:1",
            "symbol": "MSFT",
            "event_type": "dividend",
            "event_date": future,
            "title": "Dividend $1",
            "metadata": {},
            "source": "TEST",
        }])
        events = get_upcoming_corporate_events("MSFT", days=7)
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
