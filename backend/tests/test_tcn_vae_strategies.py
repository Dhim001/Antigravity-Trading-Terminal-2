"""Tests for P3 TCN Multi-Horizon + P4 VAE Regime Detector."""

import pytest
import numpy as np


# ── Helper ────────────────────────────────────────────────────────────────

def _make_candles(n=300, trend=0.1, atr=2.0):
    candles = []
    for i in range(n):
        c = 100.0 + i * trend
        candles.append({
            "time": 1700000000 + i * 60,
            "open": c - 0.5,
            "high": c + 1.0,
            "low": c - 1.0,
            "close": c,
            "volume": 1000.0 + i,
            "ATR_14": atr,
            "RSI_14": 50.0 + (i % 20),
            "MACDh_12_26_9": 0.1 * (i % 10 - 5),
            "STOCHk_14_3_3": 50.0,
            "ADX_14": 25.0,
            "EMA_9": c - 0.2,
            "EMA_21": c - 0.5,
        })
    return candles


# ── TCN Tests ─────────────────────────────────────────────────────────────


class TestTcnSequenceBuilding:
    def test_builds_correct_shape(self):
        from app.services.bots.ml_tcn_trainer import build_tcn_sequences
        candles = _make_candles(300)
        X, y = build_tcn_sequences(candles, lookback=60)
        assert X.ndim == 3
        assert X.shape[1] == 60  # lookback
        assert X.shape[2] == 34  # N_FEATURES
        assert y.ndim == 2
        assert y.shape[1] == 3  # 3 horizons

    def test_returns_are_finite(self):
        from app.services.bots.ml_tcn_trainer import build_tcn_sequences
        candles = _make_candles(300)
        X, y = build_tcn_sequences(candles, lookback=60)
        assert np.all(np.isfinite(X))
        assert np.all(np.isfinite(y))

    def test_insufficient_candles(self):
        from app.services.bots.ml_tcn_trainer import build_tcn_sequences
        candles = _make_candles(50)
        X, y = build_tcn_sequences(candles, lookback=120)
        assert len(X) == 0


class TestForwardReturns:
    def test_compute_returns(self):
        from app.services.bots.ml_tcn_trainer import _compute_forward_returns
        closes = [100.0 + i for i in range(200)]
        r = _compute_forward_returns(closes, 10)
        assert r is not None
        ret_5, ret_15, ret_60 = r
        assert ret_5 > 0  # uptrend
        assert ret_15 > ret_5
        assert ret_60 > ret_15

    def test_returns_none_at_boundary(self):
        from app.services.bots.ml_tcn_trainer import _compute_forward_returns
        closes = [100.0 + i for i in range(50)]
        r = _compute_forward_returns(closes, 40)  # not enough data for 60-bar return
        assert r is None


class TestTcnStrategy:
    def test_returns_none_without_model(self):
        from app.services.bots.strategies_tcn import TcnMultiHorizonStrategy
        strat = TcnMultiHorizonStrategy({"symbol": "BTCUSDT"})
        bar = {
            "time": 1700000000, "open": 100, "high": 105, "low": 95, "close": 102,
            "volume": 1000, "ATR_14": 3.0, "RSI_14": 55.0,
            "MACDh_12_26_9": 0.5, "STOCHk_14_3_3": 60.0,
            "ADX_14": 28.0, "EMA_9": 101.0, "EMA_21": 100.0,
            "_symbol": "BTCUSDT",
        }
        for i in range(130):
            result = strat.evaluate({**bar, "close": 100 + i * 0.1})
        assert result["signal"] == "NONE"

    def test_returns_none_insufficient_lookback(self):
        from app.services.bots.strategies_tcn import TcnMultiHorizonStrategy
        strat = TcnMultiHorizonStrategy({"symbol": "BTCUSDT"})
        bar = {"time": 1700000000, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}
        result = strat.evaluate(bar)
        assert result["signal"] == "NONE"


class TestTcnRegistration:
    def test_factory(self):
        from app.services.bots.strategies import get_strategy
        from app.services.bots.strategies_tcn import TcnMultiHorizonStrategy
        strat = get_strategy("TCN_MULTI_HORIZON", {"symbol": "BTCUSDT"})
        assert isinstance(strat, TcnMultiHorizonStrategy)

    def test_catalog(self):
        from app.services.bots.strategy_catalog import list_strategy_catalog
        ids = [s["id"] for s in list_strategy_catalog()]
        assert "TCN_MULTI_HORIZON" in ids


# ── VAE Tests ─────────────────────────────────────────────────────────────


class TestReconstructionError:
    def test_zero_error_for_identical(self):
        from app.services.bots.ml_vae_regime import compute_reconstruction_error
        x = np.array([1.0, 2.0, 3.0])
        assert compute_reconstruction_error(x, x) == 0.0

    def test_positive_error_for_different(self):
        from app.services.bots.ml_vae_regime import compute_reconstruction_error
        x = np.array([1.0, 2.0, 3.0])
        y = np.array([1.5, 2.5, 3.5])
        err = compute_reconstruction_error(x, y)
        assert err > 0
        assert abs(err - 0.25) < 1e-6  # mean of [0.25, 0.25, 0.25]


class TestVaeStrategy:
    def test_returns_none_without_model(self):
        from app.services.bots.strategies_vae_regime import VaeRegimeStrategy
        strat = VaeRegimeStrategy({"symbol": "BTCUSDT"})
        bar = {
            "time": 1700000000, "open": 100, "high": 105, "low": 95, "close": 102,
            "volume": 1000, "ATR_14": 3.0, "RSI_14": 55.0,
            "MACDh_12_26_9": 0.5, "STOCHk_14_3_3": 60.0,
            "ADX_14": 28.0, "EMA_9": 101.0, "EMA_21": 100.0,
            "_symbol": "BTCUSDT",
        }
        for i in range(30):
            result = strat.evaluate({**bar, "close": 100 + i * 0.1})
        assert result["signal"] == "NONE"

    def test_anomaly_history_tracking(self):
        from app.services.bots.strategies_vae_regime import VaeRegimeStrategy
        strat = VaeRegimeStrategy({"symbol": "BTCUSDT"})
        assert len(strat._anomaly_history) == 0


class TestVaeRegistration:
    def test_factory(self):
        from app.services.bots.strategies import get_strategy
        from app.services.bots.strategies_vae_regime import VaeRegimeStrategy
        strat = get_strategy("VAE_REGIME_DETECTOR", {"symbol": "BTCUSDT"})
        assert isinstance(strat, VaeRegimeStrategy)

    def test_catalog(self):
        from app.services.bots.strategy_catalog import list_strategy_catalog
        ids = [s["id"] for s in list_strategy_catalog()]
        assert "VAE_REGIME_DETECTOR" in ids
