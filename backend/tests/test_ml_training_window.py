"""Tests for ML Lab training_window_months → bar targets / trim."""

from __future__ import annotations

import time

from app.services.bots.ml_training_window import (
    bar_limit_for_training_window,
    parse_training_window_months,
    summarize_training_window,
    training_window_seconds,
    trim_candles_to_training_window,
)


def test_parse_training_window_months_buckets():
    assert parse_training_window_months({"training_window_months": 3}) == 3
    assert parse_training_window_months({"training_window_months": "12"}) == 12
    assert parse_training_window_months({}) == 3
    assert parse_training_window_months({"training_window_months": 7}) == 6  # nearest


def test_bar_limits_scale_with_window():
    b1 = bar_limit_for_training_window(1, purpose="train")
    b3 = bar_limit_for_training_window(3, purpose="train")
    b6 = bar_limit_for_training_window(6, purpose="train")
    b12 = bar_limit_for_training_window(12, purpose="train")
    assert b1 < b3 < b6 <= b12
    assert b1 >= 500
    assert b12 <= 50_000


def test_htf_train_honors_calendar_not_1m_scale():
    """6mo · 5m must not be crushed to ~8k (old 1m-cap × scale bug)."""
    b = bar_limit_for_training_window(6, timeframe="5m", purpose="train")
    # ideal ≈ 6*30*24*12 = 51840, hard max 50000
    assert b >= 40_000
    assert b <= 50_000


def test_htf_validate_leaner_than_train():
    train = bar_limit_for_training_window(6, timeframe="5m", purpose="train")
    validate = bar_limit_for_training_window(6, timeframe="5m", purpose="validate")
    assert validate < train
    assert validate >= 2_500


def test_skip_live_artifact_writes():
    from app.services.bots.ml_training_window import skip_live_artifact_writes

    assert skip_live_artifact_writes({"_wf_mode": True}) is True
    assert skip_live_artifact_writes({"skip_onnx_export": True}) is True
    assert skip_live_artifact_writes({}) is False
    assert skip_live_artifact_writes(None) is False


def test_validate_purpose_allows_more_than_train():
    train = bar_limit_for_training_window(3, purpose="train")
    validate = bar_limit_for_training_window(3, purpose="validate")
    assert validate >= train


def test_trim_candles_to_training_window():
    now = int(time.time())
    # 45 days of hourly-ish stamps
    candles = [
        {"time": now - d * 86400, "close": float(d)}
        for d in range(45, -1, -1)
    ]
    trimmed = trim_candles_to_training_window(candles, 1, now_ts=now)
    assert len(trimmed) < len(candles)
    assert all(int(c["time"]) >= now - training_window_seconds(1) for c in trimmed)


def test_summarize_training_window():
    now = int(time.time())
    candles = [{"time": now - 3600}, {"time": now}]
    meta = summarize_training_window(candles, 3, bar_limit=25000, timeframe="15m")
    assert meta["training_window_months"] == 3
    assert meta["timeframe"] == "15m"
    assert meta["bars"] == 2
    assert meta["bar_limit"] == 25000
    assert meta["span_days"] is not None


def test_model_storage_key_htf_separation():
    from app.services.bots.ml_model_artifacts import model_root_for, model_storage_key

    assert model_storage_key("ETHUSDT", "1m") == "ETHUSDT"
    assert model_storage_key("ETHUSDT", "15m") == "ETHUSDT__15M"
    assert model_storage_key("ETHUSDT", None) == "ETHUSDT"
    r1 = model_root_for("LSTM_DIRECTION", "ETHUSDT", "1m")
    r15 = model_root_for("LSTM_DIRECTION", "ETHUSDT", "15m")
    assert r1 and r15 and r1 != r15
    assert r15.endswith("ETHUSDT__15M")
