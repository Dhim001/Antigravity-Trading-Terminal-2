"""Tests for persistent backtest job store."""

import unittest

from app.database import init_db
from app.services.bots.backtest_job_store import (
    create_backtest_job,
    get_backtest_job,
    is_job_cancelled,
    request_cancel_job,
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

    def test_cancel_job(self):
        job_id = create_backtest_job({"symbol": "ETHUSDT", "days": 3})
        self.assertTrue(request_cancel_job(job_id))
        self.assertTrue(is_job_cancelled(job_id))


if __name__ == "__main__":
    unittest.main()
