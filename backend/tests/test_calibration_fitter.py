"""Tests for calibration_fitter — temperature scaling + fractional Kelly."""

from __future__ import annotations

import json
import os

import pytest

from app.services.bots import calibration_fitter as cf


# --- Temperature scaling --------------------------------------------------


def test_calibrate_identity_when_T_is_1():
    assert cf.calibrate_probability(0.7, 1.0) == pytest.approx(0.7, abs=1e-9)


def test_calibrate_T_gt_1_flattens():
    # T > 1 should pull probabilities toward 0.5
    assert cf.calibrate_probability(0.9, 2.0) < 0.9
    assert cf.calibrate_probability(0.9, 2.0) > 0.5
    assert cf.calibrate_probability(0.1, 2.0) > 0.1
    assert cf.calibrate_probability(0.1, 2.0) < 0.5


def test_calibrate_T_lt_1_sharpens():
    # T < 1 should push probabilities away from 0.5
    assert cf.calibrate_probability(0.9, 0.5) > 0.9
    assert cf.calibrate_probability(0.1, 0.5) < 0.1


def test_calibrate_invalid_returns_input():
    assert cf.calibrate_probability(0.7, 0.0) == pytest.approx(0.7)
    assert cf.calibrate_probability(0.7, -1.0) == pytest.approx(0.7)
    assert cf.calibrate_probability(float("nan"), 1.0) == 0.5


def test_calibrate_clamps_extremes():
    # 0.0 and 1.0 must not blow up logit
    assert 0.0 <= cf.calibrate_probability(0.0, 1.5) <= 1.0
    assert 0.0 <= cf.calibrate_probability(1.0, 1.5) <= 1.0


# --- fit_temperature ------------------------------------------------------


def test_fit_temperature_recovers_identity_on_well_calibrated():
    # Construct data where empirical win rate matches predicted probability:
    # 100 samples at p=0.6 with exactly 60 wins → MLE T should be near 1.
    probs = [0.6] * 100
    labels = [1] * 60 + [0] * 40
    T = cf.fit_temperature(probs, labels, n_steps=300, lr=0.05)
    assert 0.7 <= T <= 1.3


def test_fit_temperature_overconfident_pushes_T_above_1():
    # Model says 0.9 but only 50% are actually wins → T should rise to flatten
    probs = [0.9] * 100
    labels = [1, 0] * 50  # 50% win rate
    T = cf.fit_temperature(probs, labels, n_steps=300, lr=0.05)
    assert T > 1.5


def test_fit_temperature_empty_returns_default():
    assert cf.fit_temperature([], []) == cf.DEFAULT_TEMPERATURE
    assert cf.fit_temperature([0.5, 0.6], [1]) == cf.DEFAULT_TEMPERATURE


def test_fit_temperature_clamped_to_range():
    # Pathological inputs should not escape the clamp
    T = cf.fit_temperature([0.99] * 50, [0] * 50, n_steps=1000, lr=10.0)
    assert cf.MIN_TEMPERATURE <= T <= cf.MAX_TEMPERATURE


# --- Fractional Kelly ------------------------------------------------------


def test_kelly_fraction_zero_when_no_edge():
    # p=0.5, b=1 → no edge
    assert cf.kelly_fraction(0.5, 1.0) == 0.0


def test_kelly_fraction_positive_with_edge():
    # p=0.6, b=2 → f* = (2*0.6 - 0.4)/2 = 0.4 → quarter = 0.1
    assert cf.kelly_fraction(0.6, 2.0, fraction=0.25) == pytest.approx(0.1, abs=1e-9)


def test_kelly_fraction_negative_floored_to_zero():
    # p=0.3, b=1 → negative Kelly → 0
    assert cf.kelly_fraction(0.3, 1.0) == 0.0


def test_kelly_fraction_invalid_inputs():
    assert cf.kelly_fraction(float("nan"), 1.0) == 0.0
    assert cf.kelly_fraction(0.6, 0.0) == 0.0
    assert cf.kelly_fraction(0.6, -1.0) == 0.0


def test_kelly_fraction_respects_fraction_cap():
    full = cf.kelly_fraction(0.7, 2.0, fraction=1.0)
    quarter = cf.kelly_fraction(0.7, 2.0, fraction=0.25)
    assert quarter == pytest.approx(full * 0.25, abs=1e-9)


def test_kelly_size_scale_below_min_p_returns_one():
    assert cf.kelly_size_scale(0.4, 2.0, min_p=0.5) == 1.0


def test_kelly_size_scale_no_edge_returns_one():
    assert cf.kelly_size_scale(0.5, 1.0) == 1.0


def test_kelly_size_scale_clamped():
    # Very strong edge should hit the upper clamp, not blow up
    scale = cf.kelly_size_scale(0.99, 10.0, fraction=1.0)
    assert scale == cf.MAX_SIZE_SCALE


def test_kelly_size_scale_min_clamp():
    # Tiny edge should hit the lower clamp, not zero out
    scale = cf.kelly_size_scale(0.51, 1.0, fraction=1.0)
    assert scale >= cf.MIN_SIZE_SCALE


# --- Persistence ----------------------------------------------------------


def test_calibration_blob_roundtrip(tmp_path):
    blob = cf.CalibrationBlob(
        temperature=1.42, kelly_fraction=0.3, fitted_samples=200,
        avg_win=12.5, avg_loss=5.0,
    )
    path = str(tmp_path / "cal.json")
    cf.save_calibration(path, blob)
    loaded = cf.load_calibration(path)
    assert loaded is not None
    assert loaded.temperature == pytest.approx(1.42, abs=1e-6)
    assert loaded.kelly_fraction == pytest.approx(0.3, abs=1e-4)
    assert loaded.fitted_samples == 200
    assert loaded.avg_win == pytest.approx(12.5, abs=1e-6)
    assert loaded.avg_loss == pytest.approx(5.0, abs=1e-6)


def test_load_calibration_missing_returns_none(tmp_path):
    assert cf.load_calibration(str(tmp_path / "nope.json")) is None


def test_load_calibration_corrupt_returns_none(tmp_path):
    path = str(tmp_path / "bad.json")
    with open(path, "w") as fh:
        fh.write("{not json")
    assert cf.load_calibration(path) is None


# --- Live-path cache ------------------------------------------------------


def test_get_bot_calibration_returns_default_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(cf, "_default_path", lambda bid: str(tmp_path / f"{bid}.json"))
    cf.invalidate_bot_cache()
    blob = cf.get_bot_calibration("bot-x")
    assert blob.temperature == cf.DEFAULT_TEMPERATURE
    assert blob.kelly_fraction == cf.DEFAULT_KELLY_FRACTION


def test_invalidate_bot_cache_clears_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(cf, "_default_path", lambda bid: str(tmp_path / f"{bid}.json"))
    cf.invalidate_bot_cache()
    cf.get_bot_calibration("bot-y")
    assert "bot-y" in cf._bot_cache
    cf.invalidate_bot_cache("bot-y")
    assert "bot-y" not in cf._bot_cache
