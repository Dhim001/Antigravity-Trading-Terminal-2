"""Lightweight trading environment for reinforcement learning.

Gymnasium-style API (reset / step) without the Gymnasium dependency.
Wraps a candle series with indicators into a simulated trading environment
where an agent can take discrete actions and receive rewards.

Decision at completed bar t, fill at bar t+1 open. ATR stop/TP may still
hit on that fill bar's high/low. Reward is Δlog-equity − λ·ΔDD − turnover;
R-multiple is recorded in info / episode stats only.

Action space (Discrete, 4):
    0 = HOLD   — do nothing
    1 = BUY    — open long / close short
    2 = SELL   — open short / close long
    3 = CLOSE  — flatten any position

Observation space (Box):
    SIGNAL_FEATURE_NAMES features + 3 position state features
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

_MAX_HOLDING_BARS = 100    # normalize bars_since_entry
# Default episode cap — without this, one "episode" walks the entire candle
# history (50k+ bars on Apply & Retrain), so Optuna budgets (8k–65k steps)
# finish at ep=0 with best_mean_return=-inf.
_DEFAULT_MAX_EPISODE_STEPS = 2048


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


def _bar_allows_new_entries(symbol: str | None, bar: dict | None) -> bool:
    """False when equity session is closed (crypto always True)."""
    if not symbol:
        return True
    try:
        from app.services.bots.time_windows import is_crypto_symbol

        if is_crypto_symbol(symbol):
            return True
    except Exception:
        pass
    raw = (bar or {}).get("time")
    if raw is None:
        return True
    try:
        from app.services.altdata.calendar import session_features_for_bar

        return float(session_features_for_bar(symbol, raw).get("is_rth", 1.0)) >= 0.5
    except Exception:
        return True


class TradingEnv:
    """Simulated trading environment for RL agents.

    Parameters
    ----------
    candles : list[dict]
        OHLCV bars with indicators already computed.  Sorted oldest-first.
    config : dict, optional
        Environment config overrides. Set ``symbol`` for equity RTH action masking.
    feature_lookback : int
        Number of prior bars for feature rolling computations.
    """

    def __init__(
        self,
        candles: list[dict],
        *,
        config: dict | None = None,
        feature_lookback: int = 20,
        feat_mean=None,
        feat_std=None,
        progress_path: str | None = None,
    ):
        self.candles = candles
        self.config = config or {}
        self.feature_lookback = feature_lookback
        self.n_candles = len(candles)
        self._symbol = str(self.config.get("symbol") or "").strip() or None
        self._progress_path = progress_path
        from app.services.bots.rl_risk import (
            DEFAULT_SCALER_FIT_FRAC,
            REWARD_DD_LAMBDA,
            REWARD_TURNOVER_COEF,
            resolve_atr_stop_mult,
            resolve_rl_costs,
            resolve_take_profit_r,
        )

        self._fee_bps, self._slippage_bps = resolve_rl_costs(self.config)
        self._atr_stop_mult = resolve_atr_stop_mult(self.config)
        self._take_profit_r = resolve_take_profit_r(self.config)
        try:
            self._dd_lambda = float(self.config.get("reward_dd_lambda", REWARD_DD_LAMBDA))
        except (TypeError, ValueError):
            self._dd_lambda = REWARD_DD_LAMBDA
        try:
            self._turnover_coef = float(
                self.config.get("reward_turnover_coef", REWARD_TURNOVER_COEF)
            )
        except (TypeError, ValueError):
            self._turnover_coef = REWARD_TURNOVER_COEF
        try:
            self._scaler_fit_frac = float(
                self.config.get("scaler_fit_frac", DEFAULT_SCALER_FIT_FRAC)
            )
        except (TypeError, ValueError):
            self._scaler_fit_frac = DEFAULT_SCALER_FIT_FRAC
        self._scaler_fit_frac = min(1.0, max(0.1, self._scaler_fit_frac))
        self._use_atr_stops = self.config.get("use_atr_stops")
        if self._use_atr_stops is None:
            self._use_atr_stops = True
        else:
            self._use_atr_stops = bool(self._use_atr_stops)
        try:
            cfg_ep = int(self.config.get("max_episode_steps") or 0)
        except (TypeError, ValueError):
            cfg_ep = 0
        self._max_episode_steps = (
            cfg_ep if cfg_ep > 0 else _DEFAULT_MAX_EPISODE_STEPS
        )
        # Deterministic starts for tiny series / tests; random windows for long history.
        seed = self.config.get("env_seed")
        self._rng = np.random.default_rng(None if seed is None else int(seed))
        self._episode_end_idx = self.n_candles - 1

        # Pre-extract all feature vectors for speed
        self._feature_vectors: list[np.ndarray] = []
        self._opens: list[float] = []
        self._closes: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._atrs: list[float] = []
        self._allow_entry: list[bool] = []

        _hb_t = 0.0
        for i in range(self.n_candles):
            c = candles[i]
            close_px = _safe_float(c.get("close"))
            open_px = _safe_float(c.get("open"), close_px)
            self._opens.append(open_px if open_px > 0 else close_px)
            self._closes.append(close_px)
            self._highs.append(_safe_float(c.get("high")))
            self._lows.append(_safe_float(c.get("low")))
            atr = c.get("ATR_14") or c.get("ATRr_14") or c.get("atr")
            self._atrs.append(_safe_float(atr, 0.0))
            self._allow_entry.append(_bar_allows_new_entries(self._symbol, c))

            lb_start = max(0, i - feature_lookback)
            lb_rows = candles[lb_start:i]
            features = bar_to_signal_features(
                c, lookback_rows=lb_rows, symbol=self._symbol,
            )
            self._feature_vectors.append(signal_features_to_vector(features))

            # Heartbeat so large feature builds don't look like a frozen job.
            if self._progress_path and (i % 2000 == 0 or i == self.n_candles - 1):
                import time as _time

                now = _time.time()
                if now - _hb_t >= 1.5:
                    _hb_t = now
                    try:
                        from app.services.bots.ml_job_progress import (
                            ml_cancel_requested,
                            write_ml_progress,
                        )

                        if ml_cancel_requested(self._progress_path):
                            raise InterruptedError("ml_cancel_requested")
                        write_ml_progress(
                            self._progress_path,
                            pct=5,
                            phase="env",
                            detail=f"build features {i + 1}/{self.n_candles}",
                        )
                    except InterruptedError:
                        raise
                    except Exception:
                        pass

        # Fit scaler on a train prefix only — full-file mean/std leaks the future.
        # Walk-forward OOS passes a frozen fold scaler and skips this fit.
        self._scaler_fit_n = 0
        if feat_mean is not None and feat_std is not None:
            self._feat_mean = np.zeros(N_FEATURES)
            self._feat_std = np.ones(N_FEATURES)
            self.set_feature_scaler(feat_mean, feat_std)
        elif self._feature_vectors:
            self._fit_train_scaler()
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
        self._peak_equity = 1.0
        self._max_dd = 0.0
        self._total_trades = 0
        self._stop_price = 0.0
        self._take_profit_price = 0.0
        self._stop_distance = 0.0
        self._closed_pnls: list[float] = []
        self._done = False

    def _fit_train_scaler(self) -> None:
        n = len(self._feature_vectors)
        warmup = max(0, int(self.feature_lookback))
        n_fit = max(warmup + 1, int(n * self._scaler_fit_frac))
        n_fit = min(n, max(1, n_fit))
        stacked = np.stack(self._feature_vectors[:n_fit])
        self._feat_mean = stacked.mean(axis=0)
        self._feat_std = stacked.std(axis=0)
        self._feat_std = np.where(self._feat_std < 1e-8, 1.0, self._feat_std)
        self._scaler_fit_n = n_fit

    def set_feature_scaler(self, feat_mean, feat_std) -> None:
        """Apply a frozen train-time feature scaler (WF OOS must not refit)."""
        mean = np.asarray(feat_mean, dtype=np.float64).reshape(-1)
        std = np.asarray(feat_std, dtype=np.float64).reshape(-1)
        if mean.size == 0 or std.size != mean.size:
            return
        # Align feature matrix to scaler width (legacy 41-dim models).
        # Env may still hold a Python list of per-bar vectors at this point.
        if self._feature_vectors is not None and len(self._feature_vectors) > 0:
            from app.services.bots.ml_feature_engineering import align_features_to_scaler_dim

            mat = np.asarray(self._feature_vectors, dtype=np.float64)
            if mat.ndim == 1:
                mat = mat.reshape(1, -1)
            if mat.shape[1] != mean.size:
                mat = align_features_to_scaler_dim(
                    mat, int(mean.size), log_label="RL env scaler",
                )
            self._feature_vectors = mat
        self._feat_mean = mean
        self._feat_std = np.where(std < 1e-8, 1.0, std)

    @property
    def obs_dim(self) -> int:
        # Reflect aligned scaler width so legacy 41-dim models stay consistent.
        if self._feat_mean is not None:
            return int(self._feat_mean.shape[0]) + N_POSITION_FEATURES
        return OBS_DIM

    @property
    def n_actions(self) -> int:
        return N_ACTIONS

    def reset(self) -> np.ndarray:
        """Reset the environment to a (possibly random) episode window.

        On long histories, episodes are capped to ``max_episode_steps`` and
        start at a random eligible index so Apply & Retrain (50k bars) can
        complete many episodes within a normal ``total_timesteps`` budget.
        """
        warmup = max(0, int(self.feature_lookback))
        usable = max(0, self.n_candles - warmup - 2)
        ep_len = max(8, int(self._max_episode_steps))
        if usable <= ep_len:
            self._start_idx = warmup
            self._episode_end_idx = max(warmup + 1, self.n_candles - 1)
        else:
            # Inclusive start; exclusive end index for the last step check.
            max_start = self.n_candles - 2 - ep_len
            max_start = max(warmup, max_start)
            self._start_idx = int(self._rng.integers(warmup, max_start + 1))
            self._episode_end_idx = min(
                self.n_candles - 1,
                self._start_idx + ep_len,
            )

        self._step_idx = self._start_idx
        self._position_side = SIDE_FLAT
        self._entry_price = 0.0
        self._entry_step = 0
        self._stop_price = 0.0
        self._take_profit_price = 0.0
        self._stop_distance = 0.0
        self._equity = 1.0
        self._prev_equity = 1.0
        self._peak_equity = 1.0
        self._max_dd = 0.0
        self._total_trades = 0
        self._closed_pnls: list[float] = []
        self._done = False
        return self._get_obs()

    def _mask_entry_action(self, action: int) -> int:
        """Outside equity RTH: block new entries; still allow closes/flattens."""
        if self._step_idx >= len(self._allow_entry):
            return action
        if self._allow_entry[self._step_idx]:
            return action
        if action == ACTION_BUY and self._position_side == SIDE_FLAT:
            return ACTION_HOLD
        if action == ACTION_SELL and self._position_side == SIDE_FLAT:
            return ACTION_HOLD
        return action

    def step(self, action: int) -> tuple[np.ndarray, float, bool, dict]:
        """Execute one step in the environment.

        Parameters
        ----------
        action : int
            0=HOLD, 1=BUY, 2=SELL, 3=CLOSE

        Returns
        -------
        obs : np.ndarray
        reward : float
        done : bool
        info : dict
        """
        if self._done:
            return self._get_obs(), 0.0, True, {"reason": "already_done"}

        decision_idx = self._step_idx
        fill_idx = decision_idx + 1
        raw_action = int(action)
        action = self._mask_entry_action(raw_action)

        info: dict[str, Any] = {
            "action": action,
            "raw_action": raw_action,
            "step": decision_idx,
            "fill_idx": fill_idx,
            "entry_masked": action != raw_action,
            "r_multiple": 0.0,
        }
        eq_before = self._equity
        peak_before = self._peak_equity
        traded = False
        r_acc = 0.0

        def _apply_close(price: float) -> None:
            nonlocal traded, r_acc
            r_acc += self._close_position(price)
            traded = True

        if fill_idx >= self.n_candles:
            if self._position_side != SIDE_FLAT:
                close_px = self._closes[min(decision_idx, self.n_candles - 1)]
                exit_side = "SELL" if self._position_side == SIDE_LONG else "BUY"
                _apply_close(self._fill_price(close_px, exit_side))
            self._done = True
            info["reason"] = "end_of_data"
            if traded:
                self._total_trades += 1
            reward = self._finish_step_reward(eq_before, peak_before, traded)
            info["r_multiple"] = r_acc
            self._fill_step_info(info, traded)
            return self._get_obs(), reward, True, info

        fill_open = self._opens[fill_idx] if fill_idx < len(self._opens) else self._closes[fill_idx]
        if fill_open <= 0:
            fill_open = self._closes[fill_idx]
        fill_high = self._highs[fill_idx] if fill_idx < len(self._highs) else fill_open
        fill_low = self._lows[fill_idx] if fill_idx < len(self._lows) else fill_open

        # Decision used completed bar t; fill and barriers use bar t+1.
        self._step_idx = fill_idx

        if action == ACTION_BUY:
            if self._position_side == SIDE_SHORT:
                _apply_close(self._fill_price(fill_open, "BUY"))
            if self._position_side == SIDE_FLAT:
                self._open_position(SIDE_LONG, self._fill_price(fill_open, "BUY"))
                traded = True
        elif action == ACTION_SELL:
            if self._position_side == SIDE_LONG:
                _apply_close(self._fill_price(fill_open, "SELL"))
            if self._position_side == SIDE_FLAT:
                self._open_position(SIDE_SHORT, self._fill_price(fill_open, "SELL"))
                traded = True
        elif action == ACTION_CLOSE:
            if self._position_side != SIDE_FLAT:
                exit_side = "SELL" if self._position_side == SIDE_LONG else "BUY"
                _apply_close(self._fill_price(fill_open, exit_side))

        # After the next-open fill, ATR stop/TP may still hit on this bar's H/L.
        if self._position_side != SIDE_FLAT and self._use_atr_stops:
            hit_px, hit_reason = self._barrier_hit(fill_high, fill_low)
            if hit_px is not None:
                _apply_close(hit_px)
                info["barrier"] = hit_reason

        if fill_idx >= self._episode_end_idx or fill_idx >= self.n_candles - 1:
            if self._position_side != SIDE_FLAT:
                final_close = self._closes[fill_idx]
                exit_side = "SELL" if self._position_side == SIDE_LONG else "BUY"
                _apply_close(self._fill_price(final_close, exit_side))
            self._done = True
            info["reason"] = (
                "end_of_data"
                if fill_idx >= self.n_candles - 1
                else "episode_horizon"
            )

        if traded:
            self._total_trades += 1

        reward = self._finish_step_reward(eq_before, peak_before, traded)
        info["r_multiple"] = r_acc
        self._fill_step_info(info, traded)
        return self._get_obs(), reward, self._done, info

    def _finish_step_reward(
        self, eq_before: float, peak_before: float, traded: bool,
    ) -> float:
        from app.services.bots.rl_risk import path_step_reward

        eq = self._equity
        reward = path_step_reward(
            eq_before,
            eq,
            peak_before,
            traded=traded,
            dd_lambda=self._dd_lambda,
            turnover_coef=self._turnover_coef,
        )
        self._peak_equity = max(peak_before, eq)
        if self._peak_equity > 1e-12:
            dd = max(0.0, (self._peak_equity - eq) / self._peak_equity)
            self._max_dd = max(self._max_dd, dd)
        self._prev_equity = eq
        return float(reward)

    def _fill_step_info(self, info: dict[str, Any], traded: bool) -> None:
        info["equity"] = self._equity
        info["position_side"] = self._position_side
        info["total_trades"] = self._total_trades
        info["traded"] = traded
        info["fee_bps"] = self._fee_bps
        info["slippage_bps"] = self._slippage_bps

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

    def _fill_price(self, price: float, side: str) -> float:
        slip = self._slippage_bps / 10_000.0
        if slip <= 0 or price <= 0:
            return price
        if str(side).upper() == "BUY":
            return price * (1.0 + slip)
        return price * (1.0 - slip)

    def _fee_frac(self) -> float:
        return self._fee_bps / 10_000.0

    def _barrier_hit(self, high: float, low: float) -> tuple[float | None, str | None]:
        if self._stop_price <= 0 and self._take_profit_price <= 0:
            return None, None
        if self._position_side == SIDE_LONG:
            if self._stop_price > 0 and low <= self._stop_price:
                return self._fill_price(self._stop_price, "SELL"), "atr_stop"
            if self._take_profit_price > 0 and high >= self._take_profit_price:
                return self._fill_price(self._take_profit_price, "SELL"), "atr_tp"
        elif self._position_side == SIDE_SHORT:
            if self._stop_price > 0 and high >= self._stop_price:
                return self._fill_price(self._stop_price, "BUY"), "atr_stop"
            if self._take_profit_price > 0 and low <= self._take_profit_price:
                return self._fill_price(self._take_profit_price, "BUY"), "atr_tp"
        return None, None

    def _arm_barriers(self, side: int, price: float) -> None:
        idx = min(self._step_idx, len(self._atrs) - 1) if self._atrs else -1
        atr = self._atrs[idx] if idx >= 0 else 0.0
        from app.services.bots.rl_risk import stop_take_prices

        side_name = "BUY" if side == SIDE_LONG else "SELL"
        dist, sl_px, tp_px = stop_take_prices(
            side_name,
            price,
            atr,
            stop_mult=self._atr_stop_mult,
            take_profit_r=self._take_profit_r,
        )
        self._stop_distance = float(dist or 0.0)
        self._stop_price = float(sl_px or 0.0)
        self._take_profit_price = float(tp_px or 0.0)
        if not self._use_atr_stops:
            self._stop_price = 0.0
            self._take_profit_price = 0.0

    def _open_position(self, side: int, price: float) -> None:
        self._position_side = side
        self._entry_price = price
        self._entry_step = self._step_idx
        self._arm_barriers(side, price)
        fee = self._fee_frac()
        if fee > 0:
            self._equity *= (1.0 - fee)

    def _close_position(self, price: float) -> float:
        """Close position, update equity, return R-multiple for diagnostics."""
        if self._entry_price <= 0:
            self._position_side = SIDE_FLAT
            return 0.0

        if self._position_side == SIDE_LONG:
            pnl_pct = (price - self._entry_price) / self._entry_price
        elif self._position_side == SIDE_SHORT:
            pnl_pct = (self._entry_price - price) / self._entry_price
        else:
            pnl_pct = 0.0

        fee = self._fee_frac()
        if fee > 0:
            pnl_pct -= fee

        self._equity *= (1.0 + pnl_pct)
        self._closed_pnls.append(pnl_pct)

        stop_frac = (
            self._stop_distance / self._entry_price
            if self._entry_price > 0 and self._stop_distance > 0
            else 0.0
        )
        reward = (pnl_pct / stop_frac) if stop_frac > 1e-12 else pnl_pct

        self._position_side = SIDE_FLAT
        self._entry_price = 0.0
        self._stop_price = 0.0
        self._take_profit_price = 0.0
        self._stop_distance = 0.0
        return reward

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
        from app.services.bots.rl_risk import trade_payoff_stats

        payoff = trade_payoff_stats(list(self._closed_pnls))
        return {
            "final_equity": round(self._equity, 6),
            "return_pct": round((self._equity - 1.0) * 100, 4),
            "total_trades": self._total_trades,
            "steps": self._step_idx - self._start_idx,
            "fee_bps": self._fee_bps,
            "slippage_bps": self._slippage_bps,
            "atr_stop_mult": self._atr_stop_mult,
            "take_profit_r": self._take_profit_r,
            "max_drawdown": round(self._max_dd, 6),
            **payoff,
        }
