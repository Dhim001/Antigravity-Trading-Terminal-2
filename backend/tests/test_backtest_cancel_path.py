"""End-to-end guarantees for the backtest Cancel button.

Covers the three links in the chain the UI depends on:
  1. the simulation loop polls ``cancel_cb`` and aborts mid-run,
  2. ``request_cancel_job`` (job_id path) flips the persisted flag the run reads,
  3. a client disconnect abandons inline runs but leaves deferred jobs running.
"""

from __future__ import annotations

import unittest

from app.services.bots.backtest_job_store import (
    create_backtest_job,
    is_job_cancelled,
    request_cancel_job,
    set_job_status,
)
from app.services.bots.backtest_jobs import (
    abandon_client_jobs,
    cancel_job,
    cancel_job_by_id,
    clear_job,
    start_job,
)
from app.services.bots.backtester import BacktesterService
from app.services.bots.screener import MarketScreenerService
from tests.test_backtest_align import _make_candles


class BacktestLoopCancelTests(unittest.TestCase):
    def setUp(self):
        self.backtester = BacktesterService(MarketScreenerService())
        self.candles = _make_candles(400)

    def test_loop_aborts_when_cancel_cb_flips(self):
        calls = {"n": 0}

        def cancel_cb() -> bool:
            calls["n"] += 1
            return calls["n"] > 5

        result = self.backtester.run_backtest(
            "TEST",
            "MACD_RSI",
            {"allocation": 1000},
            self.candles,
            cancel_cb=cancel_cb,
        )
        self.assertTrue(result.get("cancelled"))
        self.assertEqual(result.get("error"), "Backtest cancelled")
        # Aborted early rather than replaying every bar.
        self.assertLess(calls["n"], len(self.candles))

    def test_run_completes_when_never_cancelled(self):
        result = self.backtester.run_backtest(
            "TEST",
            "MACD_RSI",
            {"allocation": 1000},
            self.candles,
            cancel_cb=lambda: False,
        )
        self.assertFalse(result.get("cancelled"))
        self.assertNotIn("error", result)

    def test_cancel_during_ml_batch_precompute(self):
        """Cancel mid-precompute must abort before the bar loop finishes."""
        from app.services.bots.strategies_ml import MlSignalBoostStrategy, get_ml_signal_store

        class _Gbm:
            def predict_proba(self, X):
                import numpy as np

                X = np.asarray(X)
                out = np.zeros((X.shape[0], 3), dtype=float)
                out[:, 1] = 1.0
                return out

        store = get_ml_signal_store()
        symbol = "CANCELML"
        tf = "1m"
        key = store._cache_key(symbol, None, tf)
        store._models[key] = _Gbm()
        store._metadata[key] = {
            "reverse_map": {"0": "BUY", "1": "NONE", "2": "SELL"},
            "feature_schema_version": 4,
        }
        store._mtime[key] = -1.0

        calls = {"n": 0}

        def cancel_cb() -> bool:
            calls["n"] += 1
            # First precompute poll is at bar 0 (i % 512 == 0) — abort there.
            return calls["n"] >= 1

        # Force the batch path; cancel should surface as InterruptedError → cancelled.
        result = self.backtester.run_backtest(
            symbol,
            "ML_SIGNAL_BOOST",
            {
                "allocation": 1000,
                "symbol": symbol,
                "model_symbol": symbol,
                "timeframe": tf,
                "batch_inference": True,
                "calibration_gate_enabled": False,
            },
            self.candles,
            cancel_cb=cancel_cb,
        )
        self.assertTrue(result.get("cancelled"))
        self.assertEqual(result.get("error"), "Backtest cancelled")
        # Precompute polls cancel before the bar loop; one check is enough.
        self.assertGreaterEqual(calls["n"], 1)
        self.assertTrue(callable(getattr(MlSignalBoostStrategy({}), "precompute_backtest_signals")))


class CancelSignalTests(unittest.TestCase):
    def test_request_cancel_job_is_visible_to_the_running_backtest(self):
        job_id = create_backtest_job({"symbol": "BTCUSDT"}, status="running")
        self.assertFalse(is_job_cancelled(job_id))
        self.assertTrue(request_cancel_job(job_id))
        # This is exactly the predicate _execute_backtest polls each bar.
        self.assertTrue(is_job_cancelled(job_id))

    def test_cancel_is_rejected_once_the_job_finished(self):
        job_id = create_backtest_job({"symbol": "BTCUSDT"}, status="running")
        set_job_status(job_id, "completed")
        self.assertFalse(request_cancel_job(job_id))

    def test_cancel_job_marks_token_for_websocket(self):
        ws = object()
        job = start_job(ws, "job-1")
        self.assertTrue(cancel_job(ws))
        self.assertTrue(job.is_cancelled())
        clear_job(ws)

    def test_cancel_by_id_reaches_run_started_on_another_connection(self):
        ws = object()
        job = start_job(ws, "job-reconnect", deferred=True)
        # Cancel arrives on a *new* websocket after a reconnect.
        self.assertTrue(cancel_job_by_id("job-reconnect"))
        self.assertTrue(job.is_cancelled())
        clear_job(ws)

    def test_cancel_by_id_ignores_unknown_job(self):
        self.assertFalse(cancel_job_by_id("job-does-not-exist"))
        self.assertFalse(cancel_job_by_id(None))


class DisconnectAbandonTests(unittest.TestCase):
    def test_disconnect_cancels_inline_run(self):
        ws = object()
        job = start_job(ws, "job-inline", deferred=False)
        self.assertTrue(abandon_client_jobs(ws))
        self.assertTrue(job.is_cancelled())

    def test_disconnect_keeps_deferred_job_running(self):
        ws = object()
        job = start_job(ws, "job-deferred", deferred=True)
        self.assertFalse(abandon_client_jobs(ws))
        self.assertFalse(job.is_cancelled())

    def test_disconnect_does_not_flag_deferred_job_in_store(self):
        ws = object()
        job_id = create_backtest_job({"symbol": "BTCUSDT"}, status="running")
        start_job(ws, job_id, deferred=True)
        abandon_client_jobs(ws)
        self.assertFalse(is_job_cancelled(job_id))


if __name__ == "__main__":
    unittest.main()
