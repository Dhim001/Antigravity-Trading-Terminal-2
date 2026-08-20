"""Tests for cross-asset model transfer (model_transfer.py)."""

import json
import os
import tempfile
import unittest
from unittest import mock

_tmp = tempfile.mkdtemp(prefix="model_transfer_test_")
os.environ["TRADING_DB_PATH"] = os.path.join(_tmp, "test.db")

from app.services.bots import model_transfer as mt  # noqa: E402
from app.services.bots.ml_feature_engineering import SIGNAL_FEATURE_VERSION  # noqa: E402
from app.services.bots.rl_trading_env import N_ACTIONS, OBS_DIM  # noqa: E402

_DATA = os.path.join(_tmp, "data")


def _write_donor(
    strategy_subdir: str,
    symbol: str,
    *,
    timeframe_folder: str | None = None,
    model_type: str = "rl_ppo",
    schema_version: int = SIGNAL_FEATURE_VERSION,
    obs_dim: int = OBS_DIM,
    n_actions: int = N_ACTIONS,
    timeframe_meta: str = "1m",
    with_checkpoint: bool = True,
    checkpoint_name: str = "policy.pt",
    with_metrics: bool = True,
    trained_at: str = "2026-08-10T12:00:00Z",
) -> str:
    folder = symbol if not timeframe_folder else f"{symbol}__{timeframe_folder}"
    root = os.path.join(_DATA, strategy_subdir, folder)
    os.makedirs(root, exist_ok=True)
    meta = {
        "symbol": symbol,
        "timeframe": timeframe_meta,
        "model_type": model_type,
        "feature_schema_version": schema_version,
        "obs_dim": obs_dim,
        "n_actions": n_actions,
        "trained_at": trained_at,
        "version_id": "20260810T120000Z",
    }
    if with_metrics:
        meta["metrics"] = {"mean_return_pct": 1.23, "episodes": 42}
    with open(os.path.join(root, "metadata.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    if with_checkpoint:
        with open(os.path.join(root, checkpoint_name), "wb") as fh:
            fh.write(b"\x80\x04 fake-checkpoint")
    with open(os.path.join(root, "scaler.json"), "w", encoding="utf-8") as fh:
        json.dump({"feat_mean": [0.0] * 5, "feat_std": [1.0] * 5}, fh)
    return root


class _PatchDirs:
    """Patch the module-level data roots model_transfer resolves through."""

    def __init__(self):
        self._patches = [
            mock.patch("app.services.bots.ml_model_artifacts.BASE_DIR", _tmp),
            mock.patch("app.config.DATA_DIR", _DATA),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        return False


def _clean_data():
    import shutil

    if os.path.isdir(_DATA):
        shutil.rmtree(_DATA, ignore_errors=True)
    os.makedirs(_DATA, exist_ok=True)


class _TransferTestCase(unittest.TestCase):
    def setUp(self):
        _clean_data()


class TestResolveDonor(_TransferTestCase):
    def test_resolve_current(self):
        with _PatchDirs():
            _write_donor("rl_ppo_models", "BTCUSDT")
            donor = mt.resolve_donor("RL_PPO_AGENT", "BTCUSDT", "1m")
            self.assertIsNotNone(donor)
            self.assertEqual(donor["symbol"], "BTCUSDT")
            self.assertEqual(donor["version_id"], "20260810T120000Z")
            self.assertTrue(os.path.isdir(donor["dir"]))

    def test_resolve_missing_returns_none(self):
        with _PatchDirs():
            self.assertIsNone(mt.resolve_donor("RL_PPO_AGENT", "NOPE", "1m"))

    def test_resolve_unknown_strategy_returns_none(self):
        with _PatchDirs():
            self.assertIsNone(mt.resolve_donor("NOT_A_STRATEGY", "BTCUSDT", "1m"))

    def test_donor_checkpoint_and_scaler(self):
        with _PatchDirs():
            _write_donor("rl_ppo_models", "BTCUSDT")
            donor = mt.resolve_donor("RL_PPO_AGENT", "BTCUSDT", "1m")
            ckpt = mt.donor_checkpoint_path(donor, "RL_PPO_AGENT")
            self.assertIsNotNone(ckpt)
            self.assertTrue(ckpt.endswith("policy.pt"))
            scaler = mt.donor_scaler(donor)
            self.assertEqual(scaler["feat_std"], [1.0] * 5)
            # GBM has no weight checkpoint by design.
            self.assertIsNone(mt.donor_checkpoint_path(donor, "ML_SIGNAL_BOOST"))


class TestCompatibility(_TransferTestCase):
    def _meta(self, **over):
        base = {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "model_type": "rl_ppo",
            "feature_schema_version": SIGNAL_FEATURE_VERSION,
            "obs_dim": OBS_DIM,
            "n_actions": N_ACTIONS,
            "metrics": {"mean_return_pct": 0.5},
        }
        base.update(over)
        return base

    def test_ok(self):
        self.assertEqual(
            mt.check_compatibility(self._meta(), "RL_PPO_AGENT", "1m"), [],
        )

    def test_schema_mismatch_rejected(self):
        errs = mt.check_compatibility(
            self._meta(feature_schema_version=SIGNAL_FEATURE_VERSION + 1),
            "RL_PPO_AGENT", "1m",
        )
        self.assertTrue(any("schema" in e for e in errs))

    def test_obs_dim_mismatch_rejected(self):
        errs = mt.check_compatibility(
            self._meta(obs_dim=OBS_DIM + 7), "RL_PPO_AGENT", "1m",
        )
        self.assertTrue(any("obs_dim" in e for e in errs))

    def test_n_actions_mismatch_rejected(self):
        errs = mt.check_compatibility(
            self._meta(n_actions=N_ACTIONS + 1), "RL_PPO_AGENT", "1m",
        )
        self.assertTrue(any("n_actions" in e for e in errs))

    def test_timeframe_mismatch_rejected(self):
        errs = mt.check_compatibility(
            self._meta(timeframe="15m"), "RL_PPO_AGENT", "1m",
        )
        self.assertTrue(any("timeframe" in e for e in errs))

    def test_strategy_mismatch_rejected(self):
        errs = mt.check_compatibility(
            self._meta(model_type="lstm_direction"), "RL_PPO_AGENT", "1m",
        )
        self.assertTrue(any("strategy" in e for e in errs))

    def test_missing_metrics_rejected_for_weight_strategies(self):
        meta = self._meta()
        meta.pop("metrics")
        errs = mt.check_compatibility(meta, "RL_PPO_AGENT", "1m")
        self.assertTrue(any("metrics" in e for e in errs))
        # Recipe-only strategy (GBM) does not require metrics.
        self.assertNotIn(
            "donor has no training metrics (partial or failed run)",
            mt.check_compatibility(meta, "ML_SIGNAL_BOOST", "1m"),
        )


class TestLineage(_TransferTestCase):
    def test_build_lineage_shape(self):
        with _PatchDirs():
            _write_donor("rl_ppo_models", "BTCUSDT")
            donor = mt.resolve_donor("RL_PPO_AGENT", "BTCUSDT", "1m")
            lin = mt.build_lineage(
                donor,
                method=mt.METHOD_WEIGHT_WARM_START,
                scaler_strategy="recompute",
                finetune_budget={"total_timesteps": 50000, "learning_rate": 9e-5},
            )
            self.assertEqual(lin["donor_symbol"], "BTCUSDT")
            self.assertEqual(lin["donor_version_id"], "20260810T120000Z")
            self.assertEqual(lin["donor_trained_at"], "2026-08-10T12:00:00Z")
            self.assertEqual(lin["method"], "weight_warm_start")
            self.assertEqual(lin["scaler_strategy"], "recompute")
            self.assertEqual(lin["finetune_budget"]["total_timesteps"], 50000)


class TestListDonors(_TransferTestCase):
    def test_lists_other_symbols_only(self):
        with _PatchDirs():
            _write_donor("rl_ppo_models", "BTCUSDT")
            _write_donor("rl_ppo_models", "ETHUSDT")
            _write_donor("rl_ppo_models", "ADAUSD")
            donors = mt.list_donors("RL_PPO_AGENT", "ADAUSD", "1m")
            symbols = {d["symbol"] for d in donors}
            self.assertEqual(symbols, {"BTCUSDT", "ETHUSDT"})
            self.assertTrue(all(d["has_checkpoint"] for d in donors))
            self.assertEqual(donors[0]["mean_return_pct"], 1.23)

    def test_requires_checkpoint_for_weight_strategies(self):
        with _PatchDirs():
            _write_donor("rl_ppo_models", "BTCUSDT", with_checkpoint=False)
            donors = mt.list_donors("RL_PPO_AGENT", "ADAUSD", "1m")
            self.assertEqual(donors, [])

    def test_gbm_recipe_donor_needs_no_checkpoint(self):
        with _PatchDirs():
            _write_donor(
                "ml_signal_models", "BTCUSDT",
                model_type="ml_signal_boost", with_checkpoint=False,
            )
            donors = mt.list_donors("ML_SIGNAL_BOOST", "ADAUSD", "1m")
            self.assertEqual(len(donors), 1)
            self.assertEqual(donors[0]["symbol"], "BTCUSDT")

    def test_timeframe_folders_filtered(self):
        with _PatchDirs():
            _write_donor(
                "rl_ppo_models", "BTCUSDT",
                timeframe_folder="15M", timeframe_meta="15m",
            )
            # 15m donor must not appear for a 1m target…
            self.assertEqual(mt.list_donors("RL_PPO_AGENT", "ADAUSD", "1m"), [])
            # …but does for a 15m target.
            donors = mt.list_donors("RL_PPO_AGENT", "ADAUSD", "15m")
            self.assertEqual(len(donors), 1)

    def test_incompatible_donor_excluded(self):
        with _PatchDirs():
            _write_donor(
                "rl_ppo_models", "BTCUSDT",
                schema_version=SIGNAL_FEATURE_VERSION + 1,
            )
            self.assertEqual(mt.list_donors("RL_PPO_AGENT", "ADAUSD", "1m"), [])

    def test_unknown_strategy_returns_empty(self):
        with _PatchDirs():
            self.assertEqual(mt.list_donors("NOPE", "ADAUSD", "1m"), [])


class TestLoadDonorWeights(_TransferTestCase):
    def test_disabled_without_symbol(self):
        self.assertIsNone(mt.load_donor_weights(object(), "RL_PPO_AGENT", None, "1m"))
        self.assertIsNone(mt.load_donor_weights(object(), "RL_PPO_AGENT", {}, "1m"))

    def test_freeze_trunk_marks_head_trainable(self):
        try:
            import torch
            import torch.nn as nn
        except ImportError:
            self.skipTest("torch not installed")

        class TinyNet(nn.Module):
            def __init__(self):
                super().__init__()
                self.trunk = nn.Linear(4, 4)
                self.fc = nn.Linear(4, 2)

        with _PatchDirs():
            root = _write_donor("lstm_signal_models", "BTCUSDT",
                                model_type="lstm_direction",
                                checkpoint_name="lstm_direction.pt")
            model = TinyNet()
            torch.save(model.state_dict(), os.path.join(root, "lstm_direction.pt"))

            target = TinyNet()
            res = mt.load_donor_weights(
                target, "LSTM_DIRECTION",
                {"symbol": "BTCUSDT", "freeze_trunk": True}, "1m",
            )
            self.assertIsNotNone(res)
            self.assertTrue(res["freeze_trunk"])
            self.assertEqual(res["lineage"]["donor_symbol"], "BTCUSDT")
            # Trunk frozen, head trainable.
            self.assertFalse(target.trunk.weight.requires_grad)
            self.assertTrue(target.fc.weight.requires_grad)
            # Weights actually transferred.
            for p_d, p_t in zip(model.parameters(), target.parameters()):
                self.assertTrue(torch.equal(p_d, p_t))

    def test_bad_checkpoint_falls_back_to_scratch(self):
        try:
            import torch.nn as nn
        except ImportError:
            self.skipTest("torch not installed")

        class TinyNet(nn.Module):
            def __init__(self):
                super().__init__()
                self.fc = nn.Linear(4, 2)

        with _PatchDirs():
            # Fake checkpoint bytes that are not a valid torch archive.
            _write_donor("lstm_signal_models", "BTCUSDT",
                         model_type="lstm_direction",
                         checkpoint_name="lstm_direction.pt")
            res = mt.load_donor_weights(
                TinyNet(), "LSTM_DIRECTION", {"symbol": "BTCUSDT"}, "1m",
            )
            self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
