"""RL_PPO_AGENT strategy — ONNX-based PPO policy inference.

Loads a pre-trained PPO actor-critic ONNX model and maps the policy's
discrete actions to trading signals.  Maintains local position shadow state
to construct the full observation vector.

Falls back to NONE if onnxruntime is not installed or no model exists.
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
    bar_to_signal_features,
    signal_features_to_vector,
)
from app.services.bots.ml_signal_gates import apply_ml_meta_label_gate
from app.services.bots.rl_ppo_trainer import get_ppo_store
from app.services.bots.rl_trading_env import (
    ACTION_BUY,
    ACTION_CLOSE,
    ACTION_HOLD,
    ACTION_SELL,
    N_FEATURES,
    OBS_DIM,
    SIDE_FLAT,
    SIDE_LONG,
    SIDE_SHORT,
    _MAX_HOLDING_BARS,
)
from app.services.bots.strategies import BaseStrategy

logger = logging.getLogger(__name__)


class RlPpoStrategy(BaseStrategy):
    """PPO reinforcement learning trading agent.

    Maintains a local shadow of position state and queries the ONNX policy
    network for action decisions on each bar.

    Config keys:
        min_confidence (float): Min softmax prob to act (default 0.28).
        model_symbol (str): Override symbol for model lookup.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self._cfg = merge_strategy_config("RL_PPO_AGENT", config or {})
        self._bar_history: deque = deque(maxlen=25)

        # Shadow position state (tracked locally; synced from live `_current_side`)
        self._position_side = SIDE_FLAT
        self._entry_price = 0.0
        self._entry_bar = 0
        self._bar_count = 0

        # Feature scaler (loaded from model artifacts)
        self._feat_mean: np.ndarray | None = None
        self._feat_std: np.ndarray | None = None
        self._scaler_loaded = False

    def evaluate(self, df_row) -> dict:
        self._bar_history.append(dict(df_row))
        self._bar_count += 1

        # Need enough history for feature computation
        if len(self._bar_history) < 20:
            return {"signal": "NONE"}

        # Resolve symbol
        symbol = self._cfg.get("model_symbol") or str(df_row.get("_symbol", ""))
        if not symbol:
            symbol = str(self.config.get("symbol", "")).upper()
        if not symbol:
            return {"signal": "NONE"}

        close = float(df_row.get("close") or 0)
        # Keep shadow aligned with the engine/live position when provided.
        self._sync_shadow_from_row(df_row, close)

        # Load scaler if not yet loaded
        if not self._scaler_loaded:
            self._load_scaler(symbol)

        # Extract features
        lookback_rows = list(self._bar_history)[:-1]
        features = bar_to_signal_features(df_row, lookback_rows=lookback_rows)
        feat_vec = signal_features_to_vector(features)

        # Normalize features
        if self._feat_mean is not None and self._feat_std is not None:
            feat_vec = (feat_vec - self._feat_mean) / self._feat_std

        # Position state features
        pos_pnl = self._compute_unrealized_pnl(close)
        bars_held = (
            float(self._bar_count - self._entry_bar) / _MAX_HOLDING_BARS
            if self._position_side != SIDE_FLAT
            else 0.0
        )
        pos_features = np.array(
            [float(self._position_side), pos_pnl, bars_held],
            dtype=np.float64,
        )

        obs = np.concatenate([feat_vec, pos_features]).astype(np.float32)

        # Query PPO policy
        store = get_ppo_store()
        pinned = self._cfg.get("model_version") or None
        result = store.predict_action(symbol, obs, model_version=pinned or None)
        if result is None:
            return {
                "signal": "NONE",
                "rl_step": {
                    "observation": obs.tolist()[:24],
                    "action": [0],
                    "reward": float(pos_pnl),
                    "position": float(self._position_side),
                },
            }

        action, confidence = result
        threshold = float(self._cfg.get("min_confidence", 0.28))

        def _step_payload(sig: str) -> dict:
            return {
                "observation": obs.tolist()[:24],
                "action": [int(action)],
                "reward": float(pos_pnl),
                "position": float(self._position_side),
                "confidence": float(confidence),
                "signal": sig,
            }

        atr = df_row.get("ATR_14") or df_row.get("ATRr_14") or 0
        try:
            atr = float(atr)
        except (TypeError, ValueError):
            atr = 0.0

        # Map action to signal and update shadow position state
        if action == ACTION_BUY and confidence >= threshold:
            # If short, this closes short first; then opens long
            if self._position_side == SIDE_SHORT:
                self._close_shadow_position()
                signal = "BUY"  # close short = buy to cover
            elif self._position_side == SIDE_FLAT:
                self._open_shadow_position(SIDE_LONG, close)
                signal = "BUY"
            else:
                return {"signal": "NONE", "rl_step": _step_payload("NONE")}  # already long

            return apply_ml_meta_label_gate({
                "signal": signal,
                "confidence": round(confidence, 4),
                "stop_loss_distance": atr * 1.5 if atr > 0 else None,
                "model_type": "rl_ppo",
                "rl_step": _step_payload(signal),
            }, df_row, self._cfg)

        if action == ACTION_SELL and confidence >= threshold:
            if self._position_side == SIDE_LONG:
                self._close_shadow_position()
                signal = "SELL"
            elif self._position_side == SIDE_FLAT:
                self._open_shadow_position(SIDE_SHORT, close)
                signal = "SELL"
            else:
                return {"signal": "NONE", "rl_step": _step_payload("NONE")}  # already short

            return apply_ml_meta_label_gate({
                "signal": signal,
                "confidence": round(confidence, 4),
                "stop_loss_distance": atr * 1.5 if atr > 0 else None,
                "model_type": "rl_ppo",
                "rl_step": _step_payload(signal),
            }, df_row, self._cfg)

        if action == ACTION_CLOSE and self._position_side != SIDE_FLAT:
            close_signal = "SELL" if self._position_side == SIDE_LONG else "BUY"
            self._close_shadow_position()
            return {
                "signal": "CLOSE",
                "close_direction": close_signal,
                "confidence": round(confidence, 4),
                "model_type": "rl_ppo",
                "rl_step": _step_payload("CLOSE"),
            }

        return {
            "signal": "NONE",
            "confidence": round(confidence, 4),
            "rl_step": _step_payload("NONE"),
        }

    def _load_scaler(self, symbol: str) -> None:
        self._scaler_loaded = True
        store = get_ppo_store()
        pinned = self._cfg.get("model_version") or None
        scaler = store.get_scaler(symbol, model_version=pinned or None)
        if scaler:
            mean = scaler.get("feat_mean")
            std = scaler.get("feat_std")
            if mean and std and len(mean) == N_FEATURES and len(std) == N_FEATURES:
                self._feat_mean = np.array(mean, dtype=np.float64)
                self._feat_std = np.array(std, dtype=np.float64)
                self._feat_std = np.where(self._feat_std < 1e-8, 1.0, self._feat_std)

    def _sync_shadow_from_row(self, df_row, close: float) -> None:
        """Align local shadow with live/backtest position when side is injected."""
        side_raw = df_row.get("_current_side")
        if side_raw is None:
            return
        side_u = str(side_raw).upper()
        if side_u in ("BUY", "LONG"):
            target = SIDE_LONG
        elif side_u in ("SELL", "SHORT"):
            target = SIDE_SHORT
        else:
            target = SIDE_FLAT
        if target == self._position_side:
            return
        if target == SIDE_FLAT:
            self._close_shadow_position()
        else:
            # Engine already holds a position — seed shadow at mark without
            # inventing an entry signal (entry price ≈ current if unknown).
            px = close if close > 0 else float(self._entry_price or 0)
            self._open_shadow_position(target, px if px > 0 else 0.0)

    def _open_shadow_position(self, side: int, price: float) -> None:
        self._position_side = side
        self._entry_price = price
        self._entry_bar = self._bar_count

    def _close_shadow_position(self) -> None:
        self._position_side = SIDE_FLAT
        self._entry_price = 0.0

    def _compute_unrealized_pnl(self, current_price: float) -> float:
        if self._entry_price <= 0 or self._position_side == SIDE_FLAT:
            return 0.0
        if self._position_side == SIDE_LONG:
            return (current_price - self._entry_price) / self._entry_price
        else:
            return (self._entry_price - current_price) / self._entry_price
