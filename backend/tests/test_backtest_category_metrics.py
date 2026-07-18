"""Tests for category metrics emission (ml_metrics / rl_data / agent_metrics)."""

from app.services.bots.backtest_category_metrics import (
    CategoryMetricsCollector,
    compute_alpha_decay,
    future_direction_label,
)


def test_future_direction_label():
    assert future_direction_label(100, 101) == "BUY"
    assert future_direction_label(100, 99) == "SELL"
    assert future_direction_label(100, 100.01) == "NONE"


def test_ml_metrics_confusion_and_confidence():
    c = CategoryMetricsCollector("ML_SIGNAL_BOOST")
    for i in range(10):
        c.record_bar(
            bar_index=i,
            signal_data={"signal": "BUY", "confidence": 0.7},
            signal="BUY",
            close=100.0,
            future_close=101.0,
            position_side=None,
            executed=False,
        )
    out = c.finalize(summary={"win_rate": 55, "sharpe_ratio": 1.2})
    assert "ml_metrics" in out
    m = out["ml_metrics"]
    assert m["confusion_matrix"][0][0] == 10  # true BUY, pred BUY
    assert m["accuracy"] == 1.0
    assert m["directional_predictions"] == 10
    assert m["prediction_counts"]["BUY"] == 10
    assert m.get("prediction_warning") is None
    assert m["confidence_distribution"]
    assert m.get("log_loss") is not None
    assert m["log_loss"] > 0


def test_ml_metrics_all_none_warns():
    c = CategoryMetricsCollector("ML_SIGNAL_BOOST")
    for i in range(8):
        # Flat next bar → actual NONE; gated signal always NONE
        c.record_bar(
            bar_index=i,
            signal_data={"signal": "NONE"},
            signal="NONE",
            close=100.0,
            future_close=100.01,
            position_side=None,
            executed=False,
        )
    m = c.finalize()["ml_metrics"]
    assert m["directional_predictions"] == 0
    assert m["prediction_counts"]["NONE"] == 8
    assert m.get("prediction_warning")
    assert m["majority_class_baseline"] == m["accuracy"]


def test_rl_data_episode_steps():
    c = CategoryMetricsCollector("RL_PPO_AGENT")
    for i in range(5):
        c.record_bar(
            bar_index=i,
            signal_data={
                "signal": "BUY" if i % 2 == 0 else "NONE",
                "confidence": 0.6,
                "rl_step": {
                    "observation": [0.1 * i, -0.2, 0.3],
                    "action": [1 if i % 2 == 0 else 0],
                    "reward": 0.01 * i,
                    "position": 1.0 if i % 2 == 0 else 0.0,
                },
            },
            signal="BUY" if i % 2 == 0 else "NONE",
            close=100.0,
            future_close=None,
            position_side="BUY" if i % 2 == 0 else None,
            executed=i % 2 == 0,
        )
    out = c.finalize()
    assert "rl_data" in out
    rl = out["rl_data"]
    assert rl["action_distribution"]["long"] >= 1
    assert len(rl["episode_steps"]) == 5
    assert len(rl["position_trajectory"]) == 5
    assert len(rl["reward_accumulation"]) == 5
    assert rl["episode_steps"][0]["observation"]
    assert out.get("ml_metrics", {}).get("confidence_distribution")


def test_load_feature_importance_accepts_top_features(tmp_path, monkeypatch):
    import json
    from app.services.bots import backtest_category_metrics as bcm

    safe = "SPY"
    data_dir = tmp_path / "data" / "ml_signal_models" / safe
    data_dir.mkdir(parents=True)
    meta = {
        "top_features": [
            {"name": "rsi", "importance": 0.4},
            {"name": "macd", "importance": 0.2},
        ],
    }
    (data_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr("app.config.BASE_DIR", str(tmp_path))

    fi = bcm.load_ml_feature_importance("ML_SIGNAL_BOOST", "SPY")
    assert fi and fi[0]["name"] == "rsi"
    assert fi[0]["category"] == "indicator"


def test_attach_is_vs_oos():
    from app.services.bots.backtest_category_metrics import (
        attach_is_vs_oos,
        is_vs_oos_from_windows,
    )

    pair = is_vs_oos_from_windows(
        {"summary": {"sharpe_ratio": 2.0}, "total_pnl": 100},
        {"summary": {"sharpe_ratio": 1.0}, "total_pnl": 40},
    )
    assert pair["is_sharpe"] == 2.0
    assert pair["oos_sharpe"] == 1.0
    result = {"summary": {}}
    attach_is_vs_oos(result, pair)
    assert result["ml_metrics"]["is_vs_oos"]["oos_pnl"] == 40


def test_is_vs_oos_requires_oos_side():
    """Plain backtests must not emit IS-only is_vs_oos (misleading empty OOS)."""
    from app.services.bots.backtest_category_metrics import CategoryMetricsCollector

    c = CategoryMetricsCollector("RL_PPO_AGENT")
    assert c._is_vs_oos(None, {"sharpe_ratio": 1.2, "total_pnl": 50}) is None
    assert c._is_vs_oos(
        {"oos_sharpe": 0.4, "oos_pnl": 10},
        {"sharpe_ratio": 1.2, "total_pnl": 50},
    ) == {
        "is_sharpe": 1.2,
        "oos_sharpe": 0.4,
        "is_pnl": 50,
        "oos_pnl": 10,
    }


def test_agent_metrics_funnel():
    c = CategoryMetricsCollector("CHART_AGENT")
    c.record_bar(
        bar_index=0,
        signal_data={"signal": "NONE", "reject_reason": "confidence 0.4 below min 0.55"},
        signal="NONE",
        close=100.0,
        future_close=None,
        position_side=None,
        executed=False,
        filter_bucket="confidence",
    )
    c.record_bar(
        bar_index=1,
        signal_data={"signal": "BUY", "confidence": 0.8},
        signal="BUY",
        close=100.0,
        future_close=None,
        position_side="BUY",
        executed=True,
    )
    c.record_closed_trade(confidence=0.8, regime="normal", pnl=12.0)
    out = c.finalize(filter_rejects={"confidence": 1}, summary={"trade_count": 1, "win_rate": 100})
    am = out["agent_metrics"]
    assert am["signals_filtered"] >= 1
    assert am["signals_executed"] >= 1
    assert am["gate_funnel"]
    assert am["confidence_calibration"]
    assert any(r["regime"] == "normal" for r in am["regime_performance"])


def test_compute_alpha_decay_from_equity():
    # Rising then flattening equity → measurable rolling Sharpe series
    curve = [{"time": i * 60, "equity": 10_000 + i * 2} for i in range(120)]
    # Inject late decay
    for i in range(80, 120):
        curve[i]["equity"] = curve[79]["equity"] - (i - 79) * 5
    decay = compute_alpha_decay(curve, window=20)
    assert decay is not None
    assert len(decay["rolling_sharpe"]) >= 4
    assert "early_sharpe" in decay
    assert "late_sharpe" in decay
    assert decay["window_bars"] == 20


def test_ml_finalize_includes_alpha_decay():
    c = CategoryMetricsCollector("ML_SIGNAL_BOOST")
    for i in range(5):
        c.record_bar(
            bar_index=i,
            signal_data={"signal": "BUY", "confidence": 0.7},
            signal="BUY",
            close=100.0,
            future_close=101.0,
            position_side=None,
            executed=False,
        )
    curve = [{"time": i * 86_400, "equity": 10_000 * (1.001 ** i)} for i in range(100)]
    out = c.finalize(equity_curve=curve, summary={"sharpe_ratio": 1.0})
    assert "alpha_decay" in out["ml_metrics"]
    assert out["ml_metrics"]["alpha_decay"]["rolling_sharpe"]
