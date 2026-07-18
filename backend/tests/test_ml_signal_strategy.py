"""Tests for ML signal strategy components:
  - ml_feature_engineering  (feature extraction)
  - ml_triple_barrier       (labelling)
  - strategies_ml           (strategy + training pipeline)
"""

import math
import pytest


# ── Feature engineering tests ──────────────────────────────────────────


class TestBarToSignalFeatures:
    def _make_bar(self, **overrides):
        bar = {
            "time": 1700000000,
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 102.0,
            "volume": 1000.0,
            "ATR_14": 3.0,
            "RSI_14": 55.0,
            "MACDh_12_26_9": 0.5,
            "STOCHk_14_3_3": 60.0,
            "ADX_14": 28.0,
            "EMA_9": 101.0,
            "EMA_21": 100.0,
            "BBU_20_2.0": 108.0,
            "BBL_20_2.0": 92.0,
            "BBM_20_2.0": 100.0,
            "VWAP": 101.0,
            "SUPERTd_14_3.0": 1.0,
        }
        bar.update(overrides)
        return bar

    def test_returns_correct_number_of_features(self):
        from app.services.bots.ml_feature_engineering import (
            SIGNAL_FEATURE_NAMES,
            bar_to_signal_features,
        )
        bar = self._make_bar()
        features = bar_to_signal_features(bar)
        assert len(features) == len(SIGNAL_FEATURE_NAMES)
        for name in SIGNAL_FEATURE_NAMES:
            assert name in features, f"Missing feature: {name}"

    def test_all_features_are_finite(self):
        from app.services.bots.ml_feature_engineering import bar_to_signal_features
        bar = self._make_bar()
        features = bar_to_signal_features(bar)
        for name, val in features.items():
            assert isinstance(val, (int, float)), f"{name} is not numeric"
            assert math.isfinite(val), f"{name} is not finite: {val}"

    def test_lookback_improves_rolling_features(self):
        from app.services.bots.ml_feature_engineering import bar_to_signal_features
        bar = self._make_bar()
        # Without lookback: rolling features should default
        f_no_lb = bar_to_signal_features(bar, lookback_rows=None)
        # With lookback: rolling features should be computed
        lookback = [self._make_bar(close=100 + i * 0.1, volume=900 + i * 10) for i in range(25)]
        f_with_lb = bar_to_signal_features(bar, lookback_rows=lookback)
        # close_z_20 should differ when we have real lookback data
        assert f_no_lb["close_z_20"] == 0.0  # no lookback → default
        assert f_with_lb["close_z_20"] != 0.0  # has lookback → computed

    def test_rsi_normalized_to_0_1(self):
        from app.services.bots.ml_feature_engineering import bar_to_signal_features
        bar = self._make_bar(RSI_14=70.0)
        features = bar_to_signal_features(bar)
        assert 0.0 <= features["rsi_14"] <= 1.0
        assert features["rsi_14"] == pytest.approx(0.7, abs=0.01)

    def test_vector_conversion(self):
        from app.services.bots.ml_feature_engineering import (
            SIGNAL_FEATURE_NAMES,
            bar_to_signal_features,
            signal_features_to_vector,
        )
        bar = self._make_bar()
        features = bar_to_signal_features(bar)
        vector = signal_features_to_vector(features)
        assert vector.shape == (len(SIGNAL_FEATURE_NAMES),)
        # Verify ordering matches
        for i, name in enumerate(SIGNAL_FEATURE_NAMES):
            assert vector[i] == pytest.approx(features[name], abs=1e-10)

    def test_handles_missing_indicators_gracefully(self):
        from app.services.bots.ml_feature_engineering import bar_to_signal_features
        # Minimal bar — only OHLCV, no indicators
        bar = {
            "time": 1700000000,
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 102.0,
            "volume": 1000.0,
        }
        features = bar_to_signal_features(bar)
        # Should not raise, all values should be finite
        for name, val in features.items():
            assert math.isfinite(val), f"{name} is not finite: {val}"


# ── Triple-barrier labelling tests ────────────────────────────────────


class TestTripleBarrier:
    def _make_candles(self, closes, *, atr=2.0):
        """Generate candles from a close series with synthetic high/low."""
        candles = []
        for i, c in enumerate(closes):
            candles.append({
                "time": 1700000000 + i * 60,
                "open": c - 0.5,
                "high": c + 1.0,
                "low": c - 1.0,
                "close": c,
                "volume": 1000.0,
                "ATR_14": atr,
            })
        return candles

    def test_uptrend_labels_buy(self):
        from app.services.bots.ml_triple_barrier import label_triple_barrier
        # Strong uptrend: 100, 102, 104, 106, 108, 110, ...
        closes = [100.0 + i * 2.0 for i in range(20)]
        candles = self._make_candles(closes, atr=2.0)
        # With atr_mult=2, upper barrier = entry + 4.0
        # High of bar+2 would be ~105 (close 104 + 1), entry was 100 → barrier at 104
        labels = label_triple_barrier(
            candles, atr_mult_upper=2.0, atr_mult_lower=2.0, max_holding_bars=10
        )
        # First bar should be labelled BUY (price goes up)
        assert labels[0]["label"] == 1
        assert labels[0]["label_name"] == "BUY"
        assert labels[0]["barrier_hit"] == "upper"

    def test_downtrend_labels_sell(self):
        from app.services.bots.ml_triple_barrier import label_triple_barrier
        # Strong downtrend: 110, 108, 106, 104, ...
        closes = [110.0 - i * 2.0 for i in range(20)]
        candles = self._make_candles(closes, atr=2.0)
        labels = label_triple_barrier(
            candles, atr_mult_upper=2.0, atr_mult_lower=2.0, max_holding_bars=10
        )
        # First bar should be labelled SELL (price goes down)
        assert labels[0]["label"] == -1
        assert labels[0]["label_name"] == "SELL"
        assert labels[0]["barrier_hit"] == "lower"

    def test_flat_market_labels_none(self):
        from app.services.bots.ml_triple_barrier import label_triple_barrier
        # Flat: all closes at 100, high/low within ±1
        closes = [100.0] * 20
        candles = self._make_candles(closes, atr=5.0)
        # With atr_mult=2 and ATR=5, barriers at ±10 — never hit with ±1 range
        labels = label_triple_barrier(
            candles, atr_mult_upper=2.0, atr_mult_lower=2.0, max_holding_bars=5
        )
        # Early bars should be NONE (time barrier hit)
        assert labels[0]["label"] == 0
        assert labels[0]["label_name"] == "NONE"
        assert labels[0]["barrier_hit"] == "time"

    def test_label_distribution(self):
        from app.services.bots.ml_triple_barrier import label_distribution
        labels = [
            {"label_name": "BUY"},
            {"label_name": "BUY"},
            {"label_name": "SELL"},
            {"label_name": "NONE"},
        ]
        dist = label_distribution(labels)
        assert dist["BUY"] == 2
        assert dist["SELL"] == 1
        assert dist["NONE"] == 1

    def test_bars_held_and_exit_price(self):
        from app.services.bots.ml_triple_barrier import label_triple_barrier
        closes = [100.0 + i * 2.0 for i in range(20)]
        candles = self._make_candles(closes, atr=2.0)
        labels = label_triple_barrier(
            candles, atr_mult_upper=2.0, atr_mult_lower=2.0, max_holding_bars=10
        )
        first = labels[0]
        assert first["bars_held"] > 0
        assert first["exit_price"] > first["entry_price"]  # upper barrier


# ── Strategy tests ────────────────────────────────────────────────────


class TestMlSignalBoostStrategy:
    def test_returns_none_without_model(self):
        from app.services.bots.strategies_ml import MlSignalBoostStrategy
        strat = MlSignalBoostStrategy({"symbol": "BTCUSDT"})
        bar = {
            "time": 1700000000,
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 102.0,
            "volume": 1000.0,
            "ATR_14": 3.0,
            "RSI_14": 55.0,
            "MACDh_12_26_9": 0.5,
            "STOCHk_14_3_3": 60.0,
            "ADX_14": 28.0,
            "EMA_9": 101.0,
            "EMA_21": 100.0,
            "_symbol": "BTCUSDT",
        }
        # Feed enough bars to pass lookback requirement
        for i in range(25):
            result = strat.evaluate({**bar, "close": 100 + i * 0.1})
        # Without a trained model, should return NONE
        assert result["signal"] == "NONE"

    def test_returns_none_with_insufficient_lookback(self):
        from app.services.bots.strategies_ml import MlSignalBoostStrategy
        strat = MlSignalBoostStrategy({"symbol": "BTCUSDT"})
        bar = {"time": 1700000000, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}
        # Only 1 bar — not enough lookback
        result = strat.evaluate(bar)
        assert result["signal"] == "NONE"


class TestGetStrategy:
    def test_ml_signal_boost_registered(self):
        from app.services.bots.strategies import get_strategy
        strat = get_strategy("ML_SIGNAL_BOOST", {"symbol": "BTCUSDT"})
        assert strat is not None
        from app.services.bots.strategies_ml import MlSignalBoostStrategy
        assert isinstance(strat, MlSignalBoostStrategy)


class TestCatalog:
    def test_ml_signal_boost_in_catalog(self):
        from app.services.bots.strategy_catalog import list_strategy_catalog
        catalog = list_strategy_catalog()
        ids = [s["id"] for s in catalog]
        assert "ML_SIGNAL_BOOST" in ids
        entry = next(s for s in catalog if s["id"] == "ML_SIGNAL_BOOST")
        assert entry["category"] == "ml"
        assert entry["execution_mode"] == "BAR_CLOSE"
