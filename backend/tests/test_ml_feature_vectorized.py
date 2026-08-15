"""Parity: vectorized feature matrix vs per-bar bar_to_signal_features."""

from __future__ import annotations

import numpy as np
import pytest


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
        "_symbol": "TESTUSDT",
    }
    bar.update(overrides)
    return bar


class TestTrainLookbackParity:
    def test_train_uses_eval_feature_lookback(self):
        """Train feature windows must match evaluate deque (24 priors)."""
        import inspect

        from app.services.bots.ml_feature_engineering import EVAL_FEATURE_LOOKBACK
        from app.services.bots.ml_lstm_trainer import build_sequences
        from app.services.bots.ml_tcn_trainer import build_tcn_sequences
        from app.services.bots.ml_transformer_trainer import build_transformer_sequences

        assert EVAL_FEATURE_LOOKBACK == 24
        # Source-level guard: trainers must not hardcode 20 as feature window.
        for fn in (build_sequences, build_tcn_sequences, build_transformer_sequences):
            src = inspect.getsource(fn)
            assert "EVAL_FEATURE_LOOKBACK" in src, f"{fn.__name__} missing EVAL_FEATURE_LOOKBACK"
            assert "feature_lookback = 20" not in src
            assert "feature_lb = 20" not in src


class TestVectorizedFeatureParity:
    def test_vectorized_matches_loop(self):
        from app.services.bots.ml_feature_engineering import (
            compute_signal_feature_matrix_vectorized,
            precompute_signal_feature_matrix_loop,
        )

        rows = [_make_bar(i) for i in range(120)]
        loop = precompute_signal_feature_matrix_loop(rows)
        vec = compute_signal_feature_matrix_vectorized(rows)
        assert loop.shape == vec.shape
        # Tight tolerance — float32 matrix vs float64→float32 loop path
        assert np.allclose(loop, vec, atol=1e-5, rtol=1e-5), (
            f"max abs diff={np.max(np.abs(loop.astype(np.float64) - vec.astype(np.float64)))}"
        )

    def test_numba_microstructure_matches_python(self):
        """Numba CVD/VPIN window kernel must match Python trackers."""
        from app.services.bots.ml_feature_engineering import _microstructure_windowed_python
        from app.services.bots.ml_feature_kernels import (
            _ensure_numba,
            microstructure_windowed_fast,
        )

        if not _ensure_numba():
            pytest.skip("numba unavailable")
        rows = [_make_bar(i) for i in range(90)]
        o = np.array([r["open"] for r in rows], dtype=np.float64)
        c = np.array([r["close"] for r in rows], dtype=np.float64)
        h = np.array([r["high"] for r in rows], dtype=np.float64)
        l = np.array([r["low"] for r in rows], dtype=np.float64)
        v = np.array([r["volume"] for r in rows], dtype=np.float64)
        py = _microstructure_windowed_python(o, c, h, l, v, 24)
        nb = microstructure_windowed_fast(o, c, h, l, v, 24)
        for a, b, name in zip(py, nb, ("cvd_z", "cvd_slope", "vpin")):
            assert np.allclose(a, b, atol=1e-9, rtol=1e-9), (
                f"{name} max abs={np.max(np.abs(a - b))}"
            )

    def test_vectorized_emits_intermediate_progress(self):
        """Long vectorized precompute must not sit silent (UI 15-min stall guard)."""
        from app.services.bots.ml_feature_engineering import (
            compute_signal_feature_matrix_vectorized,
        )

        rows = [_make_bar(i) for i in range(2500)]
        seen = []

        def _cb(done, total):
            seen.append((int(done), int(total)))

        compute_signal_feature_matrix_vectorized(rows, progress_cb=_cb)
        assert seen, "expected progress callbacks during vectorized features"
        assert seen[0][0] == 0
        assert seen[-1] == (2500, 2500)
        # Must report at least one mid-phase tick before completion.
        assert any(0 < d < 2500 for d, _ in seen)

    def test_vectorized_matches_evaluate_deque(self):
        from collections import deque

        from app.services.bots.ml_feature_engineering import (
            EVAL_FEATURE_LOOKBACK,
            EVAL_HISTORY_LOOKBACK,
            bar_to_signal_features,
            compute_signal_feature_matrix_vectorized,
            signal_features_to_vector,
        )

        assert EVAL_FEATURE_LOOKBACK == 24
        rows = [_make_bar(i) for i in range(90)]
        feat_mat = compute_signal_feature_matrix_vectorized(rows)
        # Live evaluate keeps a long history for HTF; micro features still window
        # to EVAL_FEATURE_LOOKBACK inside bar_to_signal_features.
        hist: deque = deque(maxlen=EVAL_HISTORY_LOOKBACK + 1)
        for i, row in enumerate(rows):
            hist.append(dict(row))
            if len(hist) < 20:
                continue
            ev = signal_features_to_vector(
                bar_to_signal_features(row, lookback_rows=list(hist)[:-1])
            )
            assert np.allclose(ev, feat_mat[i], atol=1e-5, rtol=1e-5), f"bar {i}"

    def test_flag_dispatches_loop(self, monkeypatch):
        from app.services.bots.ml_feature_engineering import (
            precompute_signal_feature_matrix,
            precompute_signal_feature_matrix_loop,
        )

        rows = [_make_bar(i) for i in range(40)]
        monkeypatch.setenv("BACKTEST_VECTORIZED_FEATURES", "false")
        out = precompute_signal_feature_matrix(rows)
        ref = precompute_signal_feature_matrix_loop(rows)
        assert np.allclose(out, ref, atol=1e-6)

    def test_batch_precompute_parity_with_vectorized(self):
        from app.services.bots.strategies_ml import (
            MlSignalBoostStrategy,
            get_ml_signal_store,
        )

        class _FakeGbm:
            def predict_proba(self, X):
                X = np.asarray(X, dtype=np.float64)
                out = np.zeros((X.shape[0], 3), dtype=np.float64)
                for i, row in enumerate(X):
                    s = float(np.nan_to_num(row, nan=0.0).sum())
                    buy = 0.2 + 0.01 * ((s * 10) % 7)
                    sell = 0.2 + 0.01 * ((s * 3) % 5)
                    none = max(0.05, 1.0 - buy - sell)
                    total = buy + sell + none
                    out[i] = [buy / total, none / total, sell / total]
                return out

        store = get_ml_signal_store()
        symbol = "VECUSDT"
        tf = "1m"
        key = store._cache_key(symbol, None, tf)
        store._models[key] = _FakeGbm()
        store._metadata[key] = {
            "reverse_map": {"0": "BUY", "1": "NONE", "2": "SELL"},
            "feature_schema_version": 4,
            "feature_names": None,
        }
        store._mtime[key] = -1.0

        rows = [_make_bar(i, _symbol=symbol) for i in range(64)]
        cfg = {
            "symbol": symbol,
            "model_symbol": symbol,
            "timeframe": tf,
            "min_confidence": 0.35,
            "batch_inference_size": 16,
            "calibration_gate_enabled": False,
        }
        batch_out = MlSignalBoostStrategy(cfg).precompute_backtest_signals(rows)
        per_strat = MlSignalBoostStrategy(cfg)
        per_out = [per_strat.evaluate(row) for row in rows]
        for i, (b, p) in enumerate(zip(batch_out, per_out)):
            assert b.get("signal") == p.get("signal"), f"bar {i}: {b} vs {p}"
            if b.get("confidence") is not None and p.get("confidence") is not None:
                assert b["confidence"] == pytest.approx(p["confidence"], abs=1e-5)
