"""MEMORY_CENTRIC_REVIEW #28 — per-bot signal-gate caches (conformal, HMM
regime, calibration fitter, stacking meta-learner) are bounded by the shared
LRU+TTL store: eviction drops the hot copy while disk stays the source of
truth, and explicit invalidation also drops LRU tracking."""

from __future__ import annotations

import numpy as np

from app.services.bots import (
    calibration_fitter,
    conformal_gate,
    hmm_regime,
    stacking_meta_learner,
)
from app.services.bots.model_store_lru import bind_dict_cache


def _small_lru(monkeypatch, module, store):
    lru = bind_dict_cache(store, max_entries=2, ttl_sec=None)
    monkeypatch.setattr(module, "_cache_lru", lru)
    return lru


def _conformal_cal() -> conformal_gate.ConformalCalibration:
    return conformal_gate.ConformalCalibration(
        q_hat=0.8, threshold=0.2, n=50, alpha=0.1, scores=(0.7, 0.8),
    )


def _hmm_model() -> hmm_regime.RegimeModel:
    return hmm_regime.RegimeModel(
        means=((0.001, 0.01),),
        covariances=(np.eye(2),),
        weights=(1.0,),
        state_labels=("bull_quiet",),
        vol_threshold=0.01,
        n_states=1,
    )


def _stacking_model() -> stacking_meta_learner.StackingModel:
    return stacking_meta_learner.StackingModel(
        mode="inverse_mse", weights=(1.0,), base_names=("lstm",), n_oos=10,
    )


def test_conformal_cache_evicts_oldest_and_reloads_from_disk(monkeypatch, tmp_path):
    lru = _small_lru(monkeypatch, conformal_gate, conformal_gate._bot_cal)
    paths = {}
    for bot_id in ("bot-a", "bot-b", "bot-c"):
        p = tmp_path / f"{bot_id}.json"
        paths[bot_id] = p
        conformal_gate.save_conformal(bot_id, _conformal_cal(), path=str(p))

    # max_entries=2 → the oldest (bot-a) was evicted from the hot cache.
    assert "bot-a" not in conformal_gate._bot_cal
    assert "bot-b" in conformal_gate._bot_cal
    assert "bot-c" in conformal_gate._bot_cal
    assert len(lru) == 2

    # Explicit invalidation drops both the payload and the LRU tracking.
    conformal_gate.invalidate_conformal_cache("bot-b")
    assert "bot-b" not in conformal_gate._bot_cal
    assert len(lru) == 1

    # Disk fallback: evicted bot still loads, and the load re-enters the cache.
    cal = conformal_gate.load_conformal("bot-a", path=str(paths["bot-a"]))
    assert cal is not None
    assert "bot-a" in conformal_gate._bot_cal
    assert len(lru) == 2


def test_hmm_regime_cache_evicts_oldest_and_reloads_from_disk(monkeypatch, tmp_path):
    lru = _small_lru(monkeypatch, hmm_regime, hmm_regime._bot_models)
    paths = {}
    for bot_id in ("bot-a", "bot-b", "bot-c"):
        p = tmp_path / f"{bot_id}.json"
        paths[bot_id] = p
        hmm_regime.save_regime_model(bot_id, _hmm_model(), path=str(p))

    assert "bot-a" not in hmm_regime._bot_models
    assert "bot-c" in hmm_regime._bot_models
    assert len(lru) == 2

    hmm_regime.invalidate_regime_cache("bot-b")
    assert "bot-b" not in hmm_regime._bot_models
    assert len(lru) == 1

    model = hmm_regime.load_regime_model("bot-a", path=str(paths["bot-a"]))
    assert model is not None
    assert "bot-a" in hmm_regime._bot_models
    assert len(lru) == 2

    hmm_regime.invalidate_regime_cache(None)
    assert not hmm_regime._bot_models
    assert len(lru) == 0


def test_stacking_cache_evicts_oldest_and_reloads_from_disk(monkeypatch, tmp_path):
    lru = _small_lru(monkeypatch, stacking_meta_learner, stacking_meta_learner._bot_models)
    paths = {}
    for bot_id in ("bot-a", "bot-b", "bot-c"):
        p = tmp_path / f"{bot_id}.json"
        paths[bot_id] = p
        stacking_meta_learner.save_stacking_model(bot_id, _stacking_model(), path=str(p))

    assert "bot-a" not in stacking_meta_learner._bot_models
    assert "bot-c" in stacking_meta_learner._bot_models
    assert len(lru) == 2

    stacking_meta_learner.invalidate_stacking_cache("bot-b")
    assert "bot-b" not in stacking_meta_learner._bot_models
    assert len(lru) == 1

    model = stacking_meta_learner.load_stacking_model("bot-a", path=str(paths["bot-a"]))
    assert model is not None
    assert "bot-a" in stacking_meta_learner._bot_models
    assert len(lru) == 2


def test_calibration_fitter_cache_evicts_oldest_and_reloads_from_disk(monkeypatch, tmp_path):
    lru = _small_lru(monkeypatch, calibration_fitter, calibration_fitter._bot_cache)
    paths = {}
    for bot_id in ("bot-a", "bot-b", "bot-c"):
        p = tmp_path / f"{bot_id}.json"
        paths[bot_id] = p
        calibration_fitter.save_calibration(
            str(p),
            calibration_fitter.CalibrationBlob(temperature=1.0, kelly_fraction=0.5),
        )
        calibration_fitter.get_bot_calibration(bot_id, path=str(p))

    # Path-keyed entries: oldest path evicted.
    assert str(paths["bot-a"]) not in calibration_fitter._bot_cache
    assert str(paths["bot-c"]) in calibration_fitter._bot_cache
    assert len(lru) == 2

    blob = calibration_fitter.get_bot_calibration("bot-a", path=str(paths["bot-a"]))
    assert blob.temperature == 1.0
    assert str(paths["bot-a"]) in calibration_fitter._bot_cache

    calibration_fitter.invalidate_bot_cache(None)
    assert not calibration_fitter._bot_cache
    assert len(lru) == 0


def test_default_paths_resolve_via_config_data_dir():
    """Production path: _default_path() must resolve (app.config.DATA_DIR)."""
    assert conformal_gate._default_path("bot-x").replace("\\", "/").endswith(
        "data/conformal/bot-x.json"
    )
    assert hmm_regime._default_path("bot-x").replace("\\", "/").endswith(
        "data/hmm_regime/bot-x.json"
    )
    assert calibration_fitter._default_path("bot-x").replace("\\", "/").endswith(
        "data/calibration/bot-x.json"
    )
    assert stacking_meta_learner._default_path("bot-x").replace("\\", "/").endswith(
        "data/stacking/bot-x.json"
    )
