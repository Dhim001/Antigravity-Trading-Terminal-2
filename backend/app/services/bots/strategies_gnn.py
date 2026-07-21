"""GNN_CROSS_ASSET strategy — cross-asset signal propagation via Graph Attention Network.

Queries the GNN model with features from all symbols in a basket/watchlist
and returns per-symbol directional signals. Captures lead-lag relationships
across correlated assets.

Falls back to NONE if model not available or only single-symbol context.
"""

from __future__ import annotations

import logging
from collections import deque

import numpy as np

from app.services.bots.indicators import merge_strategy_config
from app.services.bots.ml_feature_engineering import bar_to_signal_features, signal_features_to_vector
from app.services.bots.ml_gnn_trainer import get_gnn_store
from app.services.bots.ml_signal_gates import apply_ml_meta_label_gate
from app.services.bots.strategies import BaseStrategy

logger = logging.getLogger(__name__)


class GnnCrossAssetStrategy(BaseStrategy):
    """GNN-based cross-asset signal propagation.

    This strategy is unique in that it benefits from multi-symbol context.
    When used with a single symbol, it falls back to the node's own features.
    When used in a scanner/basket context, it propagates signals across
    correlated assets.

    Config:
        min_confidence (float): Min signal probability (default 0.55).
        basket_id (str): Model basket identifier.
        model_symbol (str): Override symbol for single-symbol fallback.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self._cfg = merge_strategy_config("GNN_CROSS_ASSET", config or {})
        self._bar_history: deque = deque(maxlen=25)

    def _model_timeframe(self) -> str:
        from app.services.bots.ml_model_artifacts import normalize_model_timeframe

        return normalize_model_timeframe(
            self._cfg.get("timeframe") or self.config.get("timeframe")
        )

    def evaluate(self, df_row) -> dict:
        self._bar_history.append(dict(df_row))

        if len(self._bar_history) < 20:
            return {"signal": "NONE"}

        symbol = self._cfg.get("model_symbol") or str(df_row.get("_symbol", ""))
        if not symbol:
            symbol = str(self.config.get("symbol", "")).upper()
        if not symbol:
            return {"signal": "NONE"}

        # Extract features for current bar
        lookback_rows = list(self._bar_history)[:-1]
        features = bar_to_signal_features(df_row, lookback_rows=lookback_rows)
        feat_vec = signal_features_to_vector(features)

        from app.services.bots.ml_feature_drift import record_ml_inference_features

        record_ml_inference_features(symbol, "GNN_CROSS_ASSET", feat_vec)

        # Prefer explicit basket; fall back to symbol so Model Training artifacts resolve
        basket_id = (self._cfg.get("basket_id") or symbol or "").upper()
        if not basket_id:
            return {"signal": "NONE"}

        store = get_gnn_store()
        pinned = self._cfg.get("model_version") or None
        tf = self._model_timeframe()

        # For single-symbol context, create 1-node graph
        node_features = feat_vec.reshape(1, -1)
        adj = np.array([[1.0]], dtype=np.float32)

        logits = store.predict(
            basket_id, node_features, adj, model_version=pinned or None, timeframe=tf,
        )
        if logits is None:
            return {
                "signal": "NONE",
                "reject_reason": "ml_model_missing",
                "reject_detail": f"No trained GNN_CROSS_ASSET model for {basket_id} @ {tf}",
            }

        # Softmax
        x = logits[0] - logits[0].max()
        proba = np.exp(x) / np.exp(x).sum()
        pred = int(np.argmax(proba))
        conf = float(proba[pred])

        threshold = float(self._cfg.get("min_confidence", 0.55))
        signal_map = {0: "BUY", 1: "NONE", 2: "SELL"}
        signal = signal_map.get(pred, "NONE")

        atr = df_row.get("ATR_14") or df_row.get("ATRr_14") or 0
        try:
            atr = float(atr)
        except (TypeError, ValueError):
            atr = 0.0

        if signal in ("BUY", "SELL") and conf >= threshold:
            return apply_ml_meta_label_gate({
                "signal": signal,
                "confidence": round(conf, 4),
                "stop_loss_distance": atr * 1.5 if atr > 0 else None,
                "model_type": "gnn",
            }, df_row, self._cfg)

        return {"signal": "NONE"}
