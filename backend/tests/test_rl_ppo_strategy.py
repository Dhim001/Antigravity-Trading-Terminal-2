"""Tests for DRL PPO Trading Agent components:
  - rl_trading_env   (environment mechanics)
  - rl_ppo_trainer   (GAE, rollout buffer)
  - strategies_rl    (strategy wrapper)
"""

import math
import pytest
import numpy as np


# ── Environment tests ─────────────────────────────────────────────────────


class TestTradingEnv:
    def _make_candles(self, n=100, trend=0.1, atr=2.0):
        candles = []
        for i in range(n):
            c = 100.0 + i * trend
            candles.append({
                "time": 1700000000 + i * 60,
                "open": c - 0.5,
                "high": c + 1.0,
                "low": c - 1.0,
                "close": c,
                "volume": 1000.0 + i,
                "ATR_14": atr,
                "RSI_14": 50.0,
                "MACDh_12_26_9": 0.1,
                "STOCHk_14_3_3": 50.0,
                "ADX_14": 25.0,
                "EMA_9": c - 0.2,
                "EMA_21": c - 0.5,
            })
        return candles

    def test_reset_returns_correct_shape(self):
        from app.services.bots.rl_trading_env import TradingEnv, OBS_DIM
        candles = self._make_candles(100)
        env = TradingEnv(candles)
        obs = env.reset()
        assert obs.shape == (OBS_DIM,)
        assert all(np.isfinite(obs))

    def test_step_returns_correct_types(self):
        from app.services.bots.rl_trading_env import TradingEnv, ACTION_HOLD
        candles = self._make_candles(100)
        env = TradingEnv(candles)
        env.reset()
        obs, reward, done, info = env.step(ACTION_HOLD)
        assert isinstance(obs, np.ndarray)
        assert isinstance(reward, float)
        assert isinstance(done, bool)
        assert isinstance(info, dict)

    def test_hold_no_position_change(self):
        from app.services.bots.rl_trading_env import TradingEnv, ACTION_HOLD, SIDE_FLAT
        candles = self._make_candles(100)
        env = TradingEnv(candles)
        env.reset()
        _, _, _, info = env.step(ACTION_HOLD)
        assert info["position_side"] == SIDE_FLAT
        assert info["traded"] is False

    def test_buy_opens_long(self):
        from app.services.bots.rl_trading_env import TradingEnv, ACTION_BUY, SIDE_LONG
        candles = self._make_candles(100)
        env = TradingEnv(candles)
        env.reset()
        _, _, _, info = env.step(ACTION_BUY)
        assert info["position_side"] == SIDE_LONG
        assert info["traded"] is True

    def test_sell_opens_short(self):
        from app.services.bots.rl_trading_env import TradingEnv, ACTION_SELL, SIDE_SHORT
        candles = self._make_candles(100)
        env = TradingEnv(candles)
        env.reset()
        _, _, _, info = env.step(ACTION_SELL)
        assert info["position_side"] == SIDE_SHORT

    def test_close_flattens_position(self):
        from app.services.bots.rl_trading_env import (
            TradingEnv, ACTION_BUY, ACTION_CLOSE, SIDE_FLAT
        )
        candles = self._make_candles(100)
        env = TradingEnv(candles)
        env.reset()
        env.step(ACTION_BUY)
        _, _, _, info = env.step(ACTION_CLOSE)
        assert info["position_side"] == SIDE_FLAT

    def test_episode_ends_at_data_boundary(self):
        from app.services.bots.rl_trading_env import TradingEnv, ACTION_HOLD
        candles = self._make_candles(50)
        env = TradingEnv(candles, feature_lookback=5)
        env.reset()
        done = False
        steps = 0
        while not done:
            _, _, done, _ = env.step(ACTION_HOLD)
            steps += 1
        assert done
        assert steps > 0

    def test_uptrend_long_positive_return(self):
        from app.services.bots.rl_trading_env import TradingEnv, ACTION_BUY, ACTION_CLOSE
        # Strong uptrend
        candles = self._make_candles(80, trend=1.0)
        env = TradingEnv(candles, feature_lookback=5)
        env.reset()
        # Buy at start
        env.step(ACTION_BUY)
        # Hold for 20 bars
        for _ in range(20):
            env.step(0)
        # Close
        env.step(ACTION_CLOSE)
        stats = env.episode_stats()
        assert stats["final_equity"] > 1.0  # should have profited

    def test_episode_stats(self):
        from app.services.bots.rl_trading_env import TradingEnv, ACTION_BUY, ACTION_CLOSE, ACTION_HOLD
        candles = self._make_candles(60)
        env = TradingEnv(candles, feature_lookback=5)
        env.reset()
        env.step(ACTION_BUY)
        env.step(ACTION_HOLD)
        env.step(ACTION_CLOSE)
        stats = env.episode_stats()
        assert "final_equity" in stats
        assert "total_trades" in stats
        assert stats["total_trades"] == 2  # buy + close


# ── GAE tests ─────────────────────────────────────────────────────────────


class TestGAE:
    def test_gae_shape(self):
        from app.services.bots.rl_ppo_trainer import compute_gae
        rewards = [1.0, 0.5, -0.5, 0.2, 1.0]
        values = [0.5, 0.6, 0.3, 0.4, 0.7]
        dones = [False, False, False, False, False]
        advantages, returns = compute_gae(rewards, values, dones, next_value=0.5)
        assert advantages.shape == (5,)
        assert returns.shape == (5,)

    def test_gae_all_finite(self):
        from app.services.bots.rl_ppo_trainer import compute_gae
        rewards = [0.1, -0.2, 0.3, 0.0, -0.1]
        values = [0.5, 0.4, 0.6, 0.3, 0.5]
        dones = [False, False, True, False, False]
        advantages, returns = compute_gae(rewards, values, dones, next_value=0.4)
        assert all(np.isfinite(advantages))
        assert all(np.isfinite(returns))

    def test_gae_done_resets(self):
        from app.services.bots.rl_ppo_trainer import compute_gae
        # When done=True, next value should be zero (no future)
        rewards = [1.0, 1.0]
        values = [0.5, 0.5]
        dones = [False, True]
        advantages, returns = compute_gae(rewards, values, dones, next_value=10.0)
        # The last step's advantage should NOT incorporate next_value because done=True
        # return_last = advantage_last + value_last
        # With done=True: delta = reward - value = 1.0 - 0.5 = 0.5
        assert abs(advantages[1] - 0.5) < 0.01


# ── Rollout buffer tests ─────────────────────────────────────────────────


class TestRolloutBuffer:
    def test_add_and_length(self):
        from app.services.bots.rl_ppo_trainer import RolloutBuffer
        buf = RolloutBuffer()
        for i in range(10):
            buf.add(np.zeros(5), 0, 0.1, False, -0.5, 0.5)
        assert len(buf) == 10

    def test_clear(self):
        from app.services.bots.rl_ppo_trainer import RolloutBuffer
        buf = RolloutBuffer()
        buf.add(np.zeros(5), 0, 0.1, False, -0.5, 0.5)
        buf.clear()
        assert len(buf) == 0

    def test_get_batches(self):
        from app.services.bots.rl_ppo_trainer import RolloutBuffer
        buf = RolloutBuffer()
        for i in range(20):
            buf.add(np.zeros(5), 0, 0.1, False, -0.5, 0.5)
        batches = list(buf.get_batches(batch_size=8))
        # 20 samples / 8 per batch = 3 batches (8, 8, 4)
        assert len(batches) == 3
        total = sum(len(b) for b in batches)
        assert total == 20


# ── Strategy tests ────────────────────────────────────────────────────────


class TestRlPpoStrategy:
    def test_returns_none_without_model(self):
        from app.services.bots.strategies_rl import RlPpoStrategy
        strat = RlPpoStrategy({"symbol": "BTCUSDT"})
        bar = {
            "time": 1700000000,
            "open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0,
            "volume": 1000.0, "ATR_14": 3.0, "RSI_14": 55.0,
            "MACDh_12_26_9": 0.5, "STOCHk_14_3_3": 60.0,
            "ADX_14": 28.0, "EMA_9": 101.0, "EMA_21": 100.0,
            "_symbol": "BTCUSDT",
        }
        for i in range(30):
            result = strat.evaluate({**bar, "close": 100 + i * 0.1})
        assert result["signal"] == "NONE"

    def test_returns_none_with_insufficient_bars(self):
        from app.services.bots.strategies_rl import RlPpoStrategy
        strat = RlPpoStrategy({"symbol": "BTCUSDT"})
        bar = {"time": 1700000000, "open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}
        result = strat.evaluate(bar)
        assert result["signal"] == "NONE"

    def test_shadow_position_tracking(self):
        from app.services.bots.strategies_rl import RlPpoStrategy
        from app.services.bots.rl_trading_env import SIDE_LONG, SIDE_FLAT
        strat = RlPpoStrategy({"symbol": "BTCUSDT"})
        assert strat._position_side == SIDE_FLAT
        strat._open_shadow_position(SIDE_LONG, 100.0)
        assert strat._position_side == SIDE_LONG
        assert strat._entry_price == 100.0
        pnl = strat._compute_unrealized_pnl(105.0)
        assert pnl == pytest.approx(0.05, abs=0.001)
        strat._close_shadow_position()
        assert strat._position_side == SIDE_FLAT


class TestRlRegistration:
    def test_get_strategy_returns_ppo(self):
        from app.services.bots.strategies import get_strategy
        strat = get_strategy("RL_PPO_AGENT", {"symbol": "BTCUSDT"})
        assert strat is not None
        from app.services.bots.strategies_rl import RlPpoStrategy
        assert isinstance(strat, RlPpoStrategy)

    def test_ppo_in_catalog(self):
        from app.services.bots.strategy_catalog import list_strategy_catalog
        catalog = list_strategy_catalog()
        ids = [s["id"] for s in catalog]
        assert "RL_PPO_AGENT" in ids
        entry = next(s for s in catalog if s["id"] == "RL_PPO_AGENT")
        assert entry["category"] == "ml"
