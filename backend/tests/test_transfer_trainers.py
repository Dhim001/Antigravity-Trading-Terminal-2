"""Donor warm-start tests for the RL PPO and LSTM trainers.

Runs real (tiny) training jobs against temp model roots to verify:
- donor weights load and the budget/LR shrink,
- the KL guard restores donor weights when the fine-tune drifts too far,
- WF folds ignore donors (honest OOS),
- LSTM cross-symbol donor warm-start records lineage.
"""

import json
import math
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

_tmp = tempfile.mkdtemp(prefix="transfer_trainer_test_")
os.environ["TRADING_DB_PATH"] = os.path.join(_tmp, "test.db")

import pytest  # noqa: E402

torch = pytest.importorskip("torch")  # noqa: E402 — trainers need PyTorch

from app.services.bots.ml_feature_engineering import SIGNAL_FEATURE_VERSION  # noqa: E402
from app.services.bots.rl_trading_env import N_ACTIONS, OBS_DIM  # noqa: E402

_DATA = os.path.join(_tmp, "data")


def _make_candles(n=300, trend=0.1, atr=2.0):
    candles = []
    for i in range(n):
        # Oscillation amplitude (6) exceeds the default triple-barrier width
        # (atr_mult 2.0 × ATR 2.0 = 4) so supervised trainers see ≥2 label
        # classes; the drift keeps a mild uptrend for the RL env.
        c = 100.0 + i * trend + 6.0 * math.sin(i / 2.5)
        candles.append({
            "time": 1700000000 + i * 60,
            "open": c - 0.5,
            "high": c + 1.0,
            "low": c - 1.0,
            "close": c,
            "volume": 1000.0 + i,
            "ATR_14": atr,
            "RSI_14": 50.0 + (i % 20),
            "MACDh_12_26_9": 0.1 * (i % 10 - 5),
            "STOCHk_14_3_3": 50.0,
            "ADX_14": 25.0,
            "EMA_9": c - 0.2,
            "EMA_21": c - 0.5,
        })
    return candles


def _write_ppo_donor(symbol="BTCUSDT", hidden_dim=64):
    from app.services.bots.rl_ppo_trainer import _build_actor_critic

    root = os.path.join(_DATA, "rl_ppo_models", symbol)
    os.makedirs(root, exist_ok=True)
    model = _build_actor_critic(obs_dim=OBS_DIM, act_dim=N_ACTIONS, hidden_dim=hidden_dim)
    torch.save(model.state_dict(), os.path.join(root, "policy.pt"))
    with open(os.path.join(root, "scaler.json"), "w", encoding="utf-8") as fh:
        json.dump({"feat_mean": [0.0] * 5, "feat_std": [1.0] * 5}, fh)
    meta = {
        "symbol": symbol,
        "timeframe": "1m",
        "model_type": "rl_ppo",
        "feature_schema_version": SIGNAL_FEATURE_VERSION,
        "obs_dim": OBS_DIM,
        "n_actions": N_ACTIONS,
        "trained_at": "2026-08-10T12:00:00Z",
        "version_id": "20260810T120000Z",
        "metrics": {"mean_return_pct": 2.5, "episodes": 100},
    }
    with open(os.path.join(root, "metadata.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    return model


def _fake_onnx_export(model, args, dest_path, **kwargs):
    """The test env lacks the ``onnx`` package — write a placeholder file."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as fh:
        fh.write(b"ONNX-PLACEHOLDER")
    return dest_path


def _fake_ppo_export(symbol, model, *, timeframe=None):
    from app.services.bots.rl_ppo_trainer import _onnx_path

    path = _onnx_path(symbol, timeframe)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"ONNX-PLACEHOLDER")
    return path


def _ppo_patches():
    return [
        mock.patch("app.services.bots.ml_model_artifacts.BASE_DIR", _tmp),
        mock.patch("app.config.DATA_DIR", _DATA),
        mock.patch(
            "app.services.bots.rl_ppo_trainer.PPO_MODEL_DIR",
            os.path.join(_DATA, "rl_ppo_models"),
        ),
        # The test env has torch but not the onnx package — stub the export.
        mock.patch(
            "app.services.bots.rl_ppo_trainer._export_policy_onnx",
            side_effect=_fake_ppo_export,
        ),
    ]


def _ppo_config(**over):
    cfg = {
        "timeframe": "1m",
        # merge_strategy_config injects the 200k strategy default, which wins
        # over the function arg — set the test budget here (as production does
        # via ml_train_executor).
        "total_timesteps": 2560,
        "max_episode_steps": 64,
        "hidden_dim": 64,
        "n_steps": 64,
        "ppo_epochs": 1,
        "batch_size": 32,
        "force_cpu": True,
        "skip_snapshot": True,
        "donor": {"symbol": "BTCUSDT"},
    }
    cfg.update(over)
    return cfg


class TestPpoDonorWarmStart(unittest.TestCase):
    def setUp(self):
        import shutil

        if os.path.isdir(_DATA):
            shutil.rmtree(_DATA, ignore_errors=True)
        os.makedirs(_DATA, exist_ok=True)

    def test_donor_warm_start_reduces_budget_and_records_lineage(self):
        from app.services.bots.rl_ppo_trainer import train_ppo_agent

        donor_model = _write_ppo_donor()
        patches = _ppo_patches()
        for p in patches:
            p.start()
        try:
            result = train_ppo_agent(
                "ADAUSD", _make_candles(300),
                config=_ppo_config(), total_timesteps=2560,
            )
        finally:
            for p in patches:
                p.stop()

        self.assertTrue(result.get("ok"), result.get("error"))
        transfer = (result.get("metrics") or {}).get("transfer")
        self.assertIsNotNone(transfer, "metrics.transfer missing")
        self.assertEqual(transfer["donor_symbol"], "BTCUSDT")
        self.assertEqual(transfer["scaler_strategy"], "recompute")
        self.assertFalse(transfer["transfer_rejected"])
        # Budget reduced: 2560 * 0.25 = 640 (above the 64*5 min-steps floor).
        self.assertLessEqual(result["metrics"]["total_timesteps"], 640)
        # Lineage persisted into metadata.json of the target model dir.
        meta_path = os.path.join(_DATA, "rl_ppo_models", "ADAUSD", "metadata.json")
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        self.assertEqual(meta["transfer"]["donor_symbol"], "BTCUSDT")
        self.assertEqual(meta["transfer"]["method"], "weight_warm_start")
        self.assertEqual(
            meta["transfer"]["finetune_budget"]["total_timesteps"], 640,
        )
        # Trainable checkpoint persisted for future donors.
        self.assertTrue(
            os.path.isfile(os.path.join(_DATA, "rl_ppo_models", "ADAUSD", "policy.pt")),
        )
        del donor_model

    def test_kl_guard_rejection_restores_donor_weights(self):
        from app.services.bots.rl_ppo_trainer import train_ppo_agent

        donor_model = _write_ppo_donor()
        donor_state = {k: v.clone() for k, v in donor_model.state_dict().items()}
        patches = _ppo_patches() + [
            # KL is always >= 0, so a negative ceiling always triggers.
            mock.patch("app.config.RL_TRANSFER_MAX_KL", -1.0),
        ]
        for p in patches:
            p.start()
        try:
            result = train_ppo_agent(
                "ADAUSD", _make_candles(300),
                config=_ppo_config(), total_timesteps=2560,
            )
        finally:
            for p in patches:
                p.stop()

        self.assertTrue(result.get("ok"), result.get("error"))
        transfer = result["metrics"]["transfer"]
        self.assertTrue(transfer["transfer_rejected"])
        self.assertIsNotNone(transfer["kl_divergence"])
        # The persisted checkpoint must be the donor weights, not the drifted ones.
        ckpt = os.path.join(_DATA, "rl_ppo_models", "ADAUSD", "policy.pt")
        saved = torch.load(ckpt, map_location="cpu")
        for k, v in donor_state.items():
            self.assertTrue(torch.equal(saved[k], v), f"weight mismatch at {k}")

    def test_wf_mode_ignores_donor(self):
        from app.services.bots.rl_ppo_trainer import train_ppo_agent

        _write_ppo_donor()
        patches = _ppo_patches()
        for p in patches:
            p.start()
        try:
            result = train_ppo_agent(
                "ADAUSD", _make_candles(300),
                config=_ppo_config(_wf_mode=True, wf_capacity_parity=False),
                total_timesteps=2560,
            )
        finally:
            for p in patches:
                p.stop()

        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertNotIn("transfer", result.get("metrics") or {})
        self.assertNotIn("transfer", result)


class TestLstmDonorWarmStart(unittest.TestCase):
    def setUp(self):
        import shutil

        if os.path.isdir(_DATA):
            shutil.rmtree(_DATA, ignore_errors=True)
        os.makedirs(_DATA, exist_ok=True)

    def _write_lstm_donor(self, symbol="BTCUSDT", hidden_dim=16, num_layers=1):
        from app.services.bots.ml_lstm_trainer import _build_lstm_model

        root = os.path.join(_DATA, "lstm_signal_models", symbol)
        os.makedirs(root, exist_ok=True)
        model = _build_lstm_model(
            input_dim=len_model_features(), hidden_dim=hidden_dim,
            num_layers=num_layers, num_classes=3,
        )
        torch.save(model.state_dict(), os.path.join(root, "lstm_direction.pt"))
        meta = {
            "symbol": symbol,
            "timeframe": "1m",
            "model_type": "lstm_direction",
            "feature_schema_version": SIGNAL_FEATURE_VERSION,
            "trained_at": "2026-08-10T12:00:00Z",
            "version_id": "20260810T120000Z",
            "metrics": {"val_accuracy": 0.55},
        }
        with open(os.path.join(root, "metadata.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh)

    def test_lstm_donor_warm_start(self):
        from app.services.bots.ml_lstm_trainer import train_lstm_signal_model

        self._write_lstm_donor()
        patches = [
            mock.patch("app.services.bots.ml_model_artifacts.BASE_DIR", _tmp),
            mock.patch("app.config.DATA_DIR", _DATA),
            mock.patch(
                "app.services.bots.ml_lstm_trainer.LSTM_MODEL_DIR",
                os.path.join(_DATA, "lstm_signal_models"),
            ),
            mock.patch("app.config.ML_WARM_START_EPOCHS", 1),
            mock.patch("app.config.ML_WARM_START_LR_FACTOR", 0.1),
            # The test env has torch but not the onnx package — stub the export.
            mock.patch(
                "app.services.bots.ml_model_artifacts.export_onnx_single_file",
                side_effect=_fake_onnx_export,
            ),
        ]
        for p in patches:
            p.start()
        try:
            result = train_lstm_signal_model(
                "ADAUSD", _make_candles(260),
                config={
                    "timeframe": "1m",
                    "lookback": 20,
                    "hidden_dim": 16,
                    "num_layers": 1,
                    "epochs": 30,  # donor path must shrink this to the warm-start budget
                    "min_train_samples": 40,
                    "triple_barrier_max_bars": 10,
                    "force_cpu": True,
                    "skip_snapshot": True,
                    "donor": {"symbol": "BTCUSDT"},
                },
            )
        finally:
            for p in patches:
                p.stop()

        self.assertTrue(result.get("ok"), result.get("error"))
        metrics = result.get("metrics") or {}
        self.assertTrue(metrics.get("warm_started"))
        self.assertEqual(metrics.get("transfer", {}).get("donor_symbol"), "BTCUSDT")
        # Warm-start budget: 1 epoch (ML_WARM_START_EPOCHS), not the 30 requested.
        self.assertEqual(metrics.get("epochs_trained"), 1)
        meta_path = os.path.join(_DATA, "lstm_signal_models", "ADAUSD", "metadata.json")
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        self.assertEqual(meta["transfer"]["donor_symbol"], "BTCUSDT")
        self.assertEqual(meta["transfer"]["finetune_budget"]["epochs"], 1)


def len_model_features() -> int:
    from app.services.bots.ml_feature_engineering import SIGNAL_FEATURE_NAMES

    return len(SIGNAL_FEATURE_NAMES)


def _write_gbm_donor(symbol="BTCUSDT", *, atr_mult=1.25, max_bars=12):
    """GBM donors transfer a recipe, not weights — metadata.json only."""
    root = os.path.join(_DATA, "ml_signal_models", symbol)
    os.makedirs(root, exist_ok=True)
    meta = {
        "symbol": symbol,
        "timeframe": "1m",
        "model_type": "ml_signal_boost",
        "feature_schema_version": SIGNAL_FEATURE_VERSION,
        "trained_at": "2026-08-09T12:00:00Z",
        "version_id": "20260809T120000Z",
        "config": {"atr_mult": atr_mult, "max_holding_bars": max_bars},
        "top_features": [
            {"feature": "RSI_14", "importance": 0.42},
            {"feature": "ATR_14", "importance": 0.31},
        ],
        "metrics": {"val_accuracy": 0.55},
    }
    with open(os.path.join(root, "metadata.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)


def _gbm_patches():
    return [
        mock.patch("app.services.bots.ml_model_artifacts.BASE_DIR", _tmp),
        mock.patch("app.config.DATA_DIR", _DATA),
        mock.patch(
            "app.services.bots.strategies_ml.ML_SIGNAL_MODEL_DIR",
            os.path.join(_DATA, "ml_signal_models"),
        ),
    ]


def _gbm_config(**over):
    cfg = {
        "timeframe": "1m",
        "min_train_samples": 40,
        "gbm_max_iter": 10,
        "skip_snapshot": True,
        "donor": {"symbol": "BTCUSDT"},
    }
    cfg.update(over)
    return cfg


class TestGbmRecipeTransfer(unittest.TestCase):
    def setUp(self):
        import shutil

        if os.path.isdir(_DATA):
            shutil.rmtree(_DATA, ignore_errors=True)
        os.makedirs(_DATA, exist_ok=True)

    def _train(self, config):
        from app.services.bots.strategies_ml import train_ml_signal_model

        patches = _gbm_patches()
        for p in patches:
            p.start()
        try:
            return train_ml_signal_model("ADAUSD", _make_candles(260), config=config)
        finally:
            for p in patches:
                p.stop()

    def _target_metadata(self):
        meta_path = os.path.join(_DATA, "ml_signal_models", "ADAUSD", "metadata.json")
        with open(meta_path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_gbm_recipe_transfer_uses_donor_barrier_defaults(self):
        _write_gbm_donor(atr_mult=1.25, max_bars=12)
        result = self._train(_gbm_config())

        self.assertTrue(result.get("ok"), result.get("error"))
        meta = self._target_metadata()
        # Donor recipe supplied the barrier params (not the strategy defaults).
        self.assertEqual(meta["config"]["atr_mult"], 1.25)
        self.assertEqual(meta["config"]["max_holding_bars"], 12)
        transfer = meta.get("transfer") or {}
        self.assertEqual(transfer.get("method"), "recipe_transfer")
        self.assertEqual(transfer.get("donor_symbol"), "BTCUSDT")
        self.assertEqual(transfer.get("donor_version_id"), "20260809T120000Z")
        # Donor feature importances recorded for comparison.
        self.assertEqual(
            [f["feature"] for f in transfer.get("donor_feature_importances") or []],
            ["RSI_14", "ATR_14"],
        )
        metrics_transfer = (result.get("metrics") or {}).get("transfer") or {}
        self.assertEqual(metrics_transfer.get("method"), "recipe_transfer")

    def test_gbm_target_config_overrides_donor_recipe(self):
        _write_gbm_donor(atr_mult=1.25, max_bars=12)
        result = self._train(_gbm_config(
            triple_barrier_atr_mult=3.0,
            triple_barrier_max_bars=25,
        ))

        self.assertTrue(result.get("ok"), result.get("error"))
        meta = self._target_metadata()
        # Explicit target config always wins over the donor recipe.
        self.assertEqual(meta["config"]["atr_mult"], 3.0)
        self.assertEqual(meta["config"]["max_holding_bars"], 25)
        # Lineage still records the donor.
        self.assertEqual((meta.get("transfer") or {}).get("donor_symbol"), "BTCUSDT")

    def test_gbm_wf_mode_ignores_donor(self):
        _write_gbm_donor(atr_mult=1.25, max_bars=12)
        result = self._train(_gbm_config(_wf_mode=True, wf_capacity_parity=False))

        self.assertTrue(result.get("ok"), result.get("error"))
        # WF folds train from scratch with strategy defaults — no donor recipe,
        # and the fold result carries no transfer lineage.
        self.assertNotEqual(result.get("config", {}).get("atr_mult"), 1.25)
        self.assertNotIn("transfer", result)


if __name__ == "__main__":
    unittest.main()
