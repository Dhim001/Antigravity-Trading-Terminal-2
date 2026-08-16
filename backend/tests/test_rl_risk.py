"""RL ATR risk, cost defaults, train guards, and payoff gate."""

from app.services.bots.rl_risk import (
    MIN_ENT_COEF,
    MIN_PROFIT_FACTOR,
    apply_rl_live_defaults,
    clamp_ent_coef,
    clamp_risk_usd,
    costs_are_applied,
    payoff_passes,
    resolve_rl_costs,
    resolve_rl_risk_usd,
    stop_take_prices,
    trade_payoff_stats,
)


def test_atr_stop_and_1_5r_target():
    dist, sl, tp = stop_take_prices("BUY", 100.0, 2.0)
    assert dist == 3.0
    assert sl == 97.0
    assert tp == 104.5
    dist, sl, tp = stop_take_prices("SELL", 100.0, 2.0)
    assert sl == 103.0
    assert tp == 95.5


def test_risk_usd_clamped_15_25():
    assert clamp_risk_usd(60) == 25.0
    assert clamp_risk_usd(5) == 15.0
    assert resolve_rl_risk_usd({}) == 20.0
    assert resolve_rl_risk_usd({"risk_per_trade_usd": 18}) == 18.0


def test_costs_default_not_zero():
    fee, slip = resolve_rl_costs({})
    assert fee == 10.0
    assert slip == 5.0
    # Missing keys mean costs were not explicitly configured (deploy gate blocks).
    assert not costs_are_applied({})
    assert not costs_are_applied({"fee_bps": 0, "slippage_bps": 0})
    assert costs_are_applied({"fee_bps": 10.0})
    assert costs_are_applied({"slippage_bps": 5.0})


def test_ent_coef_floor():
    assert clamp_ent_coef(0.001) == MIN_ENT_COEF
    assert clamp_ent_coef(0.02) == 0.02


def test_payoff_gate():
    ok, _ = payoff_passes(avg_win=8, avg_loss=-6, profit_factor=1.4)
    assert ok
    ok, msg = payoff_passes(avg_win=6, avg_loss=-27, profit_factor=0.11)
    assert not ok
    assert "avg win" in msg
    ok, msg = payoff_passes(avg_win=10, avg_loss=-8, profit_factor=1.2)
    assert not ok
    assert f"{MIN_PROFIT_FACTOR:.2f}" in msg


def test_trade_payoff_stats():
    stats = trade_payoff_stats([0.02, -0.01, 0.03, -0.01])
    assert stats["n_wins"] == 2
    assert stats["n_losses"] == 2
    assert stats["avg_win"] > stats["avg_loss"]  # avg_loss is negative
    assert stats["profit_factor"] > 1.3


def test_env_hits_atr_stop():
    from app.services.bots.rl_trading_env import ACTION_BUY, TradingEnv

    candles = []
    for i in range(40):
        c = 100.0 - i * 2.0
        candles.append({
            "time": 1_700_000_000 + i * 300,
            "open": c + 0.5,
            "high": c + 0.5,
            "low": c - 3.5,
            "close": c,
            "volume": 1000.0,
            "ATR_14": 2.0,
        })
    env = TradingEnv(
        candles,
        feature_lookback=5,
        config={"env_seed": 0, "max_episode_steps": 30, "fee_bps": 10, "slippage_bps": 5},
    )
    env.reset()
    env.step(ACTION_BUY)
    hit = False
    for _ in range(8):
        _, _, _, info = env.step(0)
        if info.get("barrier") == "atr_stop":
            hit = True
            break
    assert hit
    stats = env.episode_stats()
    assert stats["fee_bps"] == 10
    assert stats["n_losses"] >= 1


def test_apply_rl_live_defaults_strips_percent_stops():
    out = apply_rl_live_defaults({
        "trailing_stop_percent": 2,
        "take_profit_percent": 3,
        "allocation": 3000,
    })
    assert "trailing_stop_percent" not in out
    assert "take_profit_percent" not in out
    assert out["risk_per_trade_usd"] == 20.0
    assert out["atr_stop_mult"] == 1.5
    assert out["paper_first"] is True
    assert out["rl_percent_stops"] is False


def test_env_episode_stats_include_payoff():
    from app.services.bots.rl_trading_env import ACTION_BUY, ACTION_CLOSE, ACTION_HOLD, TradingEnv

    candles = []
    for i in range(40):
        c = 100.0 + i * 0.05
        candles.append({
            "time": 1_700_000_000 + i * 300,
            "open": c,
            "high": c + 0.2,
            "low": c - 0.2,
            "close": c,
            "volume": 1000.0,
            "ATR_14": 4.0,
        })
    env = TradingEnv(candles, feature_lookback=5, config={"env_seed": 0, "use_atr_stops": False})
    env.reset()
    env.step(ACTION_BUY)
    env.step(ACTION_HOLD)
    env.step(ACTION_CLOSE)
    stats = env.episode_stats()
    assert "profit_factor" in stats
    assert stats["closed_trades"] == 1
