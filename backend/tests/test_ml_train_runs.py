"""Tests for persistent ML train run history + memory telemetry (order 4)."""

from __future__ import annotations

import unittest

from app.database import init_db
from app.services.bots.ml_job_store import (
    create_ml_job,
    finish_ml_job,
    mark_ml_job_running,
    ml_job_counts,
    reset_ml_job_store_for_tests,
)
from app.services.bots.ml_train_runs import list_ml_train_runs, record_ml_train_run_from_job
from app.services.memory_snapshot import memory_subsystem_snapshot


class MlTrainRunsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        reset_ml_job_store_for_tests()

    def tearDown(self):
        reset_ml_job_store_for_tests()

    def test_record_and_list(self):
        job = {
            "job_id": "job-test-1",
            "kind": "train",
            "strategy": "ML_SIGNAL_BOOST",
            "symbol": "TESTSYM_MLRUNS",
            "status": "done",
            "started_at": "2026-07-20T12:00:00Z",
            "finished_at": "2026-07-20T12:01:30Z",
            "result": {
                "ok": True,
                "metrics": {"val_accuracy": 0.61},
                "version_id": "20260720T120130Z",
            },
        }
        run_id = record_ml_train_run_from_job(job)
        self.assertTrue(run_id)
        rows = list_ml_train_runs(symbol="TESTSYM_MLRUNS", strategy="ML_SIGNAL_BOOST", limit=5)
        self.assertGreaterEqual(len(rows), 1)
        hit = next(r for r in rows if r["id"] == run_id)
        self.assertTrue(hit["ok"])
        self.assertEqual(hit["kind"], "train")
        self.assertEqual(hit["duration_ms"], 90_000)
        self.assertEqual(hit["metrics"].get("val_accuracy"), 0.61)
        self.assertEqual(hit["version_id"], "20260720T120130Z")
        self.assertEqual(hit["model_label"], "ml_signal_boost")
        self.assertEqual(hit["artifact"], "model.joblib")
        self.assertEqual(hit["model_name"], "ml_signal_boost")

    def test_record_extracts_version_from_metadata_and_display_name(self):
        job = {
            "job_id": "job-meta-1",
            "kind": "train",
            "strategy": "TRANSFORMER_SIGNAL",
            "symbol": "ETHUSDT",
            "status": "done",
            "started_at": "2026-07-20T12:00:00Z",
            "finished_at": "2026-07-20T12:05:00Z",
            "result": {
                "ok": True,
                "timeframe": "1m",
                "metrics": {"val_accuracy": 0.42},
                "metadata": {
                    "version_id": "20260720T120500Z",
                    "display_name": "ETH transformer v2",
                },
            },
        }
        run_id = record_ml_train_run_from_job(job)
        rows = list_ml_train_runs(symbol="ETHUSDT", strategy="TRANSFORMER_SIGNAL", limit=5)
        hit = next(r for r in rows if r["id"] == run_id)
        self.assertEqual(hit["version_id"], "20260720T120500Z")
        self.assertEqual(hit["display_name"], "ETH transformer v2")
        self.assertEqual(hit["model_name"], "transformer_signal")
        self.assertEqual(hit["artifact"], "transformer_signal.onnx")

    def test_list_exposes_model_identity_fields(self):
        job = {
            "job_id": "job-identity-1",
            "kind": "train",
            "strategy": "LSTM_DIRECTION",
            "symbol": "TESTSYM_MLID",
            "status": "done",
            "started_at": "2026-07-20T12:00:00Z",
            "finished_at": "2026-07-20T12:03:00Z",
            "result": {
                "ok": True,
                "timeframe": "1m",
                "version_id": "20260720T120300Z",
                "metrics": {"val_accuracy": 0.5},
            },
        }
        run_id = record_ml_train_run_from_job(job)
        rows = list_ml_train_runs(symbol="TESTSYM_MLID", strategy="LSTM_DIRECTION", limit=5)
        hit = next(r for r in rows if r["id"] == run_id)
        self.assertEqual(hit["version_id"], "20260720T120300Z")
        self.assertEqual(hit["artifact"], "lstm_direction.onnx")
        self.assertEqual(hit["model_label"], "lstm_direction")

    def test_record_stores_timeframe(self):
        job = {
            "job_id": "job-tf-1",
            "kind": "train",
            "strategy": "TRANSFORMER_SIGNAL",
            "symbol": "TESTSYM_MLTF",
            "status": "done",
            "started_at": "2026-07-20T12:00:00Z",
            "finished_at": "2026-07-20T12:02:00Z",
            "result": {
                "ok": True,
                "timeframe": "5m",
                "metrics": {"val_accuracy": 0.55},
                "version_id": "20260720T120200Z",
            },
        }
        run_id = record_ml_train_run_from_job(job)
        self.assertTrue(run_id)
        rows = list_ml_train_runs(
            symbol="TESTSYM_MLTF", strategy="TRANSFORMER_SIGNAL", timeframe="5m", limit=5,
        )
        hit = next(r for r in rows if r["id"] == run_id)
        self.assertEqual(hit["timeframe"], "5m")

    def test_finish_job_persists_run(self):
        job_id = create_ml_job(kind="validate", strategy="LSTM_DIRECTION", symbol="ETHUSDT")
        mark_ml_job_running(job_id)
        finish_ml_job(
            job_id,
            "done",
            result={"ok": True, "mean_accuracy": 0.55, "n_folds": 3},
        )
        rows = list_ml_train_runs(symbol="ETHUSDT", strategy="LSTM_DIRECTION", limit=10)
        self.assertTrue(any(r.get("job_id") == job_id for r in rows))

    def test_memory_snapshot_includes_ml_jobs(self):
        create_ml_job(kind="train", strategy="A", symbol="X")
        snap = memory_subsystem_snapshot()
        self.assertIn("ml_jobs", snap)
        self.assertEqual(snap["ml_jobs"]["queued"], ml_job_counts()["queued"])


if __name__ == "__main__":
    unittest.main()
