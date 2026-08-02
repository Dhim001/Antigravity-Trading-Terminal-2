"""TRANSFORMER_SIGNAL strategy — attention-based directional signal generator.

Uses a pre-trained Transformer encoder ONNX model with 60-bar lookback
to predict BUY/SELL/NONE.  Same sliding-window approach as LSTM but with
self-attention for better long-range dependency capture.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

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

    def _model_timeframe(self) -> str:
        from app.services.bots.ml_model_artifacts import normalize_model_timeframe

        return normalize_model_timeframe(
            self._cfg.get("timeframe") or self.config.get("timeframe")
        )

    def _format_result(self, df_row, result, *, symbol: str = "", timeframe: str = "") -> dict:
        if result is None:
            return {
                "signal": "NONE",
                "reject_reason": "ml_model_missing",
                "reject_detail": (
                    f"No trained TRANSFORMER_SIGNAL model for {symbol} @ {timeframe}"
                ),
            }
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
        out: list[dict] = [{"signal": "NONE"} for _ in range(n)]
        if n < 20:
            return out

        symbol = str(self._cfg.get("model_symbol") or "").strip().upper()
        if not symbol:
            symbol = str(
                (rows[-1] or {}).get("_symbol") or self.config.get("symbol") or ""
            ).strip().upper()
        if not symbol:
            return out

        lookback = self._lookback
        feat_mat = precompute_signal_feature_matrix(
            rows,
            feature_lookback=EVAL_FEATURE_LOOKBACK,
            cancel_cb=cancel_cb,
            progress_cb=progress_cb,
        )
        vecs = feat_mat[19:]
        if len(vecs) < lookback:
            return out

        from app.services.bots.ml_onnx_runtime import backtest_research_inference

        store = get_transformer_store()
        pinned = self._cfg.get("model_version") or None
        tf = self._model_timeframe()
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
                out[row_i] = self._format_result(
                    rows[row_i], pred, symbol=symbol, timeframe=tf,
                )

        self._bar_history.clear()
        self._window.clear()
        for row in rows[-25:]:
            self._bar_history.append(dict(row))
        for vec in vecs[-lookback:]:
            self._window.append(vec)
        return out

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
        tf = self._model_timeframe()
        result = store.predict(
            symbol, window_array, model_version=pinned or None, timeframe=tf,
        )
        return self._format_result(df_row, result, symbol=symbol, timeframe=tf)
