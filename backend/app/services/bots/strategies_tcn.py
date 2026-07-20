"""TCN_MULTI_HORIZON strategy — multi-horizon directional signal generator.

Loads a pre-trained TCN ONNX model and generates signals only when 5-bar,
15-bar, and 60-bar return forecasts all agree on direction. This consensus
approach eliminates whipsaw from conflicting timeframe signals.
"""

from __future__ import annotations

import logging
from collections import deque

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

        record_ml_inference_features(symbol, "TCN_MULTI_HORIZON", self._window[-1])

        window_array = np.array(list(self._window))
        store = get_tcn_store()
        pinned = self._cfg.get("model_version") or None
        returns = store.predict(symbol, window_array, model_version=pinned or None)

        if returns is None:
            return {"signal": "NONE"}

        ret_5, ret_15, ret_60 = float(returns[0]), float(returns[1]), float(returns[2])
        min_ret = float(self._cfg.get("min_return", 0.001))
        min_conf = float(self._cfg.get("min_confidence", 0.002))

        atr = df_row.get("ATR_14") or df_row.get("ATRr_14") or 0
        try:
            atr = float(atr)
        except (TypeError, ValueError):
            atr = 0.0

        # All horizons bullish
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

        # All horizons bearish
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
