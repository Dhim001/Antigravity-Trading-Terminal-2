"""RL walk-forward OOS uses episode returns, not triple-barrier accuracy."""

from __future__ import annotations

import math

import numpy as np
import pytest


def test_rl_return_to_score_midpoint():
    from app.services.bots.ml_walk_forward_validator import _rl_return_to_score

    assert abs(_rl_return_to_score(0.0) - 0.5) < 1e-6
    assert _rl_return_to_score(10.0) > 0.7
    assert _rl_return_to_score(-10.0) < 0.3


def test_rl_recommendation_uses_returns():
    from app.services.bots.ml_walk_forward_validator import _make_recommendation

    agg = {
        "metric_kind": "rl_return",
        "mean_oos_return_pct": 3.5,
        "total_oos_signals": 12,
        "positive_return_folds": 2,
    }
    stab = {"cv": 0.2, "trend": "stable"}
    rec = _make_recommendation(agg, stab, n_success=2, n_total=2)
    assert rec.startswith("DEPLOY")


def test_rl_recommendation_rejects_deep_losses():
    from app.services.bots.ml_walk_forward_validator import _make_recommendation

    agg = {
        "metric_kind": "rl_return",
        "mean_oos_return_pct": -8.0,
        "total_oos_signals": 2,
        "positive_return_folds": 0,
    }
    stab = {"cv": 1.5, "trend": "declining"}
    rec = _make_recommendation(agg, stab, n_success=2, n_total=2)
    assert rec.startswith("REJECT")


def test_evaluate_oos_rl_env_greedy_episode():
    torch = pytest.importorskip("torch")
    from app.services.bots.ml_feature_engineering import SIGNAL_FEATURE_NAMES
    from app.services.bots.ml_walk_forward_validator import _evaluate_oos_rl_env
    from app.services.bots.rl_ppo_trainer import _build_actor_critic
    from app.services.bots.rl_trading_env import OBS_DIM

    rng = np.random.default_rng(0)
    candles = []
    price = 100.0
    for i in range(120):
        price *= 1.0 + float(rng.normal(0.0002, 0.01))
        candles.append({
            "time": 1_700_000_000 + i * 60,
            "open": price,
            "high": price * 1.001,
            "low": price * 0.999,
            "close": price,
            "volume": 1000.0,
            "ATR_14": 1.0,
        })

    model = _build_actor_critic(obs_dim=OBS_DIM, act_dim=4, hidden_dim=32)
    model.eval()
    n_feat = len(SIGNAL_FEATURE_NAMES)
    bundle = {
        "strategy": "RL_PPO_AGENT",
        "model": model,
        "scaler": {
            "feat_mean": np.zeros(n_feat).tolist(),
            "feat_std": np.ones(n_feat).tolist(),
        },
        "hidden_dim": 32,
    }
    out = _evaluate_oos_rl_env(candles, {"_wf_bundle": bundle}, {"_wf_mode": True})
    assert out["metric_kind"] == "rl_return"
    assert "return_pct" in out
    assert 0.0 <= out["accuracy"] <= 1.0
    assert out["total_bars"] == 120
    assert math.isfinite(out["return_pct"])
