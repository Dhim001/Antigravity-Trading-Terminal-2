"""Tests for heavy-job sidecar claim filtering and deferral."""

from __future__ import annotations

import os

from app.services.bots.backtester import BacktesterService
from app.services.bots.heavy_job_worker import (
    _build_context,
    api_worker_should_claim,
    defer_to_sidecar_only,
    job_is_heavy,
    request_is_heavy,
    sidecar_should_claim,
)
from app.services.bots.screener import MarketScreenerService


def test_request_is_heavy_ml_and_light():
    assert request_is_heavy({"strategy": "ML_SIGNAL_BOOST"}) is True
    assert request_is_heavy({"strategy": "RL_PPO_AGENT"}) is True
    assert request_is_heavy({"strategy": "MACD_RSI"}) is False


def test_claim_filters_with_sidecar_env(monkeypatch):
    monkeypatch.setenv("BACKTEST_HEAVY_SIDECAR", "1")
    monkeypatch.setenv("BACKTEST_SIDECAR_ALL", "0")
    heavy = {"request": {"strategy": "ML_SIGNAL_BOOST"}}
    light = {"request": {"strategy": "MACD_RSI"}}
    assert job_is_heavy(heavy) is True
    assert job_is_heavy(light) is False
    assert api_worker_should_claim(heavy) is False
    assert api_worker_should_claim(light) is True
    assert sidecar_should_claim(heavy) is True
    assert sidecar_should_claim(light) is False
    assert defer_to_sidecar_only({"strategy": "ML_SIGNAL_BOOST"}) is True
    assert defer_to_sidecar_only({"strategy": "MACD_RSI"}) is False


def test_sidecar_all_defers_everything(monkeypatch):
    monkeypatch.setenv("BACKTEST_HEAVY_SIDECAR", "1")
    monkeypatch.setenv("BACKTEST_SIDECAR_ALL", "1")
    light = {"request": {"strategy": "MACD_RSI"}}
    assert api_worker_should_claim(light) is False
    assert sidecar_should_claim(light) is True
    assert defer_to_sidecar_only({"strategy": "MACD_RSI"}) is True


def test_sidecar_disabled_runs_inline(monkeypatch):
    monkeypatch.setenv("BACKTEST_HEAVY_SIDECAR", "0")
    assert defer_to_sidecar_only({"strategy": "ML_SIGNAL_BOOST"}) is False
    assert api_worker_should_claim({"request": {"strategy": "ML_SIGNAL_BOOST"}}) is True


def test_build_context_screener_supports_process_candles():
    """Sidecar must wire a real MarketScreenerService — SimpleNamespace stubs break backtests."""
    ctx, feed, oms = _build_context()
    assert isinstance(ctx.backtester, BacktesterService)
    assert isinstance(ctx.backtester.screener, MarketScreenerService)
    assert callable(getattr(ctx.backtester.screener, "process_candles", None))
    assert feed is not None
    assert oms is not None
