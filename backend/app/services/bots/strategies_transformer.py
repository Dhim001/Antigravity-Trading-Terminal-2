"""TRANSFORMER_SIGNAL strategy — attention-based directional signal generator.

Uses a pre-trained Transformer encoder ONNX model with 60-bar lookback
to predict BUY/SELL/NONE.  Same sliding-window approach as LSTM but with
self-attention for better long-range dependency capture.
"""

from __future__ import annotations

import logging
from collections import deque

import numpy as np

from app.services.bots.indicators import merge_strategy_config
from app.services.bots.ml_feature_engineering import bar_to_signal_features, signal_features_to_vector
from app.services.bots.ml_signal_gates import apply_ml_meta_label_gate
from app.services.bots.ml_transformer_trainer import get_transformer_store
from app.services.bots.strategies import BaseStrategy

logger = logging.getLogger(__name__)


class TransformerSignalStrategy(BaseStrategy):
    """Transformer-based directional signal generator.

    Config:
        lookback (int): Sequence length (default 60).
        min_confidence (float): Minimum probability to emit signal (default 0.55).
        model_symbol (str): Override symbol for model lookup.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self._cfg = merge_strategy_config("TRANSFORMER_SIGNAL", config or {})
        self._lookback = int(self._cfg.get("lookback", 60))
        self._window: deque = deque(maxlen=self._lookback)
        self._bar_history: deque = deque(maxlen=25)

    def evaluate(self, df_row) -> dict:
        self._bar_history.append(dict(df_row))

        if len(self._bar_history) < 20:
            return {"signal": "NONE"}

        lookback_rows = list(self._bar_history)[:-1]
        features = bar_to_signal_features(df_row, lookback_rows=lookback_rows)
        self._window.append(signal_features_to_vector(features))

        if len(self._window) < self._lookback:
            return {"signal": "NONE"}

        symbol = self._cfg.get("model_symbol") or str(df_row.get("_symbol", ""))
        if not symbol:
            symbol = str(self.config.get("symbol", "")).upper()
        if not symbol:
            return {"signal": "NONE"}

        from app.services.bots.ml_feature_drift import record_ml_inference_features

        record_ml_inference_features(symbol, "TRANSFORMER_SIGNAL", self._window[-1])

        window_array = np.array(list(self._window))
        store = get_transformer_store()
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
                "model_type": "transformer",
            }, df_row, self._cfg)

        return {"signal": "NONE"}
