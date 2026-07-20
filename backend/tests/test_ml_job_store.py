"""Tests for in-memory ML job store + progress helpers (ML Lab Phase 1)."""

from __future__ import annotations

import os
import unittest
from concurrent.futures import Future
from unittest.mock import patch

from app.services.bots.ml_job_progress import (
    cleanup_ml_progress,
    make_progress_path,
    ml_cancel_requested,
    read_ml_progress,
    request_ml_cancel_file,
    write_ml_progress,
)
from app.services.bots.ml_job_store import (
    attach_ml_job_future,
    create_ml_job,
    finish_ml_job,
    get_ml_job,
    list_ml_jobs,
    mark_ml_job_running,
    ml_job_counts,
    public_ml_job,
    request_ml_job_cancel,
    reset_ml_job_store_for_tests,
    update_ml_job_progress,
)


class MlJobStoreTests(unittest.TestCase):
    def setUp(self):
        reset_ml_job_store_for_tests()

    def tearDown(self):
        reset_ml_job_store_for_tests()

    def test_create_progress_and_finish(self):
        job_id = create_ml_job(kind="train", strategy="ML_SIGNAL_BOOST", symbol="BTCUSDT")
        self.assertTrue(job_id)
        job = get_ml_job(job_id)
        self.assertEqual(job["status"], "queued")
        mark_ml_job_running(job_id)
        update_ml_job_progress(job_id, {"pct": 40, "phase": "fit", "detail": "epoch 2"})
        job = get_ml_job(job_id)
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["progress"]["pct"], 40)
        finish_ml_job(job_id, "done", result={"ok": True})
        job = get_ml_job(job_id)
        self.assertEqual(job["status"], "done")
        pub = public_ml_job(job)
        self.assertEqual(pub["result"]["ok"], True)
        self.assertNotIn("progress_path", pub)

    def test_cancel_queued_via_future(self):
        job_id = create_ml_job(kind="validate", strategy="LSTM_DIRECTION", symbol="ETHUSDT")
        fut = Future()
        attach_ml_job_future(job_id, fut)
        outcome = request_ml_job_cancel(job_id)
        self.assertTrue(outcome["ok"])
        self.assertTrue(outcome.get("cancelled") or outcome.get("immediate"))
        self.assertEqual(get_ml_job(job_id)["status"], "cancelled")
        self.assertTrue(fut.cancelled())

    def test_cancel_running_cooperative(self):
        path = make_progress_path("coop")
        try:
            job_id = create_ml_job(
                kind="train",
                strategy="RL_PPO_AGENT",
                symbol="SOLUSDT",
                progress_path=path,
            )
            mark_ml_job_running(job_id)
            outcome = request_ml_job_cancel(job_id)
            self.assertTrue(outcome["ok"])
            self.assertTrue(outcome.get("cooperative"))
            self.assertTrue(ml_cancel_requested(path))
            self.assertEqual(get_ml_job(job_id)["status"], "running")
        finally:
            cleanup_ml_progress(path)

    def test_counts_and_list(self):
        create_ml_job(kind="train", strategy="A", symbol="X")
        j2 = create_ml_job(kind="validate", strategy="B", symbol="Y")
        mark_ml_job_running(j2)
        counts = ml_job_counts()
        self.assertEqual(counts["queued"], 1)
        self.assertEqual(counts["active"], 1)
        rows = list_ml_jobs(limit=10)
        self.assertGreaterEqual(len(rows), 2)


class MlJobProgressTests(unittest.TestCase):
    def test_write_read_cancel(self):
        path = make_progress_path("prog")
        try:
            write_ml_progress(path, pct=33, phase="fold 1/3", detail="training")
            data = read_ml_progress(path)
            self.assertEqual(data["pct"], 33)
            self.assertEqual(data["phase"], "fold 1/3")
            self.assertFalse(ml_cancel_requested(path))
            request_ml_cancel_file(path)
            self.assertTrue(ml_cancel_requested(path))
        finally:
            cleanup_ml_progress(path)
            self.assertFalse(os.path.isfile(path))


class MlTrainExecutorJobWiringTests(unittest.TestCase):
    def setUp(self):
        reset_ml_job_store_for_tests()

    def tearDown(self):
        reset_ml_job_store_for_tests()

    def test_blocking_submit_registers_and_finishes(self):
        import asyncio
        from app.services.bots.ml_train_executor import submit_train_job

        async def _run():
            with patch(
                "app.services.bots.ml_train_executor.run_train_job",
                return_value={"ok": True, "symbol": "BTCUSDT"},
            ):
                with patch(
                    "app.services.bots.ml_model_artifacts.invalidate_strategy_model_caches",
                ):
                    with patch("app.config.ML_TRAIN_PROCESS_ISOLATION", False):
                        return await submit_train_job(
                            "ML_SIGNAL_BOOST",
                            "BTCUSDT",
                            [{"close": 1}],
                            {"n_estimators": 5},
                        )

        out = asyncio.run(_run())
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("job_id"))
        job = get_ml_job(out["job_id"])
        self.assertEqual(job["status"], "done")


if __name__ == "__main__":
    unittest.main()
