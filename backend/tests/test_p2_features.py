"""Tests for P2 items from AI_FINE_TUNING_POST_TRADE_LEARNING.md.

Covers: stacking online update (#9), adaptive regime boundary calibration
(#10), Optuna transfer helpers (#11), isotonic calibration layer (#12), and
the copilot prompt library (#13).
"""

import os
import tempfile
import unittest
from unittest import mock

import numpy as np

_tmp = tempfile.mkdtemp(prefix="p2_test_")
os.environ["TRADING_DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["DATA_DIR"] = _tmp


class TestStackingOnlineUpdate(unittest.TestCase):
    def setUp(self):
        from app.services.bots import stacking_meta_learner as sml

        sml._online_buffers.clear()
        sml._pending_base_probs.clear()
        sml._online_since_update.clear()
        self.sml = sml
        self.bot = "test-bot-stacking"
        # Persist a base model so maybe_update_stacking has something to load.
        model = sml.StackingModel(
            mode="inverse_mse",
            weights=(0.5, 0.5),
            base_names=("ta", "ml"),
            n_oos=10,
        )
        sml.save_stacking_model(self.bot, model)

    def tearDown(self):
        self.sml.invalidate_stacking_cache(self.bot)

    def _fill_buffer(self, n: int):
        for i in range(n):
            # ta is a good predictor, ml is a coin flip
            win = i % 2 == 0
            self.sml.note_pending_base_probs(self.bot, [0.9 if win else 0.1, 0.5])
            self.sml.record_stacking_outcome(self.bot, win=win)

    def test_outcome_recording_ring_buffer(self):
        self._fill_buffer(10)
        buf = self.sml._online_buffers[self.bot]
        self.assertEqual(len(buf), 10)
        self.assertEqual(buf[0]["label"], 1)

    def test_update_not_due_below_threshold(self):
        self._fill_buffer(5)
        with mock.patch("app.config.STACKING_ONLINE_UPDATE_EVERY_N", 100):
            res = self.sml.maybe_update_stacking(self.bot)
        self.assertFalse(res["updated"])
        self.assertEqual(res["reason"], "not_due")

    def test_update_recomputes_weights(self):
        self._fill_buffer(120)
        with mock.patch("app.config.STACKING_ONLINE_UPDATE_EVERY_N", 100):
            res = self.sml.maybe_update_stacking(self.bot)
        self.assertTrue(res["updated"])
        # ta (index 0) should dominate — it's perfectly correlated with label
        self.assertGreater(res["weights"][0], res["weights"][1])

    def test_update_requires_model(self):
        self.sml.note_pending_base_probs("no-model-bot", [0.5, 0.5])
        self.sml.record_stacking_outcome("no-model-bot", win=True)
        self.sml._online_since_update["no-model-bot"] = 999
        res = self.sml.maybe_update_stacking("no-model-bot")
        self.assertFalse(res["updated"])
        self.assertEqual(res["reason"], "no_model")


class TestAdaptiveRegimeBoundary(unittest.TestCase):
    def setUp(self):
        from app.services.bots import hmm_regime

        hmm_regime._last_boundary_calib.clear()
        self.hmm = hmm_regime
        self.bot = "test-bot-regime"

    def _make_candles(self, n: int, start: float = 100.0, drift: float = 0.001):
        candles = []
        price = start
        for i in range(n):
            price *= 1.0 + drift + 0.002 * np.sin(i)
            candles.append({"close": price, "open": price * 0.999,
                            "high": price * 1.001, "low": price * 0.998})
        return candles

    def test_no_model_returns_reason(self):
        candles = self._make_candles(600)
        res = self.hmm.adaptive_recalibrate_regime("missing-bot", candles)
        self.assertFalse(res["updated"])
        self.assertEqual(res["reason"], "no_model")

    def test_recalibrate_updates_and_debounces(self):
        candles = self._make_candles(600)
        model = self.hmm.fit_regime_model(candles[:500])
        self.assertIsNotNone(model)
        self.hmm.save_regime_model(self.bot, model)

        with mock.patch("app.config.REGIME_BOUNDARY_CALIB_ENABLED", True), \
             mock.patch("app.config.REGIME_BOUNDARY_CALIB_INTERVAL_SEC", 86400):
            res = self.hmm.adaptive_recalibrate_regime(self.bot, candles)
            self.assertTrue(res["updated"])
            # Immediate second call is debounced
            res2 = self.hmm.adaptive_recalibrate_regime(self.bot, candles)
            self.assertFalse(res2["updated"])
            self.assertEqual(res2["reason"], "debounced")

    def test_weight_floor_and_shift_clamp(self):
        candles = self._make_candles(600)
        model = self.hmm.fit_regime_model(candles[:500])
        self.hmm.save_regime_model(self.bot, model)
        with mock.patch("app.config.REGIME_BOUNDARY_CALIB_ENABLED", True), \
             mock.patch("app.config.REGIME_BOUNDARY_MIN_WEIGHT", 0.05):
            res = self.hmm.adaptive_recalibrate_regime(self.bot, candles)
        self.assertTrue(res["updated"])
        updated = self.hmm.load_regime_model(self.bot)
        for w in updated.weights:
            self.assertGreaterEqual(w, 0.05 - 1e-9)
        self.assertAlmostEqual(sum(updated.weights), 1.0, places=6)


class TestOptunaTransferHelpers(unittest.TestCase):
    def test_transfer_path_convention(self):
        from app.services.bots.ml_job_checkpoint import (
            optuna_transfer_study_name,
            optuna_transfer_study_path,
        )

        path = optuna_transfer_study_path("ml_signal_boost", "BTC/USDT", "15m")
        self.assertIn("transfer", path.replace("\\", "/"))
        self.assertTrue(path.endswith(".db"))
        name = optuna_transfer_study_name("ml_signal_boost", "BTCUSDT", "15m")
        self.assertEqual(name, "ML_SIGNAL_BOOST_BTCUSDT_15m")

    def test_transfer_path_sanitizes(self):
        from app.services.bots.ml_job_checkpoint import optuna_transfer_study_path

        p1 = optuna_transfer_study_path("LSTM_DIRECTION", "SOL-USDT", None)
        self.assertNotIn("/", os.path.basename(p1))
        self.assertIn("none", os.path.basename(p1).lower())

    def test_sweep_uses_local_transfer_helpers(self):
        """RL Auto-Tune must not named-import transfer helpers from checkpoint.

        RL_PPO_AGENT runs in-process; a stale ``ml_job_checkpoint`` in
        sys.modules would raise ImportError and fail the whole sweep.
        """
        import inspect
        from app.services.bots import ml_hyperparam_sweep as sweep

        src = inspect.getsource(sweep.run_ml_hyperparam_sweep)
        self.assertNotIn("optuna_transfer_study_name,", src)
        self.assertNotIn("optuna_transfer_study_path,", src)
        name = sweep.optuna_transfer_study_name("rl_ppo_agent", "ADAUSD", "1h")
        self.assertEqual(name, "RL_PPO_AGENT_ADAUSD_1h")
        path = sweep.optuna_transfer_study_path("rl_ppo_agent", "ADAUSD", "1h")
        self.assertIn("transfer", path.replace("\\", "/"))
        self.assertTrue(path.endswith(".db"))


class TestIsotonicCalibration(unittest.TestCase):
    def _rows(self, n: int):
        rows = []
        rng = np.random.RandomState(0)
        for i in range(n):
            win = i % 2 == 0
            rows.append({
                "features": {name: float(rng.randn()) for name in _feature_names()},
                "win": win,
                "pnl": 10.0 if win else -10.0,
            })
        return rows

    def test_calibrator_fitted_and_returned(self):
        from app.services.bots.meta_label_model import train_model_from_rows

        rows = self._rows(120)
        with mock.patch("app.config.META_LABEL_ISOTONIC_ENABLED", True), \
             mock.patch("app.config.META_LABEL_ISOTONIC_MIN_SAMPLES", 10):
            res = train_model_from_rows(rows, min_samples=20, val_fraction=0.25)
        self.assertTrue(res["ok"])
        self.assertIsNotNone(res.get("calibrator"))
        # Calibrator maps [0,1] -> [0,1]
        cal = res["calibrator"]
        out = cal.predict([0.2, 0.8])
        self.assertTrue(all(0.0 <= v <= 1.0 for v in out))

    def test_calibrator_skipped_when_disabled(self):
        from app.services.bots.meta_label_model import train_model_from_rows

        rows = self._rows(120)
        with mock.patch("app.config.META_LABEL_ISOTONIC_ENABLED", False):
            res = train_model_from_rows(rows, min_samples=20, val_fraction=0.25)
        self.assertTrue(res["ok"])
        self.assertIsNone(res.get("calibrator"))


def _feature_names():
    from app.services.bots.meta_label_model import FEATURE_NAMES
    return list(FEATURE_NAMES)


class TestPromptLibrary(unittest.TestCase):
    def setUp(self):
        from app.services.agent import prompt_library

        prompt_library._cache.clear()
        self.pl = prompt_library

    def test_available_intents(self):
        intents = self.pl.available_intents()
        self.assertIn("analysis", intents)
        self.assertIn("explain", intents)
        self.assertIn("action", intents)

    def test_build_system_prompt_includes_exemplars(self):
        prompt = self.pl.build_system_prompt("analysis", "what is BTCUSDT doing now?")
        self.assertIsNotNone(prompt)
        self.assertIn("Few-shot examples", prompt)
        self.assertIn("TRADE_COPILOT", prompt)

    def test_retrieve_best_matching_exemplar(self):
        ex = self.pl.retrieve_exemplars("explain", "why was my bot paused?", max_exemplars=1)
        self.assertEqual(len(ex), 1)
        self.assertIn("paused", ex[0]["user"].lower())

    def test_unknown_intent_returns_none(self):
        self.assertIsNone(self.pl.build_system_prompt("nonexistent", "hello"))

    def test_negative_example_logging(self):
        log_path = self.pl._NEGATIVE_LOG
        if os.path.isfile(log_path):
            os.remove(log_path)
        self.pl.log_negative_example("analysis", "what is btc doing", "wrong symbol")
        self.assertTrue(os.path.isfile(log_path))
        with open(log_path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("wrong symbol", content)
        os.remove(log_path)


if __name__ == "__main__":
    unittest.main()
