"""Tests for LSTM Direction Classifier components:
  - ml_lstm_trainer  (scaler, sequence building)
  - strategies_lstm  (strategy, model store, softmax)
"""

import math
import pytest
import numpy as np


# ── Scaler tests ─────────────────────────────────────────────────────────


class TestScaler:
    def test_compute_scaler_shape(self):
        from app.services.bots.ml_lstm_trainer import compute_scaler
        # 10 sequences of 60 timesteps × 34 features
        X = np.random.randn(10, 60, 34).astype(np.float32)
        scaler = compute_scaler(X)
        assert len(scaler["mean"]) == 34
        assert len(scaler["std"]) == 34

    def test_compute_scaler_prevents_zero_std(self):
        from app.services.bots.ml_lstm_trainer import compute_scaler
        # Constant feature → std would be 0
        X = np.ones((5, 10, 3), dtype=np.float32)
        scaler = compute_scaler(X)
        for s in scaler["std"]:
            assert s >= 1e-8, "std should be clamped above zero"

    def test_apply_scaler_normalizes(self):
        from app.services.bots.ml_lstm_trainer import apply_scaler, compute_scaler
        X = np.random.randn(20, 10, 4).astype(np.float32)
        scaler = compute_scaler(X)
        X_scaled = apply_scaler(X.copy(), scaler)
        # After scaling, mean should be ~0 and std ~1
        flat = X_scaled.reshape(-1, 4)
        for i in range(4):
            assert abs(flat[:, i].mean()) < 0.3, f"Feature {i} mean not near 0"
            assert abs(flat[:, i].std() - 1.0) < 0.3, f"Feature {i} std not near 1"

    def test_scaler_persistence(self, tmp_path, monkeypatch):
        from app.services.bots.ml_lstm_trainer import save_scaler, load_scaler, LSTM_MODEL_DIR
        import app.services.bots.ml_lstm_trainer as trainer_mod

        # Redirect model dir to tmp
        monkeypatch.setattr(trainer_mod, "LSTM_MODEL_DIR", str(tmp_path))
        scaler = {"mean": [1.0, 2.0, 3.0], "std": [0.5, 1.0, 1.5]}
        save_scaler("BTCUSDT", scaler)
        loaded = load_scaler("BTCUSDT")
        assert loaded is not None
        assert loaded["mean"] == scaler["mean"]
        assert loaded["std"] == scaler["std"]


# ── Sequence building tests ──────────────────────────────────────────────


class TestBuildSequences:
    def _make_candles(self, n=200, atr=2.0):
        candles = []
        for i in range(n):
            c = 100.0 + i * 0.1
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

    def test_builds_correct_shape(self):
        from app.services.bots.ml_lstm_trainer import build_sequences
        from app.services.bots.ml_triple_barrier import label_triple_barrier

        candles = self._make_candles(300)
        labels = label_triple_barrier(candles, atr_mult_upper=2.0, atr_mult_lower=2.0, max_holding_bars=30)
        X, y = build_sequences(candles, labels, lookback=60, max_holding_bars=30)

        assert X.ndim == 3
        assert X.shape[1] == 60  # lookback
        assert X.shape[2] == 34  # N_FEATURES
        assert len(y) == len(X)
        assert set(np.unique(y)).issubset({0, 1, 2})

    def test_insufficient_candles_returns_empty(self):
        from app.services.bots.ml_lstm_trainer import build_sequences
        from app.services.bots.ml_triple_barrier import label_triple_barrier

        candles = self._make_candles(50)  # too few
        labels = label_triple_barrier(candles, max_holding_bars=30)
        X, y = build_sequences(candles, labels, lookback=60, max_holding_bars=30)
        assert len(X) == 0
        assert len(y) == 0


# ── Softmax tests ────────────────────────────────────────────────────────


class TestSoftmax:
    def test_sums_to_one(self):
        from app.services.bots.strategies_lstm import _softmax
        logits = np.array([2.0, 1.0, 0.1])
        proba = _softmax(logits)
        assert abs(proba.sum() - 1.0) < 1e-6

    def test_handles_large_values(self):
        from app.services.bots.strategies_lstm import _softmax
        logits = np.array([1000.0, 1.0, 0.1])
        proba = _softmax(logits)
        assert all(np.isfinite(proba))
        assert abs(proba.sum() - 1.0) < 1e-6

    def test_max_gets_highest_probability(self):
        from app.services.bots.strategies_lstm import _softmax
        logits = np.array([5.0, 1.0, 0.1])
        proba = _softmax(logits)
        assert np.argmax(proba) == 0


# ── Strategy tests ────────────────────────────────────────────────────────


class TestLstmDirectionStrategy:
    def test_returns_none_without_model(self):
        from app.services.bots.strategies_lstm import LstmDirectionStrategy
        strat = LstmDirectionStrategy({"symbol": "BTCUSDT"})
        bar = {
            "time": 1700000000,
            "open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0,
            "volume": 1000.0, "ATR_14": 3.0, "RSI_14": 55.0,
            "MACDh_12_26_9": 0.5, "STOCHk_14_3_3": 60.0,
            "ADX_14": 28.0, "EMA_9": 101.0, "EMA_21": 100.0,
            "_symbol": "BTCUSDT",
        }
        # Feed 80 bars to fill both bar_history and LSTM window
        for i in range(80):
            result = strat.evaluate({**bar, "close": 100 + i * 0.1})
        assert result["signal"] == "NONE"
        assert result.get("reject_reason") in ("ml_model_missing", "ml_warmup")

    def test_returns_none_with_insufficient_lookback(self):
        from app.services.bots.strategies_lstm import LstmDirectionStrategy
        strat = LstmDirectionStrategy({"symbol": "BTCUSDT", "lookback": 60})
        bar = {"time": 1700000000, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}
        # Only 1 bar
        result = strat.evaluate(bar)
        assert result["signal"] == "NONE"
        assert result.get("reject_reason") == "ml_warmup"

    def test_reverse_map_accepts_int_and_str_keys(self):
        from app.services.bots.ml_lstm_trainer import REVERSE_MAP
        from app.services.bots.strategies_lstm import _softmax
        # Simulate predict path resolution
        for reverse_map in (REVERSE_MAP, {str(k): v for k, v in REVERSE_MAP.items()}):
            pred_idx = 0
            signal = reverse_map.get(str(pred_idx), reverse_map.get(pred_idx, "NONE"))
            assert signal == "BUY"
            proba = _softmax(np.array([3.0, 0.1, 0.1]))
            assert np.argmax(proba) == 0


class TestLstmRegistration:
    def test_get_strategy_returns_lstm(self):
        from app.services.bots.strategies import get_strategy
        strat = get_strategy("LSTM_DIRECTION", {"symbol": "BTCUSDT"})
        assert strat is not None
        from app.services.bots.strategies_lstm import LstmDirectionStrategy
        assert isinstance(strat, LstmDirectionStrategy)

    def test_lstm_in_catalog(self):
        from app.services.bots.strategy_catalog import list_strategy_catalog
        catalog = list_strategy_catalog()
        ids = [s["id"] for s in catalog]
        assert "LSTM_DIRECTION" in ids
        entry = next(s for s in catalog if s["id"] == "LSTM_DIRECTION")
        assert entry["category"] == "ml"
        assert entry["execution_mode"] == "BAR_CLOSE"
