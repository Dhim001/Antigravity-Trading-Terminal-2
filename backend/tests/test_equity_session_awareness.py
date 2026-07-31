"""Equity session awareness — RTH features, candle filter, PPO entry mask."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.altdata.calendar import (
    filter_equity_rth_candles,
    session_features_for_bar,
)
from app.services.bots.ml_feature_engineering import (
    SIGNAL_FEATURE_NAMES,
    SIGNAL_FEATURE_VERSION,
    bar_to_signal_features,
)
from app.services.bots.rl_trading_env import (
    ACTION_BUY,
    ACTION_HOLD,
    ACTION_SELL,
    TradingEnv,
)

NY = ZoneInfo("America/New_York")


def _ny_epoch(year, month, day, hour, minute=0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=NY).timestamp()


def test_session_features_crypto_always_open():
    after_close = _ny_epoch(2026, 7, 28, 18, 0)
    feats = session_features_for_bar("BTCUSDT", after_close)
    assert feats["is_rth"] == 1.0


def test_session_features_equity_after_close():
    after_close = _ny_epoch(2026, 7, 28, 18, 0)  # Tuesday
    feats = session_features_for_bar("AAPL", after_close)
    assert feats["is_rth"] == 0.0
    assert feats["minutes_from_open_norm"] == 0.0


def test_session_features_equity_mid_rth():
    mid = _ny_epoch(2026, 7, 28, 11, 30)
    feats = session_features_for_bar("AAPL", mid)
    assert feats["is_rth"] == 1.0
    assert 0.0 < feats["minutes_from_open_norm"] < 1.0


def test_filter_equity_rth_candles_keeps_crypto():
    bars = [
        {"time": _ny_epoch(2026, 7, 28, 18, 0), "close": 1},
        {"time": _ny_epoch(2026, 7, 28, 11, 0), "close": 2},
    ]
    out = filter_equity_rth_candles("ETHUSDT", bars)
    assert len(out) == 2


def test_filter_equity_rth_candles_drops_after_hours(monkeypatch):
    monkeypatch.setattr(
        "app.services.altdata.calendar._load_holiday_map",
        lambda: {},
    )
    bars = [
        {"time": _ny_epoch(2026, 7, 28, 18, 0), "close": 1},  # after close
        {"time": _ny_epoch(2026, 7, 28, 11, 0), "close": 2},  # RTH
        {"time": _ny_epoch(2026, 7, 25, 11, 0), "close": 3},  # Saturday
    ]
    out = filter_equity_rth_candles("AAPL", bars)
    assert len(out) == 1
    assert out[0]["close"] == 2


def test_bar_to_signal_features_includes_session_keys():
    ts = _ny_epoch(2026, 7, 28, 11, 0)
    feats = bar_to_signal_features(
        {"close": 100, "open": 99, "high": 101, "low": 98, "volume": 1000, "time": ts},
        symbol="AAPL",
    )
    assert SIGNAL_FEATURE_VERSION == 4
    for key in ("is_rth", "minutes_from_open_norm", "et_hour_sin", "et_hour_cos"):
        assert key in feats
        assert key in SIGNAL_FEATURE_NAMES
    assert feats["is_rth"] == 1.0


def test_ppo_masks_flat_entries_outside_rth():
    # Synthetic env: mark every bar as after-hours via allow_entry flags.
    candles = []
    base = _ny_epoch(2026, 7, 27, 11, 0)
    for i in range(25):
        candles.append({
            "time": base + i * 60,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0 + i * 0.01,
            "volume": 1000,
            "_symbol": "AAPL",
        })
    env = TradingEnv(candles, config={"symbol": "AAPL"}, feature_lookback=5)
    env.reset()
    # Force closed-session mask regardless of wall clock / holidays.
    env._allow_entry = [False] * env.n_candles
    env._position_side = 0

    masked = env._mask_entry_action(ACTION_BUY)
    assert masked == ACTION_HOLD
    masked = env._mask_entry_action(ACTION_SELL)
    assert masked == ACTION_HOLD

    obs, reward, done, info = env.step(ACTION_BUY)
    assert info.get("entry_masked") is True
    assert info.get("action") == ACTION_HOLD
    assert env._position_side == 0
