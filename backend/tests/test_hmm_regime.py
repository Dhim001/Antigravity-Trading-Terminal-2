"""Tests for Phase 2.5 — HMM regime gate (soft posterior-weighted)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from app.services.bots import hmm_regime as hr


# --- Feature extraction ---------------------------------------------------


def test_features_from_candles_basic():
    candles = [
        {"close": 100 + i * 0.5, "time": i} for i in range(50)
    ]
    feats = hr._features_from_candles(candles, vol_lookback=10)
    assert feats.shape == (49, 2)
    assert np.all(np.isfinite(feats))


def test_features_from_candles_too_short():
    feats = hr._features_from_candles([{"close": 100}], vol_lookback=10)
    assert feats.shape == (0, 2)


# --- State labelling ------------------------------------------------------


def test_label_state_bull_quiet():
    assert hr._label_state(0.001, 0.01, 0.02) == "bull_quiet"


def test_label_state_bear_volatile():
    assert hr._label_state(-0.002, 0.03, 0.02) == "bear_volatile"


def test_label_state_bull_volatile():
    assert hr._label_state(0.001, 0.025, 0.02) == "bull_volatile"


def test_label_state_bear_quiet():
    assert hr._label_state(-0.001, 0.01, 0.02) == "bear_quiet"


# --- Regime model fit ----------------------------------------------------


def _make_trending_candles(n=300, drift=0.001):
    """Candles with a consistent drift → bull regime."""
    candles = []
    price = 100.0
    for i in range(n):
        ret = drift + np.random.RandomState(i).randn() * 0.002
        price *= (1 + ret)
        candles.append({
            "time": i, "open": price * 0.999, "high": price * 1.001,
            "low": price * 0.999, "close": price, "volume": 1000,
        })
    return candles


def test_fit_regime_model_returns_model():
    candles = _make_trending_candles(300, drift=0.001)
    model = hr.fit_regime_model(candles, n_states=3)
    assert model is not None
    assert len(model.state_labels) == 3
    assert all(s in ("bull_quiet", "bull_volatile", "bear_quiet", "bear_volatile")
               for s in model.state_labels)


def test_fit_regime_model_none_when_too_few():
    candles = _make_trending_candles(50)
    model = hr.fit_regime_model(candles)
    assert model is None  # below MIN_FIT_SAMPLES


def test_fit_regime_model_none_on_empty():
    assert hr.fit_regime_model([]) is None


# --- Posterior prediction -------------------------------------------------


def test_predict_posteriors_sums_to_one():
    model = hr.RegimeModel(
        means=((0.001, 0.01), (-0.001, 0.02)),
        covariances=(np.eye(2) * 0.0001, np.eye(2) * 0.0001),
        weights=(0.5, 0.5),
        state_labels=("bull_quiet", "bear_volatile"),
        vol_threshold=0.015,
    )
    feats = np.array([[0.001, 0.01]])
    probs = hr.predict_regime_posteriors(model, feats)
    assert probs.sum() == pytest.approx(1.0, abs=1e-6)
    assert len(probs) == 2


def test_predict_posteriors_empty_model():
    model = hr.RegimeModel(
        means=(), covariances=(), weights=(),
        state_labels=(), vol_threshold=0.0,
    )
    probs = hr.predict_regime_posteriors(model, np.array([[0.0, 0.0]]))
    assert len(probs) == 0


# --- Signal scale --------------------------------------------------------


def test_regime_scale_bull_boosts_buy():
    model = hr.RegimeModel(
        means=((0.001, 0.01),),
        covariances=(np.eye(2) * 0.0001,),
        weights=(1.0,),
        state_labels=("bull_quiet",),
        vol_threshold=0.02,
    )
    posteriors = np.array([1.0])
    scale = hr.regime_signal_scale(posteriors, model, "BUY")
    assert scale > 1.0  # bull boosts buy


def test_regime_scale_bear_dampens_buy():
    model = hr.RegimeModel(
        means=((-0.001, 0.01),),
        covariances=(np.eye(2) * 0.0001,),
        weights=(1.0,),
        state_labels=("bear_quiet",),
        vol_threshold=0.02,
    )
    posteriors = np.array([1.0])
    scale = hr.regime_signal_scale(posteriors, model, "BUY")
    assert scale < 1.0  # bear dampens buy


def test_regime_scale_volatile_dampens():
    model = hr.RegimeModel(
        means=((0.001, 0.03),),
        covariances=(np.eye(2) * 0.0001,),
        weights=(1.0,),
        state_labels=("bull_volatile",),
        vol_threshold=0.02,
    )
    posteriors = np.array([1.0])
    scale = hr.regime_signal_scale(posteriors, model, "BUY")
    # Bull boosts but volatile dampens → net less than pure bull
    bull_quiet_model = hr.RegimeModel(
        means=((0.001, 0.01),),
        covariances=(np.eye(2) * 0.0001,),
        weights=(1.0,),
        state_labels=("bull_quiet",),
        vol_threshold=0.02,
    )
    scale_quiet = hr.regime_signal_scale(np.array([1.0]), bull_quiet_model, "BUY")
    assert scale < scale_quiet


def test_regime_scale_clamped():
    model = hr.RegimeModel(
        means=((0.01, 0.01),),  # extreme bull
        covariances=(np.eye(2) * 0.0001,),
        weights=(1.0,),
        state_labels=("bull_quiet",),
        vol_threshold=0.02,
    )
    posteriors = np.array([1.0])
    scale = hr.regime_signal_scale(posteriors, model, "BUY")
    assert scale <= hr.MAX_REGIME_SCALE
    assert scale >= hr.MIN_REGIME_SCALE


def test_regime_scale_no_model_returns_one():
    scale = hr.regime_signal_scale(np.array([]), None, "BUY")
    assert scale == 1.0


# --- Persistence --------------------------------------------------------


def test_regime_model_roundtrip(tmp_path):
    model = hr.RegimeModel(
        means=((0.001, 0.01), (-0.001, 0.02)),
        covariances=(np.eye(2) * 0.0001, np.eye(2) * 0.0002),
        weights=(0.6, 0.4),
        state_labels=("bull_quiet", "bear_volatile"),
        vol_threshold=0.015,
    )
    path = str(tmp_path / "m.json")
    hr.save_regime_model("bot-x", model, path=path)
    loaded = hr.load_regime_model("bot-x", path=path)
    assert loaded is not None
    assert loaded.state_labels == ("bull_quiet", "bear_volatile")
    assert loaded.weights == (0.6, 0.4)


def test_load_missing_returns_none(tmp_path):
    assert hr.load_regime_model("nope", path=str(tmp_path / "nope.json")) is None


def test_invalidate_regime_cache():
    hr._bot_models["tmp"] = hr.RegimeModel(
        means=(), covariances=(), weights=(),
        state_labels=(), vol_threshold=0.0,
    )
    hr.invalidate_regime_cache("tmp")
    assert "tmp" not in hr._bot_models


# --- Live gate ----------------------------------------------------------


def test_gate_noop_when_disabled():
    result = {"signal": "BUY", "confidence": 0.7}
    out = hr.apply_hmm_regime_gate(result, {"hmm_regime_gate_enabled": False})
    assert out is result


def test_gate_noop_for_none_signal():
    out = hr.apply_hmm_regime_gate(
        {"signal": "NONE"},
        {"hmm_regime_gate_enabled": True, "_bot_id": "b1"},
    )
    assert out["signal"] == "NONE"


def test_gate_noop_when_no_model(monkeypatch):
    monkeypatch.setattr(hr, "load_regime_model", lambda bid, path=None: None)
    result = {"signal": "BUY", "confidence": 0.7}
    out = hr.apply_hmm_regime_gate(
        result,
        {"hmm_regime_gate_enabled": True, "_bot_id": "b1"},
        recent_features=np.array([[0.001, 0.01]]),
    )
    assert out is result  # no model → identity


def test_gate_scales_confidence(monkeypatch):
    model = hr.RegimeModel(
        means=((0.001, 0.01),),
        covariances=(np.eye(2) * 0.0001,),
        weights=(1.0,),
        state_labels=("bull_quiet",),
        vol_threshold=0.02,
    )
    monkeypatch.setattr(hr, "load_regime_model", lambda bid, path=None: model)
    result = {"signal": "BUY", "confidence": 0.7}
    out = hr.apply_hmm_regime_gate(
        result,
        {"hmm_regime_gate_enabled": True, "_bot_id": "b1"},
        recent_features=np.array([[0.001, 0.01]]),
    )
    assert out["confidence"] > 0.7  # bull boosts buy
    assert "regime_scale" in out
    assert out["regime_scale"] > 1.0


def test_gate_clamps_confidence(monkeypatch):
    model = hr.RegimeModel(
        means=((0.01, 0.01),),
        covariances=(np.eye(2) * 0.0001,),
        weights=(1.0,),
        state_labels=("bull_quiet",),
        vol_threshold=0.02,
    )
    monkeypatch.setattr(hr, "load_regime_model", lambda bid, path=None: model)
    result = {"signal": "BUY", "confidence": 0.95}
    out = hr.apply_hmm_regime_gate(
        result,
        {"hmm_regime_gate_enabled": True, "_bot_id": "b1"},
        recent_features=np.array([[0.001, 0.01]]),
    )
    assert out["confidence"] <= 1.0
