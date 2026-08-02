"""Tests for time-based backtest progress helpers."""

from __future__ import annotations

import time

from app.services.bots.backtest_progress import ProgressThrottle, enrich_bar_progress
from app.services.bots.backtest_perf import estimate_backtest_seconds, is_heavy_backtest


def test_progress_throttle_emits_on_interval(monkeypatch):
    emitted = []
    clock = {"t": 100.0}

    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
    throttle = ProgressThrottle(lambda d, tot: emitted.append((d, tot)), total=1000, stride=200, min_interval_sec=2.0)

    throttle(0, 1000)
    assert len(emitted) == 1

    clock["t"] = 100.5
    throttle(10, 1000)  # too soon, not stride
    assert len(emitted) == 1

    clock["t"] = 102.1
    throttle(15, 1000)  # time gate
    assert emitted[-1] == (15, 1000)

    clock["t"] = 102.2
    throttle(250, 1000)  # stride gate
    assert emitted[-1] == (250, 1000)


def test_progress_throttle_heartbeat_same_done(monkeypatch):
    """Same bar after >=5s must re-emit so elapsed_sec can refresh the UI stall fp."""
    emitted = []
    clock = {"t": 100.0}

    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
    throttle = ProgressThrottle(
        lambda d, tot: emitted.append((d, tot)),
        total=1000,
        stride=200,
        min_interval_sec=2.0,
    )

    throttle(0, 1000)
    assert len(emitted) == 1

    clock["t"] = 103.0
    throttle(0, 1000)  # same done, <5s since last emit → still suppressed
    assert len(emitted) == 1

    clock["t"] = 105.1
    throttle(0, 1000)  # heartbeat
    assert len(emitted) == 2
    assert emitted[-1] == (0, 1000)


def test_enrich_bar_progress_includes_eta():
    payload = enrich_bar_progress(500, 1000, elapsed_sec=10.0, phase="simulate")
    assert payload["bar"] == 500
    assert payload["bars"] == 1000
    assert payload["bars_per_sec"] == 50.0
    assert payload["eta_sec"] == 10
    assert "left" in payload["message"]


def test_estimate_rl_much_slower_than_ta():
    ta = estimate_backtest_seconds(days=54, strategy="EMA_CROSS")
    rl = estimate_backtest_seconds(days=54, strategy="RL_PPO_AGENT")
    assert rl > ta * 5
    assert rl > 600  # multi-minute floor for long RL 1m runs


def test_estimate_matches_observed_long_ml_runtime():
    # Real run: BTCUSDT ML_SIGNAL_BOOST 54d reached 51570/76412 bars in ~49 min
    # (~17.5 bars/s) while the old estimate claimed 71s.
    est = estimate_backtest_seconds(days=54, strategy="ML_SIGNAL_BOOST")
    assert est > 1800  # at least 30 min, not ~1 min


def test_rl_is_always_heavy():
    assert is_heavy_backtest(days=3, strategy="RL_PPO_AGENT") is True
