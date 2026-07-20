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
    """Numerically stable softmax."""
    x = logits - logits.max()
    e = np.exp(x)
    return e / e.sum()


def _load_onnxruntime():
    """Lazy import onnxruntime."""
    try:
        import onnxruntime as ort
        return ort
    except ImportError:
        return None


# ── ONNX model store ─────────────────────────────────────────────────────


class LstmModelStore:
    """In-memory cache of ONNX inference sessions (per-symbol) — LRU + TTL."""

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
    def _cache_key(symbol: str, model_version: str | None) -> str:
        return f"{str(symbol).upper()}|{model_version or 'latest'}"

    def invalidate(self, symbol: str | None = None) -> None:
        if symbol:
            prefix = str(symbol).upper() + "|"
            self._lru.discard_prefix(str(symbol).upper())
            self._lru.discard_prefix(prefix)
            for d in (self._sessions, self._metadata, self._scalers, self._mtime):
                for k in list(d.keys()):
                    if k == str(symbol).upper() or k.startswith(prefix):
                        d.pop(k, None)
        else:
            self._lru.clear()
            self._sessions.clear()
            self._metadata.clear()
            self._scalers.clear()
            self._mtime.clear()

    def get_metadata(self, symbol: str, model_version: str | None = None) -> dict[str, Any] | None:
        self._ensure_loaded(symbol, model_version=model_version)
        return self._metadata.get(self._cache_key(symbol, model_version))

    def predict(
        self,
        symbol: str,
        window: np.ndarray,
        *,
        model_version: str | None = None,
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
        key = self._cache_key(symbol, model_version)
        session = self._ensure_loaded(symbol, model_version=model_version)
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
            proba = _softmax(logits)
            pred_idx = int(np.argmax(proba))
            confidence = float(proba[pred_idx])

            meta = self._metadata.get(key) or {}
            reverse_map = meta.get("reverse_map", REVERSE_MAP)
            # Handle both string and int keys in reverse_map
            signal = reverse_map.get(str(pred_idx), "NONE")
            return signal, confidence
        except Exception as exc:
            logger.warning("LSTM predict failed for %s: %s", symbol, exc)
            return None

    def _ensure_loaded(self, symbol: str, model_version: str | None = None):
        from app.services.bots.ml_model_artifacts import resolve_model_dir

        key = self._cache_key(symbol, model_version)
        load_dir = resolve_model_dir(_model_dir(symbol), model_version)
        onnx_path = os.path.join(load_dir, "lstm_direction.onnx")
        meta_path = os.path.join(load_dir, "metadata.json")

        if not os.path.isfile(onnx_path) or not os.path.isfile(meta_path):
            return None

        mtime = os.path.getmtime(onnx_path)
        if key in self._sessions and self._mtime.get(key) == mtime:
            self._lru.touch(key)
            return self._sessions[key]

        ort = _load_onnxruntime()
        if ort is None:
            logger.debug("onnxruntime not installed — LSTM strategy unavailable")
            return None

        try:
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            if int(meta.get("feature_schema_version", 0)) != SIGNAL_FEATURE_VERSION:
                logger.warning(
                    "LSTM model schema mismatch for %s — retrain required", key
                )
                return None

            session = ort.InferenceSession(
                onnx_path,
                providers=["CPUExecutionProvider"],
            )
            scaler = load_scaler(symbol, model_dir=load_dir)
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
    - No trained model exists for the symbol
    - Insufficient bars in the lookback window

    Config keys:
        min_confidence (float): Minimum probability to emit signal (default 0.55).
        lookback (int): Sequence length for the LSTM (default 60).
        model_symbol (str): Override symbol for model lookup.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self._cfg = merge_strategy_config("LSTM_DIRECTION", config or {})
        self._lookback = int(self._cfg.get("lookback", 60))
        # Window stores raw feature vectors (unscaled — scaling happens at predict time)
        self._window: deque = deque(maxlen=self._lookback)
        # Lookback for bar_to_signal_features rolling computation
        self._bar_history: deque = deque(maxlen=25)

    def evaluate(self, df_row) -> dict:
        # Maintain bar history for rolling feature computation
        self._bar_history.append(dict(df_row))

        # Need enough bar history for feature computation
        if len(self._bar_history) < 20:
            return {"signal": "NONE"}

        # Extract features for this bar
        lookback_rows = list(self._bar_history)[:-1]
        features = bar_to_signal_features(df_row, lookback_rows=lookback_rows)
        vec = signal_features_to_vector(features)
        self._window.append(vec)

        # Need full LSTM window
        if len(self._window) < self._lookback:
            return {"signal": "NONE"}

        # Resolve symbol
        symbol = self._cfg.get("model_symbol") or str(df_row.get("_symbol", ""))
        if not symbol:
            symbol = str(self.config.get("symbol", "")).upper()
        if not symbol:
            return {"signal": "NONE"}

        from app.services.bots.ml_feature_drift import record_ml_inference_features

        record_ml_inference_features(symbol, "LSTM_DIRECTION", vec)

        # Build window array and predict
        window_array = np.array(list(self._window))  # (lookback, N_FEATURES)
        store = get_lstm_store()
        pinned = self._cfg.get("model_version") or None
        result = store.predict(symbol, window_array, model_version=pinned or None)

        if result is None:
            return {"signal": "NONE"}

        signal, confidence = result
        threshold = float(self._cfg.get("min_confidence", 0.55))

        atr = df_row.get("ATR_14") or df_row.get("ATRr_14") or 0
        try:
            atr = float(atr)
        except (TypeError, ValueError):
            atr = 0.0

        if signal in ("BUY", "SELL") and confidence >= threshold:
            return apply_ml_meta_label_gate({
                "signal": signal,
                "confidence": round(confidence, 4),
                "stop_loss_distance": atr * 1.5 if atr > 0 else None,
                "model_type": "lstm",
            }, df_row, self._cfg)

        return {"signal": "NONE"}
