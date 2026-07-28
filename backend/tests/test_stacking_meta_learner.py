"""Tests for Phase 2.6 — stacking meta-learner."""

from __future__ import annotations

import json

import numpy as np
import pytest

from app.services.bots import stacking_meta_learner as sm


# --- Inverse-MSE weighting -----------------------------------------------


def test_inverse_mse_uniform_when_no_data():
    model = sm.fit_inverse_mse(np.zeros((0, 3)), np.zeros(0), ("ta", "ml", "rl"))
    assert model.mode == "inverse_mse"
    assert len(model.weights) == 3
    assert all(w == pytest.approx(1.0 / 3) for w in model.weights)


def test_inverse_mse_weights_better_learner_higher():
    # Base 0 is perfect (mse=0 → infinite weight, clamped), base 1 is random
    preds = np.array([[0.9, 0.5], [0.1, 0.5], [0.9, 0.5], [0.1, 0.5]])
    labels = np.array([1, 0, 1, 0])
    model = sm.fit_inverse_mse(preds, labels, ("good", "bad"))
    # good learner should get more weight than bad
    assert model.weights[0] > model.weights[1]


def test_inverse_mse_weights_sum_to_one():
    preds = np.array([[0.6, 0.4], [0.7, 0.3], [0.5, 0.5]])
    labels = np.array([1, 0, 1])
    model = sm.fit_inverse_mse(preds, labels, ("a", "b"))
    assert sum(model.weights) == pytest.approx(1.0, abs=1e-6)


# --- Gating network ------------------------------------------------------


def test_gating_falls_back_to_inverse_mse_when_too_few():
    preds = np.array([[0.6, 0.4]] * 10)
    labels = np.array([1, 0] * 5)
    model = sm.fit_gating(preds, labels, ("a", "b"))
    assert model.mode == "inverse_mse"  # below MIN_OOS_SAMPLES


def test_gating_fits_when_enough_data():
    rng = np.random.RandomState(42)
    n = 100
    # Base 0 is informative, base 1 is noise
    labels = rng.randint(0, 2, n)
    preds = np.column_stack([
        labels * 0.8 + (1 - labels) * 0.2 + rng.randn(n) * 0.05,  # informative
        rng.rand(n),  # noise
    ])
    model = sm.fit_gating(preds, labels, ("good", "noise"))
    assert model.mode == "gating"
    assert model.gating_coeffs is not None
    assert len(model.gating_coeffs) == 3  # 2 bases + bias


def test_fit_stacking_dispatches_to_gating_when_enough_data():
    rng = np.random.RandomState(0)
    n = 80
    labels = rng.randint(0, 2, n)
    preds = np.column_stack([labels * 0.7 + 0.15, rng.rand(n)])
    model = sm.fit_stacking(preds, labels, ("a", "b"), prefer_gating=True)
    assert model.mode == "gating"


def test_fit_stacking_dispatches_to_inverse_mse_when_prefer_false():
    rng = np.random.RandomState(0)
    n = 80
    labels = rng.randint(0, 2, n)
    preds = np.column_stack([labels * 0.7 + 0.15, rng.rand(n)])
    model = sm.fit_stacking(preds, labels, ("a", "b"), prefer_gating=False)
    assert model.mode == "inverse_mse"


# --- Prediction ---------------------------------------------------------


def test_predict_stacked_inverse_mse_weighted_average():
    model = sm.StackingModel(
        mode="inverse_mse",
        weights=(0.5, 0.5),
        base_names=("a", "b"),
    )
    p = sm.predict_stacked(np.array([0.8, 0.6]), model)
    assert p == pytest.approx(0.7, abs=1e-6)


def test_predict_stacked_gating_uses_coeffs():
    model = sm.StackingModel(
        mode="gating",
        weights=(0.5, 0.5),
        base_names=("a", "b"),
        gating_coeffs=(2.0, 0.0, -1.0),  # bias toward base a
    )
    # base a=0.9, base b=0.1 → 2*0.9 + 0*0.1 - 1 = 0.8 → sigmoid(0.8) ≈ 0.69
    p = sm.predict_stacked(np.array([0.9, 0.1]), model)
    assert 0.6 < p < 0.8


def test_predict_stacked_no_model_returns_half():
    assert sm.predict_stacked(np.array([0.5, 0.5]), None) == 0.5


def test_predict_stacked_mismatched_length_returns_half():
    model = sm.StackingModel(
        mode="inverse_mse", weights=(0.5, 0.5), base_names=("a", "b"),
    )
    assert sm.predict_stacked(np.array([0.5]), model) == 0.5


# --- Stacked signal ------------------------------------------------------


def test_stacked_signal_buy_when_above_threshold():
    model = sm.StackingModel(
        mode="inverse_mse", weights=(1.0,), base_names=("a",),
    )
    sig, conf = sm.stacked_signal(np.array([0.8]), model, threshold=0.55)
    assert sig == "BUY"
    assert conf > 0.55


def test_stacked_signal_sell_when_below_threshold():
    model = sm.StackingModel(
        mode="inverse_mse", weights=(1.0,), base_names=("a",),
    )
    sig, conf = sm.stacked_signal(np.array([0.2]), model, threshold=0.55)
    assert sig == "SELL"


def test_stacked_signal_none_when_ambiguous():
    model = sm.StackingModel(
        mode="inverse_mse", weights=(1.0,), base_names=("a",),
    )
    sig, _ = sm.stacked_signal(np.array([0.5]), model, threshold=0.55)
    assert sig == "NONE"


# --- Persistence --------------------------------------------------------


def test_stacking_model_roundtrip(tmp_path):
    model = sm.StackingModel(
        mode="gating",
        weights=(0.6, 0.4),
        base_names=("ta", "ml"),
        n_oos=100,
        gating_coeffs=(1.5, 0.5, -0.3),
    )
    path = str(tmp_path / "m.json")
    sm.save_stacking_model("bot-x", model, path=path)
    loaded = sm.load_stacking_model("bot-x", path=path)
    assert loaded is not None
    assert loaded.mode == "gating"
    assert loaded.weights == (0.6, 0.4)
    assert loaded.gating_coeffs == (1.5, 0.5, -0.3)


def test_load_missing_returns_none(tmp_path):
    assert sm.load_stacking_model("nope", path=str(tmp_path / "nope.json")) is None


def test_invalidate_stacking_cache():
    sm._bot_models["tmp"] = sm.StackingModel(
        mode="inverse_mse", weights=(1.0,), base_names=("a",),
    )
    sm.invalidate_stacking_cache("tmp")
    assert "tmp" not in sm._bot_models
