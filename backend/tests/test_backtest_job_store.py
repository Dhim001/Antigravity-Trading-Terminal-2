"""Tests for persistent backtest job store."""

import time
import unittest

from app.database import init_db
from app.services.bots.backtest_job_store import (
    create_backtest_job,
    get_backtest_job,
    is_job_cancelled,
    request_cancel_job,
    set_job_status,
    update_job_progress,
)


class TestBacktestJobStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_create_and_progress(self):
        job_id = create_backtest_job({"symbol": "BTCUSDT", "strategy": "MACD_RSI", "days": 7})
        self.assertTrue(job_id)
        update_job_progress(job_id, {"pct": 50, "phase": "simulate", "message": "Half"})
        job = get_backtest_job(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["progress"]["pct"], 50)
        self.assertTrue(job["progress"].get("updated_at"))

    def test_progress_heartbeat_refreshes_updated_at(self):
        """Same pct/bar must still advance updated_at for FE stall fingerprint."""
        job_id = create_backtest_job({"symbol": "ETHUSDT", "strategy": "TCN_MULTI_HORIZON", "days": 14})
        update_job_progress(job_id, {"pct": 12, "bar": 0, "phase": "features", "message": "Building…"})
        first = get_backtest_job(job_id)["progress"]["updated_at"]
        time.sleep(0.02)
        update_job_progress(job_id, {"pct": 12, "bar": 0, "phase": "features", "message": "Building…"})
        second = get_backtest_job(job_id)["progress"]["updated_at"]
        self.assertNotEqual(first, second)

    def test_get_job_can_omit_results_blob(self):
        job_id = create_backtest_job({"symbol": "BTCUSDT", "days": 7})
        fat = {"equity_curve": list(range(5000)), "trades": [{"i": i} for i in range(200)]}
        set_job_status(job_id, "completed", results=fat)
        slim = get_backtest_job(job_id, include_results=False)
        full = get_backtest_job(job_id, include_results=True)
        self.assertIsNone(slim.get("results"))
        self.assertIsNotNone(full.get("results"))
        self.assertEqual(len(full["results"]["equity_curve"]), 5000)

    def test_cancel_job(self):
        job_id = create_backtest_job({"symbol": "ETHUSDT", "days": 3})
        self.assertTrue(request_cancel_job(job_id))
        self.assertTrue(is_job_cancelled(job_id))


if __name__ == "__main__":
    unittest.main()
