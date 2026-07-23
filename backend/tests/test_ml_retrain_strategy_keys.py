"""Retrain queue: Lab trains ML only; TA/agent bots map to META_LABEL."""

from app.services.bots.ml_retrain_scheduler import (
    META_LABEL_STRATEGY,
    MlRetrainScheduler,
    lab_train_unsupported_error,
    normalize_retrain_strategy,
)


def test_normalize_retrain_strategy_maps_ta_and_agents_to_meta_label():
    assert normalize_retrain_strategy("MACD_RSI") == META_LABEL_STRATEGY
    assert normalize_retrain_strategy("ABSORPTION_AGENT") == META_LABEL_STRATEGY
    assert normalize_retrain_strategy("CHART_AGENT") == META_LABEL_STRATEGY
    assert normalize_retrain_strategy("ML_SIGNAL_BOOST") == "ML_SIGNAL_BOOST"
    assert normalize_retrain_strategy("RL_PPO_AGENT") == "RL_PPO_AGENT"


def test_request_retrain_does_not_queue_macd_as_lab_strategy():
    s = MlRetrainScheduler()
    out = s.request_retrain(
        "MACD_RSI", "ETHUSDT", "decay", "alpha_decay", timeframe="5m",
    )
    assert out["queued"] is True
    assert "META_LABEL" in out["key"]
    pending = s.get_pending(ml_only=False)
    assert len(pending) == 1
    entry = next(iter(pending.values()))
    assert entry["strategy"] == META_LABEL_STRATEGY
    assert entry.get("bot_strategy") == "MACD_RSI"
    assert s.get_pending(ml_only=True) == {}


def test_drop_stale_lab_incompatible_pending_removes_legacy_ta_keys():
    s = MlRetrainScheduler()
    s._pending["ETHUSDT:MACD_RSI:5m"] = {
        "strategy": "MACD_RSI",
        "symbol": "ETHUSDT",
        "requested_at": "2026-07-22T00:00:00Z",
    }
    s._pending["ETHUSDT:ML_SIGNAL_BOOST:5m"] = {
        "strategy": "ML_SIGNAL_BOOST",
        "symbol": "ETHUSDT",
        "requested_at": "2026-07-22T00:00:00Z",
    }
    assert s.drop_stale_lab_incompatible_pending() == 1
    assert "MACD_RSI" not in s._pending
    assert "ETHUSDT:ML_SIGNAL_BOOST:5m" in s._pending


def test_lab_train_unsupported_message_mentions_lab_and_strategy():
    msg = lab_train_unsupported_error("MACD_RSI")
    assert "Lab training not supported" in msg
    assert "MACD_RSI" in msg
    assert "meta-label" in msg.lower()
