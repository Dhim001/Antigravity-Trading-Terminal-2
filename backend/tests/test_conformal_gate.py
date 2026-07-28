"""Tests for conformal_gate — split-conformal prediction-set gate."""

from __future__ import annotations

import json

import pytest

from app.services.bots import conformal_gate as cg


# --- Nonconformity scores --------------------------------------------------


def test_nonconformity_score_positive_label():
    # p=0.9, true label=1 → score = 1 - 0.9 = 0.1 (low nonconformity)
    scores = cg._nonconformity_scores([0.9], [1])
    assert scores[0] == pytest.approx(0.1)


def test_nonconformity_score_negative_label():
    # p=0.9, true label=0 → score = 1 - 0.1 = 0.9 (high nonconformity)
    scores = cg._nonconformity_scores([0.9], [0])
    assert scores[0] == pytest.approx(0.9)


def test_nonconformity_score_clamps():
    scores = cg._nonconformity_scores([1.5, -0.3], [1, 0])
    # p=1.5 clamped to 1.0 → score = 0.0
    # p=-0.3 clamped to 0.0, y=0 → p_true=1.0 → score = 0.0
    assert scores[0] == pytest.approx(0.0)
    assert scores[1] == pytest.approx(0.0)


# --- Conformal quantile ---------------------------------------------------


def test_quantile_basic():
    # scores 0.1, 0.2, 0.3, 0.4, 0.5; alpha=0.2 → rank=ceil(6*0.8)=5 → 0.5
    s = [0.1, 0.2, 0.3, 0.4, 0.5]
    assert cg._conformal_quantile(s, 0.2) == pytest.approx(0.5)


def test_quantile_clamps_to_max_rank():
    # alpha tiny → rank huge → clamps to n
    s = [0.1, 0.2, 0.3]
    assert cg._conformal_quantile(s, 0.01) == pytest.approx(0.3)


def test_quantile_clamps_to_min_rank():
    # alpha huge → rank=1
    s = [0.1, 0.2, 0.3]
    assert cg._conformal_quantile(s, 0.99) == pytest.approx(0.1)


def test_quantile_empty_returns_one():
    assert cg._conformal_quantile([], 0.1) == 1.0


# --- fit_conformal --------------------------------------------------------


def test_fit_conformal_returns_none_when_too_few():
    assert cg.fit_conformal([0.5, 0.6], [1, 0], min_samples=30) is None


def test_fit_conformal_returns_none_on_mismatch():
    assert cg.fit_conformal([0.5, 0.6, 0.7], [1, 0], min_samples=2) is None


def test_fit_conformal_returns_none_on_empty():
    assert cg.fit_conformal([], [], min_samples=1) is None


def test_fit_conformal_well_calibrated_70pct_model():
    # 100 samples, p=0.7 with 70 wins.
    # Scores: 70 wins → 1-0.7=0.3; 30 losses → 1-0.3=0.7.
    # At α=0.1, rank=ceil(101*0.9)=91 → 91st sorted score = 0.7 (positions 71-100 are 0.7).
    # q_hat=0.7, threshold=1-0.7=0.3 — permissive, so most samples are "ambiguous".
    # That is the correct conservative conformal behaviour for a 70%-accuracy model.
    probs = [0.7] * 100
    labels = [1] * 70 + [0] * 30
    cal = cg.fit_conformal(probs, labels, alpha=0.1, min_samples=30)
    assert cal is not None
    assert cal.n == 100
    assert cal.q_hat == pytest.approx(0.7)
    assert cal.threshold == pytest.approx(0.3)


def test_fit_conformal_high_accuracy_model_has_high_threshold():
    # 100 samples, p=0.95 with 95 wins → scores: 95×0.05 + 5×0.95.
    # At α=0.1, rank=91 → 91st score. Positions 1-95 = 0.05, 96-100 = 0.95.
    # Position 91 = 0.05 → q_hat=0.05, threshold=0.95 — strict, as expected.
    probs = [0.95] * 100
    labels = [1] * 95 + [0] * 5
    cal = cg.fit_conformal(probs, labels, alpha=0.1, min_samples=30)
    assert cal is not None
    assert cal.threshold == pytest.approx(0.95)


# --- conformal_verdict ----------------------------------------------------


def test_verdict_no_calibration_rejects():
    v = cg.conformal_verdict(0.9, 0.1, None)
    assert not v.accept
    assert v.reason == "no_calibration"


def test_verdict_singleton_long_accepted():
    cal = cg.ConformalCalibration(q_hat=0.3, threshold=0.7, n=100, alpha=0.1)
    v = cg.conformal_verdict(0.85, 0.15, cal)
    assert v.accept
    assert v.side == "LONG"
    assert v.reason == "singleton_long"


def test_verdict_singleton_short_accepted():
    cal = cg.ConformalCalibration(q_hat=0.3, threshold=0.7, n=100, alpha=0.1)
    v = cg.conformal_verdict(0.15, 0.85, cal)
    assert v.accept
    assert v.side == "SHORT"
    assert v.reason == "singleton_short"


def test_verdict_ambiguous_rejects():
    cal = cg.ConformalCalibration(q_hat=0.6, threshold=0.4, n=100, alpha=0.1)
    # Both above threshold → ambiguous
    v = cg.conformal_verdict(0.55, 0.55, cal)
    assert not v.accept
    assert v.reason == "ambiguous"


def test_verdict_neither_rejects():
    cal = cg.ConformalCalibration(q_hat=0.2, threshold=0.8, n=100, alpha=0.1)
    # Both below threshold → neither
    v = cg.conformal_verdict(0.5, 0.5, cal)
    assert not v.accept
    assert v.reason == "neither"


def test_verdict_clamps_probabilities():
    cal = cg.ConformalCalibration(q_hat=0.3, threshold=0.7, n=100, alpha=0.1)
    v = cg.conformal_verdict(1.5, -0.5, cal)
    # p_long clamped to 1.0, p_short to 0.0 → singleton long
    assert v.accept
    assert v.side == "LONG"
    assert v.p_long == 1.0
    assert v.p_short == 0.0


# --- Persistence ---------------------------------------------------------


def test_calibration_roundtrip(tmp_path):
    cal = cg.ConformalCalibration(
        q_hat=0.3, threshold=0.7, n=100, alpha=0.1,
        scores=(0.1, 0.2, 0.3),
    )
    path = str(tmp_path / "cal.json")
    cg.save_conformal("bot-x", cal, path=path)
    loaded = cg.load_conformal("bot-x", path=path)
    assert loaded is not None
    assert loaded.q_hat == pytest.approx(0.3)
    assert loaded.threshold == pytest.approx(0.7)
    assert loaded.n == 100
    assert loaded.scores == (0.1, 0.2, 0.3)


def test_load_missing_returns_none(tmp_path):
    assert cg.load_conformal("nope", path=str(tmp_path / "nope.json")) is None


def test_load_corrupt_returns_none(tmp_path):
    path = str(tmp_path / "bad.json")
    with open(path, "w") as fh:
        fh.write("{broken")
    assert cg.load_conformal("bad", path=path) is None


def test_invalidate_conformal_cache():
    cg._bot_cal["tmp"] = cg.ConformalCalibration(q_hat=0.3, threshold=0.7, n=10, alpha=0.1)
    cg.invalidate_conformal_cache("tmp")
    assert "tmp" not in cg._bot_cal


# --- apply_conformal_gate (live path) -------------------------------------


def test_gate_noop_when_disabled():
    result = {"signal": "BUY", "confidence": 0.9}
    out = cg.apply_conformal_gate(result, {"conformal_gate_enabled": False})
    assert out is result


def test_gate_noop_for_none_signal():
    out = cg.apply_conformal_gate({"signal": "NONE"}, {"conformal_gate_enabled": True, "_bot_id": "b1"})
    assert out["signal"] == "NONE"


def test_gate_falls_back_to_min_confidence_when_no_calibration(monkeypatch):
    monkeypatch.setattr(cg, "load_conformal", lambda bid, path=None: None)
    result = {"signal": "BUY", "confidence": 0.6}
    out = cg.apply_conformal_gate(
        result,
        {"conformal_gate_enabled": True, "_bot_id": "b1", "min_confidence": 0.65},
    )
    # 0.6 < 0.65 → rejected by fallback
    assert out["signal"] == "NONE"
    assert out["reject_reason"] == "conformal_gate"
    assert "conformal_fallback" in out["reject_detail"]


def test_gate_passes_via_fallback_when_no_calibration_but_confident(monkeypatch):
    monkeypatch.setattr(cg, "load_conformal", lambda bid, path=None: None)
    result = {"signal": "BUY", "confidence": 0.8}
    out = cg.apply_conformal_gate(
        result,
        {"conformal_gate_enabled": True, "_bot_id": "b1", "min_confidence": 0.65},
    )
    assert out is result


def test_gate_rejects_when_conformal_ambiguous(monkeypatch):
    cal = cg.ConformalCalibration(q_hat=0.6, threshold=0.4, n=100, alpha=0.1)
    monkeypatch.setattr(cg, "load_conformal", lambda bid, path=None: cal)
    result = {"signal": "BUY", "confidence": 0.55}
    out = cg.apply_conformal_gate(
        result,
        {"conformal_gate_enabled": True, "_bot_id": "b1"},
    )
    # p_long=0.55, p_short=0.45, both >= 0.4 → ambiguous
    assert out["signal"] == "NONE"
    assert out["reject_reason"] == "conformal_gate"
    assert "ambiguous" in out["reject_detail"]


def test_gate_accepts_when_singleton(monkeypatch):
    cal = cg.ConformalCalibration(q_hat=0.3, threshold=0.7, n=100, alpha=0.1)
    monkeypatch.setattr(cg, "load_conformal", lambda bid, path=None: cal)
    result = {"signal": "BUY", "confidence": 0.85}
    out = cg.apply_conformal_gate(
        result,
        {"conformal_gate_enabled": True, "_bot_id": "b1"},
    )
    assert out is result


def test_gate_skip_in_wf_mode(monkeypatch):
    cal = cg.ConformalCalibration(q_hat=0.3, threshold=0.7, n=100, alpha=0.1)
    monkeypatch.setattr(cg, "load_conformal", lambda bid, path=None: cal)
    result = {"signal": "BUY", "confidence": 0.5}  # would be rejected normally
    out = cg.apply_conformal_gate(
        result,
        {"conformal_gate_enabled": True, "_bot_id": "b1", "_wf_mode": True},
    )
    assert out is result
