"""Parity + helpers for backtest batch ML inference."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import numpy as np
import pytest


def _make_bar(i: int, **overrides):
    base = 100.0 + i * 0.05
    bar = {
        "time": 1_700_000_000 + i * 60,
        "open": base,
        "high": base + 1.0,
        "low": base - 1.0,
        "close": base + 0.2,
        "volume": 1000.0 + i,
        "ATR_14": 1.5,
        "RSI_14": 50.0 + (i % 10),
        "MACDh_12_26_9": 0.1 * ((i % 5) - 2),
        "STOCHk_14_3_3": 55.0,
        "ADX_14": 22.0,
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


class _FakeGbm:
    """Deterministic 3-class predict_proba for parity tests."""

    def predict_proba(self, X):
        X = np.asarray(X, dtype=np.float64)
        out = np.zeros((X.shape[0], 3), dtype=np.float64)
        for i, row in enumerate(X):
            s = float(np.nan_to_num(row, nan=0.0).sum())
            # Bias class by feature sum so batch vs row order is exercised.
            buy = 0.2 + 0.01 * ((s * 10) % 7)
            sell = 0.2 + 0.01 * ((s * 3) % 5)
            none = max(0.05, 1.0 - buy - sell)
            total = buy + sell + none
            out[i] = [buy / total, none / total, sell / total]
        return out


class TestBatchInferenceFlags:
    def test_batch_enabled_default_for_ml(self, monkeypatch):
        from app.services.bots.ml_batch_inference import batch_inference_enabled

        monkeypatch.delenv("BACKTEST_BATCH_INFERENCE", raising=False)
        assert batch_inference_enabled("ML_SIGNAL_BOOST") is True

    def test_batch_disabled_via_env(self, monkeypatch):
        from app.services.bots.ml_batch_inference import batch_inference_enabled

        monkeypatch.setenv("BACKTEST_BATCH_INFERENCE", "false")
        assert batch_inference_enabled("ML_SIGNAL_BOOST") is False

    def test_batch_size_clamped(self, monkeypatch):
        from app.services.bots.ml_batch_inference import inference_batch_size

        monkeypatch.setenv("BACKTEST_INFERENCE_BATCH_SIZE", "99999")
        assert inference_batch_size() == 4096
        assert inference_batch_size({"batch_inference_size": 64}) == 64


class TestMlSignalBoostBatchParity:
    def test_batch_matches_per_bar_signals(self):
        from app.services.bots.strategies_ml import (
            MlSignalBoostStrategy,
            get_ml_signal_store,
        )

        store = get_ml_signal_store()
        symbol = "TESTUSDT"
        tf = "1m"
        key = store._cache_key(symbol, None, tf)
        store._models[key] = _FakeGbm()
        store._metadata[key] = {
            "reverse_map": {"0": "BUY", "1": "NONE", "2": "SELL"},
            "feature_schema_version": 4,
            "feature_names": None,
        }
        store._mtime[key] = -1.0

        rows = [_make_bar(i) for i in range(48)]
        cfg = {
            "symbol": symbol,
            "model_symbol": symbol,
            "timeframe": tf,
            "min_confidence": 0.35,
            "batch_inference_size": 16,
            "calibration_gate_enabled": False,
        }

        batch_strat = MlSignalBoostStrategy(cfg)
        per_strat = MlSignalBoostStrategy(cfg)

        batch_out = batch_strat.precompute_backtest_signals(rows)
        per_out = [per_strat.evaluate(row) for row in rows]

        assert len(batch_out) == len(per_out) == len(rows)
        for i, (b, p) in enumerate(zip(batch_out, per_out)):
            assert b.get("signal") == p.get("signal"), f"bar {i} signal mismatch {b} vs {p}"
            if b.get("confidence") is not None and p.get("confidence") is not None:
                assert b["confidence"] == pytest.approx(p["confidence"], abs=1e-6), f"bar {i}"
            assert b.get("reject_reason") == p.get("reject_reason"), f"bar {i}"

    def test_batch_faster_than_naive_loop_on_mock(self):
        """Rough throughput check — batch path should beat per-bar store.predict."""
        from app.services.bots.strategies_ml import (
            MlSignalBoostStrategy,
            get_ml_signal_store,
        )

        store = get_ml_signal_store()
        symbol = "BENCHUSDT"
        tf = "1m"
        key = store._cache_key(symbol, None, tf)
        store._models[key] = _FakeGbm()
        store._metadata[key] = {
            "reverse_map": {"0": "BUY", "1": "NONE", "2": "SELL"},
            "feature_schema_version": 4,
        }
        store._mtime[key] = -1.0

        rows = [_make_bar(i, _symbol=symbol) for i in range(120)]
        cfg = {
            "symbol": symbol,
            "model_symbol": symbol,
            "timeframe": tf,
            "min_confidence": 0.35,
            "batch_inference_size": 64,
            "calibration_gate_enabled": False,
        }

        t0 = time.perf_counter()
        MlSignalBoostStrategy(cfg).precompute_backtest_signals(rows)
        batch_s = time.perf_counter() - t0

        t1 = time.perf_counter()
        s = MlSignalBoostStrategy(cfg)
        for row in rows:
            s.evaluate(row)
        per_s = time.perf_counter() - t1

        # Feature build dominates; allow slack but batch should not be much slower.
        assert batch_s < per_s * 1.5 + 0.05


class TestLstmBatchParityMocked:
    def test_lstm_batch_matches_per_window_predict(self):
        from app.services.bots.strategies_lstm import LstmModelStore, _logits_to_signal

        store = LstmModelStore()
        symbol = "LSTMTEST"
        tf = "1m"
        key = store._cache_key(symbol, None, tf)

        class _Sess:
            def run(self, _outs, feeds):
                x = feeds["input"]
                # (B, seq, F) → logits (B, 3) from mean feature
                means = x.mean(axis=(1, 2))
                logits = np.stack(
                    [means, np.zeros_like(means), -means],
                    axis=1,
                ).astype(np.float32)
                return [logits]

        store._sessions[key] = _Sess()
        store._scalers[key] = {
            "mean": [0.0] * 8,
            "std": [1.0] * 8,
        }
        store._metadata[key] = {
            "reverse_map": {"0": "BUY", "1": "NONE", "2": "SELL"},
        }
        store._mtime[key] = -1.0

        # Monkey-patch apply_scaler path by using matching feature width
        from app.services.bots import strategies_lstm as mod

        def _apply(sequences, scaler):
            return sequences.astype(np.float32)

        orig = mod.apply_scaler
        mod.apply_scaler = _apply
        try:
            rng = np.random.default_rng(0)
            windows = rng.normal(size=(40, 12, 8)).astype(np.float32)
            batch = store.predict_batch(
                symbol, windows, timeframe=tf, batch_size=7, research=False,
            )
            per = [
                store.predict(symbol, windows[i], timeframe=tf)
                for i in range(len(windows))
            ]
            assert len(batch) == len(per)
            for i, (b, p) in enumerate(zip(batch, per)):
                assert b is not None and p is not None
                assert b[0] == p[0], f"window {i}"
                assert b[1] == pytest.approx(p[1], abs=1e-5)
        finally:
            mod.apply_scaler = orig

        # sanity on helper
        sig, conf = _logits_to_signal(np.array([2.0, 0.0, 0.1]), {"0": "BUY", "1": "NONE", "2": "SELL"})
        assert sig == "BUY"
        assert conf > 0.5


class TestOrtProviders:
    def test_live_providers_cpu_only(self):
        from app.services.bots.ml_onnx_runtime import resolve_ort_providers

        assert resolve_ort_providers(research=False) == ["CPUExecutionProvider"]
        assert resolve_ort_providers(research=True, config={"backtest_use_gpu": False}) == [
            "CPUExecutionProvider"
        ]

    def test_live_aligned_disables_research_onnx(self):
        from app.services.bots.ml_onnx_runtime import backtest_research_inference

        assert backtest_research_inference({}) is False
        assert backtest_research_inference({"sim_mode": "live_aligned"}) is False
        assert backtest_research_inference({"sim_mode": "research"}) is True
        assert backtest_research_inference({"sim_mode": "research_fast"}) is True

    def test_auto_device_cpu_when_no_cuda_ep(self, monkeypatch):
        from app.services.bots import ml_onnx_runtime as ort_mod

        monkeypatch.setenv("BACKTEST_INFERENCE_DEVICE", "auto")
        ort_mod._cuda_ep_cached = False
        assert ort_mod.backtest_wants_gpu({}) is False
        assert ort_mod.resolve_ort_providers(research=True, config={}) == [
            "CPUExecutionProvider"
        ]

    def test_research_gpu_falls_back_without_cuda_ep(self, monkeypatch):
        from app.services.bots import ml_onnx_runtime as ort_mod

        monkeypatch.setenv("BACKTEST_INFERENCE_DEVICE", "cuda")

        class _Ort:
            @staticmethod
            def get_available_providers():
                return ["CPUExecutionProvider"]

        monkeypatch.setitem(__import__("sys").modules, "onnxruntime", _Ort())
        # Force import path used inside resolve
        providers = ort_mod.resolve_ort_providers(research=True, config={})
        assert providers == ["CPUExecutionProvider"]

    def test_cuda_session_flag_set_only_after_success(self, monkeypatch):
        """Failed CUDA session construct must not sticky-force ThreadPool auto."""
        from app.services.bots import ml_onnx_runtime as ort_mod

        monkeypatch.setenv("BACKTEST_INFERENCE_DEVICE", "cuda")
        ort_mod._cuda_session_created = False
        ort_mod._cuda_ep_cached = True

        class _Ort:
            @staticmethod
            def get_available_providers():
                return ["CUDAExecutionProvider", "CPUExecutionProvider"]

            class SessionOptions:
                pass

            class InferenceSession:
                def __init__(self, *args, **kwargs):
                    raise RuntimeError("cuda init failed")

        monkeypatch.setitem(__import__("sys").modules, "onnxruntime", _Ort())
        try:
            ort_mod.create_inference_session("dummy.onnx", research=True, config={})
            assert False, "expected create_inference_session to raise"
        except RuntimeError:
            pass
        assert ort_mod.cuda_session_loaded_in_process() is False

    def test_cuda_cache_tag_isolates_live_sessions(self, monkeypatch):
        from app.services.bots.ml_onnx_runtime import ort_provider_cache_tag
        from app.services.bots.strategies_lstm import LstmModelStore

        monkeypatch.setenv("BACKTEST_INFERENCE_DEVICE", "cuda")
        assert ort_provider_cache_tag(research=False, config={}) == ""
        assert ort_provider_cache_tag(research=True, config={}) == "|cuda"

        store = LstmModelStore()
        base = store._cache_key("SYM", None, "1m")
        live_key = store._session_key("SYM", None, "1m", research=False)
        gpu_key = store._session_key(
            "SYM", None, "1m", research=True, config={"backtest_use_gpu": True},
        )
        assert live_key == base
        assert gpu_key == f"{base}|cuda"
        assert live_key != gpu_key

        class _Sess:
            def __init__(self, tag):
                self.tag = tag

        store._sessions[gpu_key] = _Sess("cuda")
        store._mtime[gpu_key] = -1.0
        # Live lookup must not return the CUDA research session.
        assert store._ensure_loaded("SYM", timeframe="1m", research=False) is None


class TestFeatureMatrixEvalParity:
    def test_precompute_matrix_matches_evaluate_lookback(self):
        """Batch path must match live evaluate history (HTF + micro windows)."""
        from collections import deque

        from app.services.bots.ml_feature_engineering import (
            EVAL_FEATURE_LOOKBACK,
            EVAL_HISTORY_LOOKBACK,
            bar_to_signal_features,
            precompute_signal_feature_matrix,
            signal_features_to_vector,
        )

        assert EVAL_FEATURE_LOOKBACK == 24
        rows = [_make_bar(i) for i in range(80)]
        feat_mat = precompute_signal_feature_matrix(rows)
        hist: deque = deque(maxlen=EVAL_HISTORY_LOOKBACK + 1)
        for i, row in enumerate(rows):
            hist.append(dict(row))
            if len(hist) < 20:
                continue
            ev = signal_features_to_vector(
                bar_to_signal_features(row, lookback_rows=list(hist)[:-1])
            )
            assert np.allclose(ev, feat_mat[i], atol=1e-6), f"bar {i}"


class TestSlidingWindowBatches:
    def test_iter_batches_match_full_stack(self):
        from app.services.bots.ml_batch_inference import (
            iter_sliding_window_batches,
            stack_sliding_windows,
        )

        rng = np.random.default_rng(1)
        mat = rng.normal(size=(40, 6)).astype(np.float32)
        full_w, full_e = stack_sliding_windows(mat, 8)
        parts_w = []
        parts_e = []
        for w, e in iter_sliding_window_batches(mat, 8, batch_size=7):
            parts_w.append(w)
            parts_e.append(e)
        assert np.allclose(np.concatenate(parts_w, axis=0), full_w)
        assert np.array_equal(np.concatenate(parts_e, axis=0), full_e)


class TestTryPrecompute:
    def test_missing_method_returns_none(self):
        from app.services.bots.ml_batch_inference import try_precompute_signals

        assert try_precompute_signals(object(), [{"a": 1}], strategy_key="MACD_RSI") is None

    def test_length_mismatch_returns_none(self):
        from app.services.bots.ml_batch_inference import try_precompute_signals

        strat = MagicMock()
        strat.precompute_backtest_signals.return_value = [{"signal": "NONE"}]
        assert (
            try_precompute_signals(strat, [{"a": 1}, {"a": 2}], strategy_key="ML_SIGNAL_BOOST")
            is None
        )
