"""Schema v7 Phase 1–3 feature upgrade smoke tests."""

from __future__ import annotations

import numpy as np

from app.services.bots.ml_feature_advanced import resolve_peer_symbols
from app.services.bots.ml_feature_engineering import (
    SIGNAL_FEATURE_NAMES,
    SIGNAL_FEATURE_VERSION,
    TRADE_STATE_FEATURE_VERSION,
    bar_to_signal_features,
    compute_signal_feature_matrix_vectorized,
    is_compatible_feature_schema,
    precompute_signal_feature_matrix_loop,
)


PHASE1 = (
    "htf_trend_1h", "htf_rsi_1h", "htf_atr_ratio_4h", "htf_regime_daily",
    "rv_parkinson_20", "rv_garman_klass_20", "vol_regime_ratio", "vol_of_vol",
)
PHASE2 = (
    "sentiment_score_24h", "sentiment_momentum", "macro_event_proximity",
    "frac_diff_close", "frac_diff_volume",
    "hurst_exponent_50", "sample_entropy_20", "information_ratio_20",
)
PHASE3 = (
    "peer_returns_avg", "peer_divergence", "correlation_rolling_20",
    "dist_to_support_norm", "dist_to_resistance_norm", "range_position",
    "funding_rate_norm", "oi_change_24h_norm",
)


def _make_bar(i: int, **overrides):
    base = 100.0 + i * 0.05 + (i % 7) * 0.02
    bar = {
        "time": 1_700_000_000 + i * 60,
        "open": base - 0.1,
        "high": base + 1.0 + (i % 3) * 0.1,
        "low": base - 1.0 - (i % 2) * 0.1,
        "close": base + 0.2 * ((i % 5) - 2),
        "volume": 1000.0 + i * 3 + (i % 11) * 10,
        "ATR_14": 1.5 + (i % 9) * 0.05,
        "ATR_14_median_20": 1.4,
        "RSI_14": 50.0 + (i % 10),
        "MACDh_12_26_9": 0.1 * ((i % 5) - 2),
        "STOCHk_14_3_3": 55.0,
        "ADX_14": 22.0 + (i % 8),
        "EMA_9": base,
        "EMA_21": base - 0.3,
        "BBU_20_2.0": base + 2.0,
        "BBL_20_2.0": base - 2.0,
        "BBM_20_2.0": base,
        "VWAP": base,
        "SUPERTd_14_3.0": 1.0 if i % 2 == 0 else -1.0,
        "_symbol": "BTCUSDT",
    }
    bar.update(overrides)
    return bar


def test_schema_v7_dimensions():
    for name in PHASE1 + PHASE2 + PHASE3:
        assert name in SIGNAL_FEATURE_NAMES
    assert is_compatible_feature_schema(7)
    assert is_compatible_feature_schema(1007)


def test_legacy_schemas_compatible():
    assert is_compatible_feature_schema(4)
    assert is_compatible_feature_schema(5)
    assert is_compatible_feature_schema(7)
    assert is_compatible_feature_schema(1007)
    assert not is_compatible_feature_schema(99)


def test_sentiment_defaults_to_neutral_zeros():
    rows = [_make_bar(i) for i in range(40)]
    feats = bar_to_signal_features(rows[-1], lookback_rows=rows[:-1], symbol="BTCUSDT")
    assert feats["sentiment_score_24h"] == 0.0
    assert feats["sentiment_momentum"] == 0.0
    assert feats["macro_event_proximity"] == 0.0


def test_peer_fallback_hardcoded():
    peers = resolve_peer_symbols("BTCUSDT", top_k=3)
    assert len(peers) == 3
    assert "BTCUSDT" not in peers
    assert all(p.endswith("USDT") for p in peers)


def test_htf_and_rv_nonzero_with_history():
    rows = [_make_bar(i) for i in range(200)]
    feats = bar_to_signal_features(rows[-1], lookback_rows=rows[:-1], symbol="BTCUSDT")
    assert feats["rv_parkinson_20"] > 0.0
    assert feats["rv_garman_klass_20"] >= 0.0
    assert "htf_rsi_1h" in feats
    assert 0.0 <= feats["htf_rsi_1h"] <= 1.0


def test_vectorized_loop_parity_v7():
    rows = [_make_bar(i) for i in range(100)]
    loop = precompute_signal_feature_matrix_loop(rows)
    vec = compute_signal_feature_matrix_vectorized(rows)
    assert loop.shape == (100, len(SIGNAL_FEATURE_NAMES))
    assert vec.shape == loop.shape
    assert np.allclose(loop, vec, atol=1e-5, rtol=1e-5)


def test_advanced_last_window_cap_matches_full_tail():
    """Live evaluate may cap history at 512; last-row values must match full prefix."""
    from app.services.bots.ml_feature_advanced import advanced_features_for_bar

    rows = [_make_bar(i) for i in range(600)]
    full = advanced_features_for_bar(rows[-1], rows[:-1], symbol="BTCUSDT")
    capped = advanced_features_for_bar(rows[-1], rows[-513:-1], symbol="BTCUSDT")
    for key in full:
        assert abs(full[key] - capped[key]) < 1e-9, key
