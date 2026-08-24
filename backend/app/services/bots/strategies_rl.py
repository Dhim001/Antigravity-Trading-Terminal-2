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
        min_confidence (float): Min softmax prob to act (default 0.40).
        model_symbol (str): Override symbol for model lookup.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self._cfg = merge_strategy_config("RL_PPO_AGENT", config or {})
        from app.services.bots.ml_feature_engineering import EVAL_HISTORY_LOOKBACK
        self._bar_history: deque = deque(maxlen=EVAL_HISTORY_LOOKBACK + 1)

        # Shadow position state (tracked locally; synced from live `_current_side`)
        self._position_side = SIDE_FLAT
        self._entry_price = 0.0
        self._entry_bar = 0
        self._bar_count = 0

        # Feature scaler (loaded from model artifacts)
        self._feat_mean: np.ndarray | None = None
        self._feat_std: np.ndarray | None = None
        self._scaler_loaded = False

        # Backtest-only: market feature matrix aligned to the candle DataFrame.
        # Position features still splice per bar (policy is sequential).
        self._bt_feat_matrix: np.ndarray | None = None
        self._bt_feat_len = 0

    def _model_timeframe(self) -> str:
        from app.services.bots.ml_model_artifacts import normalize_model_timeframe

        return normalize_model_timeframe(
            self._cfg.get("timeframe") or self.config.get("timeframe")
        )

    @property
    def has_backtest_feature_matrix(self) -> bool:
        return self._bt_feat_matrix is not None and self._bt_feat_len > 0

    def prepare_backtest_features(
        self,
        candles,
        start_i: int = 0,
        *,
        symbol: str = "",
        cancel_cb: Any | None = None,
        progress_cb: Any | None = None,
    ) -> None:
        """Precompute market features once (same matrix as other ML backtests).

        ONNX stays per-bar because the observation includes position state.
        ``start_i`` is accepted for call-site symmetry; the matrix is aligned
        to the full candle index so ``_bar_index`` lookups match ``df.iloc[i]``.
        """
        del start_i  # matrix is full-length; evaluate uses _bar_index
        from app.services.bots.ml_feature_engineering import precompute_signal_feature_matrix

        if symbol:
            self._cfg["model_symbol"] = str(symbol).upper()
            self._cfg.setdefault("symbol", str(symbol).upper())
        mat = precompute_signal_feature_matrix(
            candles,
            cancel_cb=cancel_cb,
            progress_cb=progress_cb,
        )
        if mat is None or getattr(mat, "shape", (0,))[0] == 0:
            self._bt_feat_matrix = None
            self._bt_feat_len = 0
            return
        self._bt_feat_matrix = np.asarray(mat, dtype=np.float64)
        self._bt_feat_len = int(self._bt_feat_matrix.shape[0])

    def _market_feature_vector(self, df_row) -> np.ndarray:
        """Market features only (no position). Matrix path matches vectorized train/BT."""
        bar_index = df_row.get("_bar_index")
        if (
            self.has_backtest_feature_matrix
            and bar_index is not None
        ):
            idx = int(bar_index)
            if 0 <= idx < self._bt_feat_len:
                return np.asarray(self._bt_feat_matrix[idx], dtype=np.float64)
        lookback_rows = list(self._bar_history)[:-1]
        features = bar_to_signal_features(df_row, lookback_rows=lookback_rows)
        return signal_features_to_vector(features)

    def evaluate(self, df_row) -> dict:
        use_matrix = self.has_backtest_feature_matrix
        if not use_matrix:
            self._bar_history.append(dict(df_row))
        self._bar_count += 1

        # Need enough history for feature computation (live deque or eval count)
        if use_matrix:
            if self._bar_count < 20:
                return {"signal": "NONE"}
        elif len(self._bar_history) < 20:
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

        tf = self._model_timeframe()

        # Load scaler if not yet loaded
        if not self._scaler_loaded:
            self._load_scaler(symbol, timeframe=tf)

        feat_vec = self._market_feature_vector(df_row)

        from app.services.bots.ml_feature_drift import record_ml_inference_features
        from app.services.bots.ml_feature_engineering import apply_feature_scaler

        # Record raw (pre-norm) signal features so PSI matches scaler baselines.
        # Skip during backtest — 1m replays would overwrite the live PSI window.
        if not df_row.get("_backtest"):
            record_ml_inference_features(symbol, "RL_PPO_AGENT", feat_vec)

        # Normalize + align to trained scaler width (legacy v4 models stay usable).
        if self._feat_mean is not None and self._feat_std is not None:
            feat_vec = apply_feature_scaler(
                feat_vec,
                self._feat_mean,
                self._feat_std,
                log_label=f"RL_PPO[{symbol}]",
            )

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
        result = store.predict_action(
            symbol, obs, model_version=pinned or None, timeframe=tf,
        )
        if result is None:
            return {
                "signal": "NONE",
                "reject_reason": "ml_model_missing",
                "reject_detail": f"No trained RL_PPO_AGENT model for {symbol} @ {tf}",
                "rl_step": {
                    "observation": obs.tolist()[:24],
                    "action": [0],
                    "reward": float(pos_pnl),
                    "position": float(self._position_side),
                },
            }

        action, confidence = result
        from app.services.bots.rl_risk import DEFAULT_MIN_CONFIDENCE

        threshold = float(self._cfg.get("min_confidence", DEFAULT_MIN_CONFIDENCE))

        # Replay buffer (AI-FT-PTL-001 §3.2): stash live (obs, action) so the
        # trade-close hook can persist the full transition with its reward.
        if not df_row.get("_backtest"):
            try:
                from app.services.bots.rl_replay_store import note_pending_action

                _bot_id = self._cfg.get("_bot_id") or self._cfg.get("bot_id")
                if _bot_id:
                    note_pending_action(str(_bot_id), symbol, obs, action)
            except Exception:
                pass

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

        from app.services.bots.rl_risk import (
            resolve_atr_stop_mult,
            resolve_take_profit_r,
            stop_take_prices,
        )

        stop_mult = resolve_atr_stop_mult(self._cfg)
        tp_r = resolve_take_profit_r(self._cfg)

        def _atr_levels(side: str) -> dict:
            dist, sl_px, tp_px = stop_take_prices(
                side, close, atr, stop_mult=stop_mult, take_profit_r=tp_r,
            )
            out = {
                "stop_loss_distance": dist,
                "atr": atr if atr > 0 else None,
                "atr_stop_mult": stop_mult,
                "take_profit_r": tp_r,
            }
            if sl_px is not None:
                out["stop_loss_price"] = sl_px
            if tp_px is not None:
                out["take_profit_price"] = tp_px
            return out

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
                **_atr_levels("BUY"),
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
                **_atr_levels("SELL"),
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

    def _load_scaler(self, symbol: str, *, timeframe: str | None = None) -> None:
        self._scaler_loaded = True
        store = get_ppo_store()
        pinned = self._cfg.get("model_version") or None
        scaler = store.get_scaler(
            symbol, model_version=pinned or None, timeframe=timeframe,
        )
        if scaler:
            mean = scaler.get("feat_mean")
            std = scaler.get("feat_std")
            if mean and std and len(mean) == len(std) and len(mean) > 0:
                # Accept legacy widths; apply_feature_scaler aligns live dims.
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
