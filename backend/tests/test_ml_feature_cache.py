"""Tests for WfFeatureCache + parallel WF fold parity (Optimizer Opt #2/#4)."""

from __future__ import annotations

import numpy as np
import pytest


def _synth_candles(n: int = 400) -> list[dict]:
    rows = []
    price = 100.0
    for i in range(n):
        price += (i % 5 - 2) * 0.05
        rows.append({
            "time": i,
            "open": price,
            "high": price + 0.5,
            "low": price - 0.5,
            "close": price,
            "volume": 100 + i,
            "ATR_14": 1.0,
        })
    return rows


def test_wf_feature_cache_gather_matches_slice():
    from app.services.bots.ml_feature_cache import WfFeatureCache
    from app.services.bots.ml_feature_engineering import precompute_signal_feature_matrix
    from app.services.bots.ml_triple_barrier import label_triple_barrier

    candles = _synth_candles(120)
    cfg = {"triple_barrier_atr_mult": 2.0, "triple_barrier_max_bars": 10}
    cache = WfFeatureCache(candles, cfg)
    full_feat = precompute_signal_feature_matrix(candles)
    full_labels = label_triple_barrier(
        candles, atr_mult_upper=2.0, atr_mult_lower=2.0, max_holding_bars=10,
    )
    assert cache.feature_matrix.shape == full_feat.shape
    np.testing.assert_allclose(cache.feature_matrix, full_feat, rtol=1e-5, atol=1e-5)
    assert len(cache.labels) == len(full_labels)
    assert all("uniqueness" in row for row in cache.labels)
    assert all("is_event" in row for row in cache.labels)

    indices = list(range(10, 80))
    gathered = cache.gather(indices)
    np.testing.assert_allclose(
        gathered["features"], full_feat[indices], rtol=1e-5, atol=1e-5,
    )
    assert len(gathered["labels"]) == len(indices)


def test_parallel_wf_folds_match_sequential(monkeypatch):
    """Parallel ThreadPool fold results match sequential (numeric parity)."""
    from app.services.bots import ml_walk_forward_validator as wf

    candles = _synth_candles(900)

    def fake_trainer(symbol, train_candles, config=None):
        return {
            "ok": True,
            "metrics": {"val_accuracy": 0.5 + (len(train_candles) % 7) * 0.001},
            "_wf_bundle": {
                "strategy": "ML_SIGNAL_BOOST",
                "model": object(),
                "metadata": {},
            },
        }

    def fake_oos(strategy, test_candles, config, train_result=None, **_kwargs):
        h = sum(int(c.get("time") or 0) for c in test_candles[:5])
        acc = 0.5 + (h % 17) * 0.001
        return {
            "accuracy": round(acc, 4),
            "n_signals": len(test_candles) // 10,
            "n_correct": 1,
            "buy_count": 1,
            "sell_count": 1,
            "none_count": 0,
        }

    monkeypatch.setattr(wf, "get_trainer", lambda s: fake_trainer)
    monkeypatch.setattr(wf, "evaluate_oos_accuracy", fake_oos)
    # Avoid heavy feature matrix in unit test
    monkeypatch.setattr(
        "app.services.bots.ml_feature_cache.WfFeatureCache",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("skip cache")),
    )

    cfg = {"_wf_mode": True, "skip_refit": True, "wf_capacity_parity": False}

    monkeypatch.setattr(wf, "_resolve_wf_fold_workers", lambda strategy, n, cfg=None: 1)
    seq = wf.walk_forward_ml_train(
        "ML_SIGNAL_BOOST", "BTCUSDT", candles, n_folds=3, config=cfg,
    )
    monkeypatch.setattr(wf, "_resolve_wf_fold_workers", lambda strategy, n, cfg=None: 3)
    par = wf.walk_forward_ml_train(
        "ML_SIGNAL_BOOST", "BTCUSDT", candles, n_folds=3, config=cfg,
    )

    assert seq["ok"] is True
    assert par["ok"] is True
    assert len(seq["folds"]) == len(par["folds"])
    for a, b in zip(seq["folds"], par["folds"]):
        assert a["fold"] == b["fold"]
        assert a.get("ok") == b.get("ok")
        if a.get("ok"):
            assert a["accuracy"] == pytest.approx(b["accuracy"], abs=1e-6)
            assert a["train_bars"] == b["train_bars"]
            assert a["test_bars"] == b["test_bars"]
    assert seq["aggregate"]["mean_oos_accuracy"] == pytest.approx(
        par["aggregate"]["mean_oos_accuracy"], abs=1e-6,
    )


def test_resolve_ml_train_max_workers_auto(monkeypatch):
    from app.services.bots import ml_train_executor as ex

    monkeypatch.setattr("app.config.ML_TRAIN_MAX_WORKERS_RAW", "3")
    assert ex.resolve_ml_train_max_workers() == 3

    monkeypatch.setattr("app.config.ML_TRAIN_MAX_WORKERS_RAW", "auto")
    monkeypatch.setattr("app.config.ML_TRAIN_RSS_LIMIT_MB", 4096)
    # Without RSS≥6144, auto stays at 1 regardless of CUDA.
    assert ex.resolve_ml_train_max_workers() == 1

    monkeypatch.setattr("app.config.ML_TRAIN_RSS_LIMIT_MB", 6144)
    try:
        import torch

        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        assert ex.resolve_ml_train_max_workers() == 1
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        assert ex.resolve_ml_train_max_workers() == 2
    except Exception:
        pytest.skip("torch not installed")


def test_resolve_precomputed_rejects_length_mismatch():
    from app.services.bots.ml_feature_cache import (
        resolve_precomputed_features,
        resolve_precomputed_labels,
    )

    candles = _synth_candles(10)
    assert resolve_precomputed_features(
        candles, {"_precomputed_features": np.zeros((5, 3), dtype=np.float32)},
    ) is None
    assert resolve_precomputed_labels(
        candles, {"_precomputed_labels": [{"label": 0}] * 5},
    ) is None
    ok_feat = resolve_precomputed_features(
        candles, {"_precomputed_features": np.zeros((10, 3), dtype=np.float32)},
    )
    assert ok_feat is not None and ok_feat.shape == (10, 3)


def test_prepare_wf_jobs_keeps_oos_cache_off_train_cfg():
    """OOS features must not ride on fold_cfg during training (leakage hygiene)."""
    from app.services.bots.ml_feature_cache import WfFeatureCache
    from app.services.bots.ml_walk_forward_validator import _prepare_wf_fold_jobs

    candles = _synth_candles(300)
    folds = [
        {
            "fold": 1,
            "train_start": 0,
            "train_end": 180,
            "test_start": 180,
            "test_end": 220,
            "purge_bars": 10,
            "embargo_bars": 0,
        },
        {
            "fold": 2,
            "train_start": 40,
            "train_end": 220,
            "test_start": 220,
            "test_end": 260,
            "purge_bars": 10,
            "embargo_bars": 5,
        },
    ]
    cache = WfFeatureCache(candles, {"triple_barrier_max_bars": 10})
    jobs = _prepare_wf_fold_jobs(folds, candles, "BTCUSDT", {}, cache)
    assert len(jobs) == 2
    for job in jobs:
        assert "_oos_precomputed_features" not in job["fold_cfg"]
        assert "_oos_precomputed_labels" not in job["fold_cfg"]
        assert "_precomputed_features" in job["fold_cfg"]
        assert job["_oos_precomputed_features"] is not None
        assert len(job["fold_cfg"]["_precomputed_features"]) == len(job["train_candles"])
        assert len(job["_oos_precomputed_features"]) == len(job["test_candles"])


def test_wf_fold_workers_respect_disable_and_defaults(monkeypatch):
    import os

    from app.services.bots import ml_walk_forward_validator as wf

    monkeypatch.setattr("app.config.ML_WF_FOLD_WORKERS", "auto")
    cpu = os.cpu_count() or 4
    assert wf._resolve_wf_fold_workers("ML_SIGNAL_BOOST", 5) == max(
        1, min(5, 4, cpu),
    )
    assert wf._resolve_wf_fold_workers(
        "ML_SIGNAL_BOOST", 5, {"_disable_wf_fold_parallel": True},
    ) == 1
    assert wf._resolve_wf_fold_workers("LSTM_DIRECTION", 5) == 1
    monkeypatch.setattr("app.config.ML_WF_FOLD_WORKERS", "1")
    assert wf._resolve_wf_fold_workers("ML_SIGNAL_BOOST", 5) == 1


def test_parallel_wf_cancel_does_not_aggregate_success(monkeypatch):
    """Cancel mid-parallel-WF must return cancelled, not a partial OK aggregate."""
    from app.services.bots import ml_walk_forward_validator as wf

    candles = _synth_candles(900)
    state = {"n": 0}

    def fake_trainer(symbol, train_candles, config=None):
        state["n"] += 1
        # First fold completes; later folds see cancel.
        return {"ok": True, "metrics": {"val_accuracy": 0.55}, "_wf_bundle": {}}

    def fake_oos(strategy, test_candles, config, train_result=None, **_kwargs):
        return {
            "accuracy": 0.55,
            "n_signals": 10,
            "n_correct": 5,
            "buy_count": 1,
            "sell_count": 1,
            "none_count": 0,
        }

    def cancel_after_first(path):
        # After one fold finishes, pretend UI cancelled.
        return state["n"] >= 1

    monkeypatch.setattr(wf, "get_trainer", lambda s: fake_trainer)
    monkeypatch.setattr(wf, "evaluate_oos_accuracy", fake_oos)
    monkeypatch.setattr(wf, "_resolve_wf_fold_workers", lambda strategy, n, cfg=None: 3)
    monkeypatch.setattr(
        "app.services.bots.ml_feature_cache.WfFeatureCache",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("skip cache")),
    )
    monkeypatch.setattr(
        "app.services.bots.ml_job_progress.ml_cancel_requested",
        cancel_after_first,
    )
    monkeypatch.setattr(
        "app.services.bots.ml_job_progress.write_ml_progress",
        lambda *a, **k: None,
    )

    out = wf.walk_forward_ml_train(
        "ML_SIGNAL_BOOST",
        "BTCUSDT",
        candles,
        n_folds=3,
        config={"_wf_mode": True, "wf_capacity_parity": False},
    )
    assert out["ok"] is False
    assert out.get("cancelled") is True
    assert "aggregate" not in out or out.get("aggregate") is None
