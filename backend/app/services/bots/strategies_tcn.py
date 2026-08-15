"""TCN_MULTI_HORIZON strategy — multi-horizon directional signal generator.

Loads a pre-trained TCN ONNX model and generates signals only when 5-bar,
15-bar, and 60-bar return forecasts all agree on direction. This consensus
approach eliminates whipsaw from conflicting timeframe signals.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

import numpy as np

from app.services.bots.indicators import merge_strategy_config
from app.services.bots.ml_feature_engineering import (
    bar_to_signal_features,
    signal_features_to_vector,
)
from app.services.bots.ml_signal_gates import apply_ml_meta_label_gate
from app.services.bots.ml_tcn_trainer import get_tcn_store
from app.services.bots.strategies import BaseStrategy

logger = logging.getLogger(__name__)


class TcnMultiHorizonStrategy(BaseStrategy):
    """TCN-based multi-horizon signal generator.

    Emits BUY/SELL only when all 3 return horizons (5, 15, 60 bars)
    agree on direction with sufficient magnitude.

    Config:
        lookback (int): Sequence length (default 120).
        min_return (float): Minimum return magnitude to count as directional (default 0.001).
        min_confidence (float): Minimum average magnitude across horizons (default 0.002).
        model_symbol (str): Override symbol for model lookup.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self._cfg = merge_strategy_config("TCN_MULTI_HORIZON", config or {})
        self._lookback = int(self._cfg.get("lookback", 120))
        self._window: deque = deque(maxlen=self._lookback)
        from app.services.bots.ml_feature_engineering import EVAL_HISTORY_LOOKBACK
        self._bar_history: deque = deque(maxlen=EVAL_HISTORY_LOOKBACK + 1)

    def _model_timeframe(self) -> str:
        from app.services.bots.ml_model_artifacts import normalize_model_timeframe

        return normalize_model_timeframe(
            self._cfg.get("timeframe") or self.config.get("timeframe")
        )

    def _returns_to_signal(self, df_row, returns, *, symbol: str, timeframe: str) -> dict:
        if returns is None:
            return {
                "signal": "NONE",
                "reject_reason": "ml_model_missing",
                "reject_detail": f"No trained TCN_MULTI_HORIZON model for {symbol} @ {timeframe}",
            }

        ret_5, ret_15, ret_60 = float(returns[0]), float(returns[1]), float(returns[2])
        min_ret = float(self._cfg.get("min_return", 0.001))
        min_conf = float(self._cfg.get("min_confidence", 0.002))

        atr = df_row.get("ATR_14") or df_row.get("ATRr_14") or 0
        try:
            atr = float(atr)
        except (TypeError, ValueError):
            atr = 0.0

        if ret_5 > min_ret and ret_15 > min_ret and ret_60 > min_ret:
            avg_mag = (abs(ret_5) + abs(ret_15) + abs(ret_60)) / 3
            if avg_mag >= min_conf:
                return apply_ml_meta_label_gate({
                    "signal": "BUY",
                    "confidence": round(min(avg_mag * 100, 1.0), 4),
                    "ret_5": round(ret_5, 6),
                    "ret_15": round(ret_15, 6),
                    "ret_60": round(ret_60, 6),
                    "stop_loss_distance": atr * 1.5 if atr > 0 else None,
                    "model_type": "tcn",
                }, df_row, self._cfg)

        if ret_5 < -min_ret and ret_15 < -min_ret and ret_60 < -min_ret:
            avg_mag = (abs(ret_5) + abs(ret_15) + abs(ret_60)) / 3
            if avg_mag >= min_conf:
                return apply_ml_meta_label_gate({
                    "signal": "SELL",
                    "confidence": round(min(avg_mag * 100, 1.0), 4),
                    "ret_5": round(ret_5, 6),
                    "ret_15": round(ret_15, 6),
                    "ret_60": round(ret_60, 6),
                    "stop_loss_distance": atr * 1.5 if atr > 0 else None,
                    "model_type": "tcn",
                }, df_row, self._cfg)

        return {"signal": "NONE"}

    def precompute_backtest_signals(
        self,
        rows: list[dict],
        *,
        cancel_cb: Any | None = None,
        progress_cb: Any | None = None,
    ) -> list[dict]:
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

        def _feat_progress(done: int, total: int) -> None:
            if progress_cb is None:
                return
            # Reserve the second half of the precompute span for ONNX batches so
            # progress stays monotonic after the feature matrix finishes.
            try:
                progress_cb(int(done), max(1, int(total) * 2))
            except Exception:
                pass

        feat_mat = precompute_signal_feature_matrix(
            rows,
            feature_lookback=EVAL_FEATURE_LOOKBACK,
            cancel_cb=cancel_cb,
            progress_cb=_feat_progress if progress_cb else None,
        )
        vecs = feat_mat[19:]
        if len(vecs) < lookback:
            return out

        store = get_tcn_store()
        pinned = self._cfg.get("model_version") or None
        tf = self._model_timeframe()
        from app.services.bots.ml_onnx_runtime import backtest_research_inference

        # live_aligned must use CPU sessions (research=False) to match evaluate().
        research = backtest_research_inference(self._cfg)
        batch_size = inference_batch_size(self._cfg)
        infer_total = max(1, len(vecs) - lookback + 1)
        infer_done = 0
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
            for j, returns in enumerate(preds):
                row_i = 19 + int(ends[j])
                out[row_i] = self._returns_to_signal(
                    rows[row_i], returns, symbol=symbol, timeframe=tf,
                )
            infer_done += len(ends)
            if progress_cb is not None:
                try:
                    # Features occupied [0, n) of total 2n; inference fills [n, 2n].
                    progress_cb(n + int((infer_done / infer_total) * n), 2 * n)
                except Exception:
                    pass

        self._bar_history.clear()
        self._window.clear()
        from app.services.bots.ml_feature_engineering import EVAL_HISTORY_LOOKBACK
        hist_n = EVAL_HISTORY_LOOKBACK + 1
        for row in rows[-hist_n:]:
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

        record_ml_inference_features(symbol, "TCN_MULTI_HORIZON", self._window[-1])

        window_array = np.array(list(self._window))
        store = get_tcn_store()
        pinned = self._cfg.get("model_version") or None
        tf = self._model_timeframe()
        returns = store.predict(
            symbol, window_array, model_version=pinned or None, timeframe=tf,
        )
        return self._returns_to_signal(df_row, returns, symbol=symbol, timeframe=tf)
