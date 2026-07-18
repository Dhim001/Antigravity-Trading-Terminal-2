"""Lightweight trading environment for reinforcement learning.

Gymnasium-style API (reset / step) without the Gymnasium dependency.
Wraps a candle series with indicators into a simulated trading environment
where an agent can take discrete actions and receive rewards.

Action space (Discrete, 4):
    0 = HOLD   — do nothing
    1 = BUY    — open long / close short
    2 = SELL   — open short / close long
    3 = CLOSE  — flatten any position

Observation space (Box, 37):
    34 ML features + 3 position state features
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.services.bots.ml_feature_engineering import (
    SIGNAL_FEATURE_NAMES,
    bar_to_signal_features,
    signal_features_to_vector,
)

N_FEATURES = len(SIGNAL_FEATURE_NAMES)
N_POSITION_FEATURES = 3  # side, unrealized_pnl, bars_since_entry
OBS_DIM = N_FEATURES + N_POSITION_FEATURES
N_ACTIONS = 4

# Action constants
ACTION_HOLD = 0
ACTION_BUY = 1
ACTION_SELL = 2
ACTION_CLOSE = 3

# Position side
SIDE_FLAT = 0
SIDE_LONG = 1
SIDE_SHORT = -1

# Reward scaling constants
_TRADE_COST = 0.001        # penalty per trade to discourage overtrading
_HOLDING_COST = 0.00005    # small per-step cost for holding a position
_MAX_HOLDING_BARS = 100    # normalize bars_since_entry


def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


class TradingEnv:
    """Simulated trading environment for RL agents.

    Parameters
    ----------
    candles : list[dict]
        OHLCV bars with indicators already computed.  Sorted oldest-first.
    config : dict, optional
        Environment config overrides.
    feature_lookback : int
        Number of prior bars for feature rolling computations.
    """

    def __init__(
        self,
        candles: list[dict],
        *,
        config: dict | None = None,
        feature_lookback: int = 20,
    ):
        self.candles = candles
        self.config = config or {}
        self.feature_lookback = feature_lookback
        self.n_candles = len(candles)

        # Pre-extract all feature vectors for speed
        self._feature_vectors: list[np.ndarray] = []
        self._closes: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []

        for i in range(self.n_candles):
            c = candles[i]
            self._closes.append(_safe_float(c.get("close")))
            self._highs.append(_safe_float(c.get("high")))
            self._lows.append(_safe_float(c.get("low")))

            lb_start = max(0, i - feature_lookback)
            lb_rows = candles[lb_start:i]
            features = bar_to_signal_features(c, lookback_rows=lb_rows)
            self._feature_vectors.append(signal_features_to_vector(features))

        # Compute feature-wise mean/std for normalization
        if self._feature_vectors:
            stacked = np.stack(self._feature_vectors)
            self._feat_mean = stacked.mean(axis=0)
            self._feat_std = stacked.std(axis=0)
            self._feat_std = np.where(self._feat_std < 1e-8, 1.0, self._feat_std)
        else:
            self._feat_mean = np.zeros(N_FEATURES)
            self._feat_std = np.ones(N_FEATURES)

        # State variables (set by reset)
        self._step_idx = 0
        self._start_idx = feature_lookback  # skip warm-up bars
        self._position_side = SIDE_FLAT
        self._entry_price = 0.0
        self._entry_step = 0
        self._equity = 1.0  # normalized starting equity
        self._prev_equity = 1.0
        self._total_trades = 0
        self._done = False

    @property
    def obs_dim(self) -> int:
        return OBS_DIM

    @property
    def n_actions(self) -> int:
        return N_ACTIONS

    def reset(self) -> np.ndarray:
        """Reset the environment to the start. Returns initial observation."""
        self._step_idx = self._start_idx
        self._position_side = SIDE_FLAT
        self._entry_price = 0.0
        self._entry_step = 0
        self._equity = 1.0
        self._prev_equity = 1.0
        self._total_trades = 0
        self._done = False
        return self._get_obs()

    def step(self, action: int) -> tuple[np.ndarray, float, bool, dict]:
        """Execute one step in the environment.

        Parameters
        ----------
        action : int
            0=HOLD, 1=BUY, 2=SELL, 3=CLOSE

        Returns
        -------
        obs : np.ndarray (37,)
        reward : float
        done : bool
        info : dict
        """
        if self._done:
            return self._get_obs(), 0.0, True, {"reason": "already_done"}

        close = self._closes[self._step_idx]
        high = self._highs[self._step_idx]
        low = self._lows[self._step_idx]

        reward = 0.0
        info: dict[str, Any] = {"action": action, "step": self._step_idx}
        traded = False

        # ── Execute action ────────────────────────────────────────────
        if action == ACTION_BUY:
            if self._position_side == SIDE_SHORT:
                # Close short position
                reward += self._close_position(close)
                traded = True
            if self._position_side == SIDE_FLAT:
                # Open long
                self._open_position(SIDE_LONG, close)
                traded = True

        elif action == ACTION_SELL:
            if self._position_side == SIDE_LONG:
                # Close long position
                reward += self._close_position(close)
                traded = True
            if self._position_side == SIDE_FLAT:
                # Open short
                self._open_position(SIDE_SHORT, close)
                traded = True

        elif action == ACTION_CLOSE:
            if self._position_side != SIDE_FLAT:
                reward += self._close_position(close)
                traded = True

        # ACTION_HOLD → no position change

        # ── Per-step rewards ──────────────────────────────────────────
        # Unrealized PnL change reward (encourage riding winners)
        if self._position_side != SIDE_FLAT:
            unrealized_pnl = self._unrealized_pnl(close)
            reward += unrealized_pnl * 0.1  # small reward for positive drift
            reward -= _HOLDING_COST  # small cost for being in a position

        # Trade cost penalty
        if traded:
            reward -= _TRADE_COST
            self._total_trades += 1

        # ── Advance step ──────────────────────────────────────────────
        self._step_idx += 1
        if self._step_idx >= self.n_candles - 1:
            # Force close at end of episode
            if self._position_side != SIDE_FLAT:
                final_close = self._closes[min(self._step_idx, self.n_candles - 1)]
                reward += self._close_position(final_close)
            self._done = True
            info["reason"] = "end_of_data"

        info["equity"] = self._equity
        info["position_side"] = self._position_side
        info["total_trades"] = self._total_trades
        info["traded"] = traded

        return self._get_obs(), reward, self._done, info

    def _get_obs(self) -> np.ndarray:
        """Construct observation vector: normalized features + position state."""
        idx = min(self._step_idx, self.n_candles - 1)
        feat = (self._feature_vectors[idx] - self._feat_mean) / self._feat_std

        # Position state features
        close = self._closes[idx]
        pos_side = float(self._position_side)
        pos_pnl = self._unrealized_pnl(close) if self._position_side != SIDE_FLAT else 0.0
        bars_held = float(self._step_idx - self._entry_step) / _MAX_HOLDING_BARS if self._position_side != SIDE_FLAT else 0.0

        pos_features = np.array([pos_side, pos_pnl, bars_held], dtype=np.float64)
        return np.concatenate([feat, pos_features]).astype(np.float32)

    def _open_position(self, side: int, price: float) -> None:
        self._position_side = side
        self._entry_price = price
        self._entry_step = self._step_idx

    def _close_position(self, price: float) -> float:
        """Close position and return realized PnL as fraction of equity."""
        if self._entry_price <= 0:
            self._position_side = SIDE_FLAT
            return 0.0

        if self._position_side == SIDE_LONG:
            pnl_pct = (price - self._entry_price) / self._entry_price
        elif self._position_side == SIDE_SHORT:
            pnl_pct = (self._entry_price - price) / self._entry_price
        else:
            pnl_pct = 0.0

        self._equity *= (1.0 + pnl_pct)
        self._position_side = SIDE_FLAT
        self._entry_price = 0.0
        return pnl_pct

    def _unrealized_pnl(self, current_price: float) -> float:
        """Unrealized PnL as fraction of entry price."""
        if self._entry_price <= 0 or self._position_side == SIDE_FLAT:
            return 0.0
        if self._position_side == SIDE_LONG:
            return (current_price - self._entry_price) / self._entry_price
        else:  # SHORT
            return (self._entry_price - current_price) / self._entry_price

    def episode_stats(self) -> dict[str, Any]:
        """Return summary statistics for the completed episode."""
        return {
            "final_equity": round(self._equity, 6),
            "return_pct": round((self._equity - 1.0) * 100, 4),
            "total_trades": self._total_trades,
            "steps": self._step_idx - self._start_idx,
        }
