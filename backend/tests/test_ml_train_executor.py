"""Tests for ML train process isolation (MEMORY_CENTRIC_REVIEW #9)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.bots.ml_train_executor import run_train_job, run_validate_job


class MlTrainExecutorTests(unittest.TestCase):
    def test_run_train_job_dispatches_and_returns_dict(self):
        with patch(
            "app.services.bots.strategies_ml.train_ml_signal_model",
            return_value={"ok": True, "symbol": "BTCUSDT"},
        ) as train:
            out = run_train_job("ML_SIGNAL_BOOST", "BTCUSDT", [{"close": 1}], {"n_estimators": 10})
        self.assertTrue(out["ok"])
        train.assert_called_once()
        args, kwargs = train.call_args
        self.assertEqual(args[0], "BTCUSDT")
        self.assertEqual(kwargs.get("config", {}).get("n_estimators"), 10)

    def test_run_train_job_unknown_strategy(self):
        out = run_train_job("NOPE", "BTCUSDT", [], {})
        self.assertFalse(out["ok"])
        self.assertIn("not supported", out["error"])

    def test_run_validate_job_delegates(self):
        with patch(
            "app.services.bots.ml_walk_forward_validator.walk_forward_ml_train",
            return_value={"ok": True, "aggregate": {"mean_oos_accuracy": 0.55}},
        ):
            with patch(
                "app.services.bots.ml_model_artifacts.persist_ml_validation_metadata",
                return_value={"ok": True},
            ):
                out = run_validate_job(
                    "ML_SIGNAL_BOOST",
                    "ETHUSDT",
                    [{"close": i} for i in range(100)],
                    {},
                    2,
                    "rolling",
                    False,
                    4,
                )
        self.assertTrue(out["ok"])
        self.assertEqual(out.get("mean_accuracy"), 0.55)


if __name__ == "__main__":
    unittest.main()
