"""Tests for Phase 3.7 — CVD + VPIN microstructure features."""

from __future__ import annotations

import math

import pytest

from app.services.bots import microstructure_features as mf
from app.services.bots.ml_feature_engineering import (
    SIGNAL_FEATURE_NAMES,
    SIGNAL_FEATURE_VERSION,
    bar_to_signal_features,
)


# --- Bar buy/sell classification -----------------------------------------


def test_classify_bar_bullish_splits_to_buyers():
    bs = mf.classify_bar_buy_sell(
        open_=100.0, close=102.0, high=103.0, low=99.0, volume=1000.0,
    )
    assert bs.buy > 500.0  # bullish bar → more buy volume
    assert bs.sell < 500.0
    assert bs.buy + bs.sell == pytest.approx(1000.0)


def test_classify_bar_bearish_splits_to_sellers():
    bs = mf.classify_bar_buy_sell(
        open_=100.0, close=98.0, high=101.0, low=97.0, volume=1000.0,
    )
    assert bs.buy < 500.0
    assert bs.sell > 500.0


def test_classify_bar_doji_splits_fifty_fifty():
    bs = mf.classify_bar_buy_sell(
        open_=100.0, close=100.0, high=101.0, low=99.0, volume=1000.0,
    )
    assert bs.buy == pytest.approx(500.0)
    assert bs.sell == pytest.approx(500.0)


def test_classify_bar_zero_volume():
    bs = mf.classify_bar_buy_sell(
        open_=100.0, close=101.0, high=102.0, low=99.0, volume=0.0,
    )
    assert bs.buy == 0.0
    assert bs.sell == 0.0


# --- Tick classification -------------------------------------------------


def test_classify_tick_above_mid_is_buy():
    bs = mf.classify_tick_buy_sell(price=101.0, bid=100.0, ask=102.0, size=10.0)
    assert bs.buy == 10.0
    assert bs.sell == 0.0


def test_classify_tick_below_mid_is_sell():
    bs = mf.classify_tick_buy_sell(price=100.0, bid=100.0, ask=102.0, size=10.0)
    assert bs.buy == 0.0
    assert bs.sell == 10.0


def test_classify_tick_zero_size():
    bs = mf.classify_tick_buy_sell(price=101.0, bid=100.0, ask=102.0, size=0.0)
    assert bs.buy == 0.0 and bs.sell == 0.0


# --- CVD tracker ---------------------------------------------------------


def test_cvd_tracker_accumulates():
    t = mf.CVDTracker(lookback=10)
    t.update(buy=100, sell=50)
    assert t.cvd == pytest.approx(50.0)
    t.update(buy=30, sell=80)
    assert t.cvd == pytest.approx(0.0)


def test_cvd_tracker_update_bar():
    t = mf.CVDTracker(lookback=10)
    t.update_bar(open_=100, close=102, high=103, low=99, volume=1000)
    # Bullish bar → positive delta
    assert t.cvd > 0


def test_cvd_z_score_cold_start():
    t = mf.CVDTracker(lookback=10)
    assert t.cvd_z == 0.0  # no history


def test_cvd_z_score_after_updates():
    t = mf.CVDTracker(lookback=20)
    for _ in range(15):
        t.update(buy=100, sell=50)
    # Consistent positive deltas → latest delta should be near mean → z ≈ 0
    assert abs(t.cvd_z) < 1.0


def test_cvd_slope_positive_on_uptrend():
    t = mf.CVDTracker(lookback=10)
    for i in range(10):
        t.update(buy=100 + i * 10, sell=50)  # increasing deltas
    assert t.cvd_slope > 0


def test_cvd_tracker_lookback_window():
    t = mf.CVDTracker(lookback=5)
    for _ in range(10):
        t.update(buy=100, sell=0)
    # Only last 5 deltas retained
    assert len(t._deltas) == 5


# --- VPIN tracker -------------------------------------------------------


def test_vpin_zero_at_start():
    t = mf.VPINTracker(n_buckets=10)
    assert t.vpin == 0.0


def test_vpin_high_on_imbalanced_flow():
    t = mf.VPINTracker(n_buckets=5, bucket_vol=100.0)
    # All-buy flow → high imbalance → high VPIN
    for _ in range(10):
        t.update(buy=100, sell=0)
    assert t.vpin > 0.5


def test_vpin_low_on_balanced_flow():
    t = mf.VPINTracker(n_buckets=5, bucket_vol=100.0)
    for _ in range(10):
        t.update(buy=50, sell=50)
    assert t.vpin < 0.2


def test_vpin_clamped_to_unit():
    t = mf.VPINTracker(n_buckets=3, bucket_vol=100.0)
    for _ in range(20):
        t.update(buy=100, sell=0)
    assert 0.0 <= t.vpin <= 1.0


def test_vpin_auto_sizes_bucket_on_first_bar():
    t = mf.VPINTracker(n_buckets=10, bucket_vol=0.0)
    t.update_bar(open_=100, close=101, high=102, low=99, volume=500)
    # First bar's volume becomes the bucket size
    assert t.bucket_vol == pytest.approx(500.0)


def test_vpin_update_bar_returns_vpin():
    t = mf.VPINTracker(n_buckets=5, bucket_vol=100.0)
    v = t.update_bar(open_=100, close=102, high=103, low=99, volume=100)
    assert isinstance(v, float)
    assert 0.0 <= v <= 1.0


# --- Batch series --------------------------------------------------------


def _make_candles(n=50, drift=0.001):
    candles = []
    price = 100.0
    for i in range(n):
        ret = drift + (i % 2 - 0.5) * 0.002
        price *= (1 + ret)
        candles.append({
            "time": i, "open": price * 0.999, "high": price * 1.001,
            "low": price * 0.999, "close": price, "volume": 1000.0,
        })
    return candles


def test_compute_cvd_series_length():
    candles = _make_candles(50)
    cvd = mf.compute_cvd_series(candles)
    assert len(cvd) == 50


def test_compute_vpin_series_length():
    candles = _make_candles(50)
    vpin = mf.compute_vpin_series(candles, n_buckets=10)
    assert len(vpin) == 50


def test_compute_vpin_series_in_unit_range():
    candles = _make_candles(50)
    vpin = mf.compute_vpin_series(candles, n_buckets=10)
    assert all(0.0 <= v <= 1.0 for v in vpin)


# --- Feature schema integration -----------------------------------------


def test_feature_schema_version_bumped():
    assert SIGNAL_FEATURE_VERSION == 4


def test_feature_schema_includes_microstructure():
    assert "cvd_z" in SIGNAL_FEATURE_NAMES
    assert "cvd_slope" in SIGNAL_FEATURE_NAMES
    assert "vpin" in SIGNAL_FEATURE_NAMES
    assert "is_rth" in SIGNAL_FEATURE_NAMES
    assert "et_hour_sin" in SIGNAL_FEATURE_NAMES
    assert "minutes_from_open_norm" in SIGNAL_FEATURE_NAMES


def test_bar_to_signal_features_includes_cvd_vpin():
    candles = _make_candles(50)
    # Use last bar as current, rest as lookback
    current = candles[-1]
    lookback = candles[:-1]
    feats = bar_to_signal_features(current, lookback_rows=lookback)
    assert "cvd_z" in feats
    assert "cvd_slope" in feats
    assert "vpin" in feats
    assert isinstance(feats["cvd_z"], float)
    assert isinstance(feats["vpin"], float)
    assert 0.0 <= feats["vpin"] <= 1.0


def test_bar_to_signal_features_cold_start_zeros():
    # No lookback → microstructure features default to 0
    feats = bar_to_signal_features({"close": 100, "open": 100, "high": 101, "low": 99, "volume": 1000})
    assert feats["cvd_z"] == 0.0
    assert feats["cvd_slope"] == 0.0
    assert feats["vpin"] == 0.0


def test_feature_vector_length_matches_schema():
    candles = _make_candles(50)
    feats = bar_to_signal_features(candles[-1], lookback_rows=candles[:-1])
    from app.services.bots.ml_feature_engineering import signal_features_to_vector
    vec = signal_features_to_vector(feats)
    assert len(vec) == len(SIGNAL_FEATURE_NAMES)
