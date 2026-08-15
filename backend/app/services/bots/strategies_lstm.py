"""LSTM_DIRECTION strategy — ONNX-based LSTM signal generator.

Loads a pre-trained ONNX model exported by ml_lstm_trainer.py and generates
BUY/SELL/NONE signals from 60-bar sliding windows of normalized features.

No PyTorch dependency at inference time — only onnxruntime.
Falls back to NONE if onnxruntime is not installed or no model is available.
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import deque
from typing import Any

import numpy as np

from app.services.bots.indicators import merge_strategy_config
from app.services.bots.ml_feature_engineering import (
    SIGNAL_FEATURE_NAMES,
    SIGNAL_FEATURE_VERSION,
    bar_to_signal_features,
    signal_features_to_vector,
)
from app.services.bots.ml_lstm_trainer import (
    REVERSE_MAP,
    _model_dir,
    apply_scaler,
    load_scaler,
)
from app.services.bots.ml_signal_gates import apply_ml_meta_label_gate
from app.services.bots.strategies import BaseStrategy

logger = logging.getLogger(__name__)

N_FEATURES = len(SIGNAL_FEATURE_NAMES)


def _softmax(logits: np.ndarray) -> np.ndarray:
    """Numerically stable softmax (1-D or last-axis for batches)."""
    arr = np.asarray(logits, dtype=np.float64)
    if arr.ndim == 1:
        x = arr - arr.max()
        e = np.exp(x)
        return e / e.sum()
    x = arr - arr.max(axis=-1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=-1, keepdims=True)


def _load_onnxruntime():
    """Lazy import onnxruntime."""
    try:
        import onnxruntime as ort
        return ort
    except ImportError:
        return None


def _logits_to_signal(logits_row: np.ndarray, reverse_map: dict) -> tuple[str, float]:
    proba = _softmax(logits_row)
    named: dict[str, float] = {}
    for i, p in enumerate(proba):
        name = reverse_map.get(str(i), reverse_map.get(i, f"cls_{i}"))
        named[str(name).upper()] = float(p)
    buy_p = float(named.get("BUY", 0.0))
    sell_p = float(named.get("SELL", 0.0))
    if buy_p >= sell_p:
        return "BUY", buy_p
    return "SELL", sell_p


# ── ONNX model store ─────────────────────────────────────────────────────


class LstmModelStore:
    """In-memory cache of ONNX inference sessions (per symbol×timeframe) — LRU + TTL."""

    def __init__(self) -> None:
        from app.config import ML_MODEL_CACHE_MAX, ML_MODEL_CACHE_TTL_SEC
        from app.services.bots.model_store_lru import bind_dict_cache

        self._sessions: dict[str, Any] = {}
        self._metadata: dict[str, dict[str, Any]] = {}
        self._scalers: dict[str, dict[str, list[float]]] = {}
        self._mtime: dict[str, float] = {}
        self._lru = bind_dict_cache(
            self._sessions, self._metadata, self._scalers, self._mtime,
            max_entries=ML_MODEL_CACHE_MAX,
            ttl_sec=ML_MODEL_CACHE_TTL_SEC,
        )

    @staticmethod
    def _cache_key(
        symbol: str,
        model_version: str | None,
        timeframe: str | None = None,
    ) -> str:
        from app.services.bots.ml_model_artifacts import model_storage_key

        return f"{model_storage_key(symbol, timeframe)}|{model_version or 'latest'}"

    @staticmethod
    def _session_key(
        symbol: str,
        model_version: str | None,
        timeframe: str | None = None,
        *,
        research: bool = False,
        config: dict | None = None,
    ) -> str:
        """Session cache key — CUDA research sessions use a ``|cuda`` suffix."""
        from app.services.bots.ml_onnx_runtime import ort_provider_cache_tag

        base = LstmModelStore._cache_key(symbol, model_version, timeframe)
        return f"{base}{ort_provider_cache_tag(research=research, config=config)}"

    def invalidate(self, symbol: str | None = None, *, timeframe: str | None = None) -> None:
        from app.services.bots.ml_model_artifacts import model_storage_key, safe_symbol_key

        if symbol:
            if timeframe is not None:
                sk = model_storage_key(symbol, timeframe)
                prefixes = (sk + "|", sk)
            else:
                sk = safe_symbol_key(symbol)
                prefixes = (sk + "|", sk + "__")
            for p in prefixes:
                self._lru.discard_prefix(p)
            for d in (self._sessions, self._metadata, self._scalers, self._mtime):
                for k in list(d.keys()):
                    if any(k == p.rstrip("|") or k.startswith(p) for p in prefixes):
                        d.pop(k, None)
        else:
            self._lru.clear()
            self._sessions.clear()
            self._metadata.clear()
            self._scalers.clear()
            self._mtime.clear()

    def get_metadata(
        self,
        symbol: str,
        model_version: str | None = None,
        *,
        timeframe: str | None = None,
    ) -> dict[str, Any] | None:
        self._ensure_loaded(symbol, model_version=model_version, timeframe=timeframe)
        return self._metadata.get(self._cache_key(symbol, model_version, timeframe))

    def predict(
        self,
        symbol: str,
        window: np.ndarray,
        *,
        model_version: str | None = None,
        timeframe: str | None = None,
    ) -> tuple[str, float] | None:
        """Run ONNX inference on a feature window.

        Parameters
        ----------
        symbol : str
        window : np.ndarray of shape (seq_len, n_features)

        Returns
        -------
        tuple of (signal: "BUY"|"SELL"|"NONE", confidence: float) or None.
        """
        key = self._session_key(symbol, model_version, timeframe)
        session = self._ensure_loaded(
            symbol, model_version=model_version, timeframe=timeframe,
        )
        if session is None:
            return None

        scaler = self._scalers.get(key)
        if scaler is None:
            return None

        # Normalize and reshape
        window_scaled = apply_scaler(
            window.astype(np.float32).reshape(1, *window.shape),
            scaler,
        )

        try:
            logits = session.run(None, {"input": window_scaled})[0][0]
            meta = self._metadata.get(key) or {}
            reverse_map = meta.get("reverse_map") or REVERSE_MAP
            # Prefer directional classes over argmax — NONE often dominates the
            # softmax (~0.4–0.55) and would zero out all trades. Confidence is
            # P(chosen side); evaluate() applies min_confidence.
            return _logits_to_signal(logits, reverse_map)
        except Exception as exc:
            logger.warning("LSTM predict failed for %s: %s", symbol, exc)
            return None

    def predict_batch(
        self,
        symbol: str,
        windows: np.ndarray,
        *,
        model_version: str | None = None,
        timeframe: str | None = None,
        batch_size: int = 512,
        cancel_cb: Any | None = None,
        research: bool = False,
        config: dict | None = None,
    ) -> list[tuple[str, float] | None]:
        """Batched ONNX inference — ``windows`` shape ``(N, seq, F)``.

        ``research=False`` (default) uses live CPU sessions. Pass
        ``research=True`` only for research/research_fast backtests.
        """
        n = int(windows.shape[0]) if windows is not None else 0
        if n == 0:
            return []
        key = self._session_key(
            symbol, model_version, timeframe, research=research, config=config,
        )
        session = self._ensure_loaded(
            symbol,
            model_version=model_version,
            timeframe=timeframe,
            research=research,
            config=config,
        )
        if session is None:
            return [None] * n
        scaler = self._scalers.get(key)
        if scaler is None:
            return [None] * n
        meta = self._metadata.get(key) or {}
        reverse_map = meta.get("reverse_map") or REVERSE_MAP
        out: list[tuple[str, float] | None] = [None] * n
        bs = max(32, int(batch_size or 512))
        for start in range(0, n, bs):
            if cancel_cb is not None and cancel_cb():
                raise InterruptedError("ml_batch_cancel_requested")
            end = min(start + bs, n)
            chunk = windows[start:end].astype(np.float32)
            try:
                scaled = apply_scaler(chunk, scaler)
                logits = session.run(None, {"input": scaled})[0]
            except Exception as exc:
                logger.warning(
                    "LSTM batch predict failed for %s [%s:%s]: %s",
                    symbol, start, end, exc,
                )
                continue
            for j, row in enumerate(logits):
                out[start + j] = _logits_to_signal(row, reverse_map)
        return out

    def _ensure_loaded(
        self,
        symbol: str,
        model_version: str | None = None,
        *,
        timeframe: str | None = None,
        research: bool = False,
        config: dict | None = None,
    ):
        from app.services.bots.ml_model_artifacts import resolve_model_dir

        key = self._session_key(
            symbol, model_version, timeframe, research=research, config=config,
        )
        # Session-injected models (mtime sentinel -1) skip disk reload.
        if key in self._sessions and self._mtime.get(key) == -1.0:
            self._lru.touch(key)
            return self._sessions[key]

        load_dir = resolve_model_dir(_model_dir(symbol, timeframe), model_version)
        onnx_path = os.path.join(load_dir, "lstm_direction.onnx")
        meta_path = os.path.join(load_dir, "metadata.json")

        if not os.path.isfile(onnx_path) or not os.path.isfile(meta_path):
            return None

        mtime = os.path.getmtime(onnx_path)
        if key in self._sessions and self._mtime.get(key) == mtime:
            self._lru.touch(key)
            return self._sessions[key]

        if _load_onnxruntime() is None:
            logger.debug("onnxruntime not installed — LSTM strategy unavailable")
            return None

        try:
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            from app.services.bots.ml_feature_engineering import is_compatible_feature_schema
            if not is_compatible_feature_schema(int(meta.get("feature_schema_version", 0))):
                logger.warning(
                    "LSTM model schema mismatch for %s — retrain required", key
                )
                return None

            from app.services.bots.ml_onnx_runtime import create_inference_session

            session = create_inference_session(
                onnx_path, research=research, config=config,
            )
            scaler = load_scaler(symbol, model_dir=load_dir, timeframe=timeframe)
            if scaler is None:
                logger.warning("LSTM scaler missing for %s — retrain required", key)
                return None

        except Exception as exc:
            logger.warning("LSTM model load failed for %s: %s", key, exc)
            return None

        self._sessions[key] = session
        self._metadata[key] = meta
        self._scalers[key] = scaler
        self._mtime[key] = mtime
        self._lru.touch(key)
        return session


_lstm_store = LstmModelStore()


def get_lstm_store() -> LstmModelStore:
    return _lstm_store


# ── Strategy class ────────────────────────────────────────────────────────


class LstmDirectionStrategy(BaseStrategy):
    """LSTM-based directional signal generator.

    Maintains a sliding window of feature vectors and runs ONNX inference
    to predict BUY/SELL/NONE.  Falls back to NONE if:
    - onnxruntime is not installed
    - No trained model exists for the symbol×timeframe
    - Insufficient bars in the lookback window

    Config keys:
        min_confidence (float): Minimum probability to emit signal (default 0.55).
        lookback (int): Sequence length for the LSTM (default 60).
        model_symbol (str): Override symbol for model lookup.
        timeframe (str): Bar TF matching the trained model (default 1m).
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self._cfg = merge_strategy_config("LSTM_DIRECTION", config or {})
        self._lookback = int(self._cfg.get("lookback", 60))
        # Window stores raw feature vectors (unscaled — scaling happens at predict time)
        self._window: deque = deque(maxlen=self._lookback)
        # Lookback for bar_to_signal_features rolling computation
        from app.services.bots.ml_feature_engineering import EVAL_HISTORY_LOOKBACK
        self._bar_history: deque = deque(maxlen=EVAL_HISTORY_LOOKBACK + 1)
        self._lookback_synced = False

    def _model_timeframe(self) -> str:
        from app.services.bots.ml_model_artifacts import normalize_model_timeframe

        return normalize_model_timeframe(
            self._cfg.get("timeframe") or self.config.get("timeframe")
        )

    def _format_lstm_result(
        self,
        df_row,
        result: tuple[str, float] | None,
        *,
        symbol: str,
        timeframe: str,
    ) -> dict:
        if result is None:
            ort = _load_onnxruntime()
            detail = (
                f"No trained LSTM_DIRECTION model for {symbol} @ {timeframe}"
                if ort is not None
                else "onnxruntime not installed — pip install onnxruntime>=1.18.0"
            )
            return {
                "signal": "NONE",
                "reject_reason": "ml_model_missing",
                "reject_detail": detail,
            }

        signal, confidence = result
        threshold = float(self._cfg.get("min_confidence", 0.55))
        conf = round(float(confidence), 4)

        atr = df_row.get("ATR_14") or df_row.get("ATRr_14") or 0
        try:
            atr = float(atr)
        except (TypeError, ValueError):
            atr = 0.0

        if signal in ("BUY", "SELL") and conf >= threshold:
            return apply_ml_meta_label_gate({
                "signal": signal,
                "raw_signal": signal,
                "confidence": conf,
                "stop_loss_distance": atr * 1.5 if atr > 0 else None,
                "model_type": "lstm",
            }, df_row, self._cfg)

        if signal in ("BUY", "SELL") and conf < threshold:
            return {
                "signal": "NONE",
                "raw_signal": signal,
                "confidence": conf,
                "reject_reason": "ml_confidence",
                "reject_detail": f"confidence {conf:.2f} below min {threshold:.2f}",
            }

        return {
            "signal": "NONE",
            "raw_signal": signal if signal in ("BUY", "SELL", "NONE") else "NONE",
            "confidence": conf,
        }

    def precompute_backtest_signals(
        self,
        rows: list[dict],
        *,
        cancel_cb: Any | None = None,
        progress_cb: Any | None = None,
    ) -> list[dict]:
        """Batch ONNX windows for backtests (matches evaluate warmup timeline)."""
        from app.services.bots.ml_batch_inference import (
            inference_batch_size,
            iter_sliding_window_batches,
        )
        from app.services.bots.ml_feature_engineering import (
            EVAL_FEATURE_LOOKBACK,
            precompute_signal_feature_matrix,
        )

        n = len(rows)
        out: list[dict] = [{"signal": "NONE", "reject_reason": "ml_warmup"} for _ in range(n)]
        if n < 20:
            for i in range(n):
                out[i] = {
                    "signal": "NONE",
                    "reject_reason": "ml_warmup",
                    "reject_detail": "Need >= 20 bars of lookback for LSTM features",
                }
            return out

        symbol = str(self._cfg.get("model_symbol") or "").strip().upper()
        if not symbol:
            symbol = str(
                (rows[-1] or {}).get("_symbol") or self.config.get("symbol") or ""
            ).strip().upper()
        if not symbol:
            miss = {
                "signal": "NONE",
                "reject_reason": "ml_symbol_missing",
                "reject_detail": "No model_symbol / symbol for LSTM model lookup",
            }
            return [dict(miss) for _ in range(n)]

        store = get_lstm_store()
        pinned = self._cfg.get("model_version") or None
        tf = self._model_timeframe()
        meta = store.get_metadata(symbol, model_version=pinned or None, timeframe=tf)
        lookback = self._lookback
        if meta:
            trained_lb = int((meta.get("config") or {}).get("lookback") or lookback)
            if trained_lb > 0:
                lookback = trained_lb
                self._lookback = trained_lb
                self._window = deque(maxlen=trained_lb)
            self._lookback_synced = True

        feat_mat = precompute_signal_feature_matrix(
            rows,
            feature_lookback=EVAL_FEATURE_LOOKBACK,
            cancel_cb=cancel_cb,
            progress_cb=progress_cb,
        )
        # evaluate() only appends feature vectors once bar_history >= 20 (idx 19+).
        vecs = feat_mat[19:]
        for i in range(min(19, n)):
            out[i] = {
                "signal": "NONE",
                "reject_reason": "ml_warmup",
                "reject_detail": "Need >= 20 bars of lookback for LSTM features",
            }
        warm_end = min(n, 19 + lookback - 1)
        for i in range(19, warm_end):
            out[i] = {
                "signal": "NONE",
                "reject_reason": "ml_warmup",
                "reject_detail": (
                    f"Need {lookback} bars in LSTM window ({i - 18} so far)"
                ),
            }
        if len(vecs) < lookback:
            return out

        from app.services.bots.ml_onnx_runtime import backtest_research_inference

        # live_aligned must use CPU sessions (research=False) to match evaluate().
        research = backtest_research_inference(self._cfg)
        batch_size = inference_batch_size(self._cfg)
        for windows, ends in iter_sliding_window_batches(vecs, lookback, batch_size):
            if cancel_cb is not None and cancel_cb():
                raise InterruptedError("ml_batch_cancel_requested")
            preds = store.predict_batch(
                symbol,
                windows,
                model_version=pinned or None,
                timeframe=tf,
                batch_size=batch_size,
                cancel_cb=cancel_cb,
                research=research,
                config=self._cfg,
            )
            for j, pred in enumerate(preds):
                row_i = 19 + int(ends[j])
                out[row_i] = self._format_lstm_result(
                    rows[row_i], pred, symbol=symbol, timeframe=tf,
                )

        self._bar_history.clear()
        self._window.clear()
        from app.services.bots.ml_feature_engineering import EVAL_HISTORY_LOOKBACK
        hist_n = EVAL_HISTORY_LOOKBACK + 1
        for row in rows[-hist_n:]:
            self._bar_history.append(dict(row))
        if len(vecs) >= lookback:
            for vec in vecs[-lookback:]:
                self._window.append(vec)
        return out

    def evaluate(self, df_row) -> dict:
        # Maintain bar history for rolling feature computation
        self._bar_history.append(dict(df_row))

        # Need enough bar history for feature computation
        if len(self._bar_history) < 20:
            return {
                "signal": "NONE",
                "reject_reason": "ml_warmup",
                "reject_detail": "Need >= 20 bars of lookback for LSTM features",
            }

        # Extract features for this bar
        lookback_rows = list(self._bar_history)[:-1]
        features = bar_to_signal_features(df_row, lookback_rows=lookback_rows)
        vec = signal_features_to_vector(features)
        self._window.append(vec)

        # Need full LSTM window
        if len(self._window) < self._lookback:
            return {
                "signal": "NONE",
                "reject_reason": "ml_warmup",
                "reject_detail": f"Need {self._lookback} bars in LSTM window ({len(self._window)} so far)",
            }

        # Resolve symbol
        symbol = str(self._cfg.get("model_symbol") or "").strip().upper()
        if not symbol:
            symbol = str(df_row.get("_symbol") or self.config.get("symbol") or "").strip().upper()
        if not symbol:
            return {
                "signal": "NONE",
                "reject_reason": "ml_symbol_missing",
                "reject_detail": "No model_symbol / symbol for LSTM model lookup",
            }

        from app.services.bots.ml_feature_drift import record_ml_inference_features

        record_ml_inference_features(symbol, "LSTM_DIRECTION", vec)

        store = get_lstm_store()
        pinned = self._cfg.get("model_version") or None
        tf = self._model_timeframe()

        # Align window length with the trained artifact once (config may disagree).
        if not self._lookback_synced:
            meta = store.get_metadata(symbol, model_version=pinned or None, timeframe=tf)
            if meta:
                trained_lb = int((meta.get("config") or {}).get("lookback") or self._lookback)
                if trained_lb > 0 and trained_lb != self._lookback:
                    self._lookback = trained_lb
                    self._window = deque(list(self._window)[-trained_lb:], maxlen=trained_lb)
                self._lookback_synced = True
                if len(self._window) < self._lookback:
                    return {
                        "signal": "NONE",
                        "reject_reason": "ml_warmup",
                        "reject_detail": (
                            f"Need {self._lookback} bars in LSTM window "
                            f"({len(self._window)} so far; matched trained lookback)"
                        ),
                    }

        window_array = np.array(list(self._window))  # (lookback, N_FEATURES)
        result = store.predict(
            symbol, window_array, model_version=pinned or None, timeframe=tf,
        )
        return self._format_lstm_result(df_row, result, symbol=symbol, timeframe=tf)
