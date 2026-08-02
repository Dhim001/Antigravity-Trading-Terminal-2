"""Tests for ML train process isolation (MEMORY_CENTRIC_REVIEW #9)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.bots.ml_train_executor import (
    _is_broken_pool_error,
    reset_ml_train_pool,
    run_train_job,
    run_validate_job,
    shutdown_ml_train_pool,
    use_process_pool_for_strategy,
)


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

    def test_validate_default_capacity_parity_keeps_train_scale(self):
        captured = {}

        def _capture(strategy, symbol, candles, config=None, **kwargs):
            captured["config"] = dict(config or {})
            return {"ok": True, "aggregate": {"mean_oos_accuracy": 0.5}}

        with patch(
            "app.services.bots.ml_walk_forward_validator.walk_forward_ml_train",
            side_effect=_capture,
        ):
            with patch(
                "app.services.bots.ml_model_artifacts.persist_ml_validation_metadata",
                return_value={"ok": True},
            ):
                run_validate_job(
                    "RL_PPO_AGENT",
                    "AAPL",
                    [{"close": i} for i in range(500)],
                    {"epochs": 100, "validate_max_bars": 18_000},
                    2,
                    "rolling",
                    False,
                    4,
                )
        cfg = captured["config"]
        self.assertTrue(cfg.get("wf_capacity_parity"))
        self.assertEqual(cfg.get("total_timesteps"), 200_000)
        self.assertEqual(cfg.get("n_steps"), 2048)
        self.assertEqual(cfg.get("ppo_epochs"), 10)
        self.assertNotIn("max_iter", cfg)

    def test_validate_fast_mode_clamps_deep_epochs(self):
        captured = {}

        def _capture(strategy, symbol, candles, config=None, **kwargs):
            captured["config"] = dict(config or {})
            return {"ok": True, "aggregate": {"mean_oos_accuracy": 0.5}}

        with patch(
            "app.services.bots.ml_walk_forward_validator.walk_forward_ml_train",
            side_effect=_capture,
        ):
            with patch(
                "app.services.bots.ml_model_artifacts.persist_ml_validation_metadata",
                return_value={"ok": True},
            ):
                run_validate_job(
                    "LSTM_DIRECTION",
                    "AAPL",
                    [{"close": i} for i in range(500)],
                    {"wf_capacity_parity": False, "epochs": 100},
                    2,
                    "rolling",
                    False,
                    4,
                )
        cfg = captured["config"]
        self.assertFalse(cfg.get("wf_capacity_parity"))
        self.assertEqual(cfg.get("epochs"), 12)
        self.assertNotIn("max_iter", cfg)

    def test_capacity_parity_ignores_exploratory_sim_mode(self):
        """Deploy-grade WF must stay live_aligned even if ML_EXPLORATORY_SIM_MODE is set."""
        captured = {}

        def _capture(strategy, symbol, candles, config=None, **kwargs):
            captured["config"] = dict(config or {})
            return {"ok": True, "aggregate": {"mean_oos_accuracy": 0.5}}

        with patch("app.config.ML_EXPLORATORY_SIM_MODE", "research_fast"):
            with patch(
                "app.services.bots.ml_walk_forward_validator.walk_forward_ml_train",
                side_effect=_capture,
            ):
                with patch(
                    "app.services.bots.ml_model_artifacts.persist_ml_validation_metadata",
                    return_value={"ok": True},
                ):
                    run_validate_job(
                        "ML_SIGNAL_BOOST",
                        "ETHUSDT",
                        [{"close": i} for i in range(200)],
                        {},
                        2,
                        "rolling",
                        False,
                        4,
                    )
        self.assertEqual(captured["config"].get("sim_mode"), "live_aligned")
        self.assertTrue(captured["config"].get("wf_capacity_parity"))

    def test_lean_validate_can_use_exploratory_sim_mode(self):
        captured = {}

        def _capture(strategy, symbol, candles, config=None, **kwargs):
            captured["config"] = dict(config or {})
            return {"ok": True, "aggregate": {"mean_oos_accuracy": 0.5}}

        with patch("app.config.ML_EXPLORATORY_SIM_MODE", "research_fast"):
            with patch(
                "app.services.bots.ml_walk_forward_validator.walk_forward_ml_train",
                side_effect=_capture,
            ):
                with patch(
                    "app.services.bots.ml_model_artifacts.persist_ml_validation_metadata",
                    return_value={"ok": True},
                ):
                    run_validate_job(
                        "ML_SIGNAL_BOOST",
                        "ETHUSDT",
                        [{"close": i} for i in range(200)],
                        {"wf_capacity_parity": False},
                        2,
                        "rolling",
                        False,
                        4,
                    )
        self.assertEqual(captured["config"].get("sim_mode"), "research_fast")
        self.assertFalse(captured["config"].get("wf_capacity_parity"))

    def test_torch_strategies_prefer_in_process_thread(self):
        with patch("app.config.ML_TRAIN_PROCESS_ISOLATION", True):
            with patch("app.config.ML_TRAIN_TORCH_IN_PROCESS", True):
                self.assertFalse(use_process_pool_for_strategy("LSTM_DIRECTION"))
                self.assertFalse(use_process_pool_for_strategy("RL_PPO_AGENT"))
                self.assertTrue(use_process_pool_for_strategy("ML_SIGNAL_BOOST"))

    def test_torch_can_opt_into_process_pool(self):
        with patch("app.config.ML_TRAIN_PROCESS_ISOLATION", True):
            with patch("app.config.ML_TRAIN_TORCH_IN_PROCESS", False):
                self.assertTrue(use_process_pool_for_strategy("LSTM_DIRECTION"))

    def test_broken_pool_error_detection(self):
        class BrokenProcessPool(Exception):
            pass

        self.assertTrue(
            _is_broken_pool_error(
                BrokenProcessPool(
                    "A child process terminated abruptly, the process pool is not usable anymore"
                )
            )
        )
        self.assertTrue(
            _is_broken_pool_error(RuntimeError("process pool is not usable anymore"))
        )
        self.assertFalse(_is_broken_pool_error(RuntimeError("CUDA OOM")))

    def test_reset_ml_train_pool_clears_singleton(self):
        import app.services.bots.ml_train_executor as exe
        from unittest.mock import MagicMock

        sentinel = MagicMock()
        exe._pool = sentinel
        reset_ml_train_pool(reason="test")
        sentinel.shutdown.assert_called_once()
        self.assertIsNone(exe._pool)
        shutdown_ml_train_pool()

    def test_run_in_pool_retries_after_broken_pool(self):
        import asyncio
        from concurrent.futures import Future

        from app.services.bots import ml_train_executor as exe

        class BrokenProcessPool(Exception):
            pass

        calls = {"n": 0}

        class _Pool:
            def submit(self, fn, *args):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise BrokenProcessPool(
                        "A child process terminated abruptly, the process pool is not usable anymore"
                    )
                fut: Future = Future()
                fut.set_result({"ok": True, "retried": True})
                return fut

        with patch.object(exe, "use_process_pool_for_strategy", return_value=True):
            with patch.object(exe, "get_ml_train_pool", return_value=_Pool()):
                with patch.object(exe, "reset_ml_train_pool") as reset:
                    with patch(
                        "app.services.bots.ml_job_store.attach_ml_job_future",
                    ):
                        with patch(
                            "app.services.bots.ml_job_store.is_ml_job_cancelled",
                            return_value=False,
                        ):
                            with patch(
                                "app.services.bots.ml_job_store.mark_ml_job_running",
                            ):
                                out = asyncio.run(
                                    exe._run_in_pool(
                                        lambda: None,
                                        job_id="job-1",
                                        strategy="LSTM_DIRECTION",
                                    )
                                )
        self.assertTrue(out.get("ok"))
        self.assertEqual(calls["n"], 2)
        reset.assert_called()


if __name__ == "__main__":
    unittest.main()
