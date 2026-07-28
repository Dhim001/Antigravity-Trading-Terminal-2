"""Tests for Phase 1.3 — latency-aware fills + sqrt market impact."""

from __future__ import annotations

import math

import pytest

from app.services.bots.backtest_costs import (
    CostModel,
    estimate_latency_slippage_bps,
    entry_fill_price,
    exit_fill_price,
    parse_cost_config,
    trade_fee,
)


# --- Latency slippage estimator -------------------------------------------


def test_latency_estimator_zero_when_no_atr():
    assert estimate_latency_slippage_bps(0.0, 60.0) == 0.0


def test_latency_estimator_zero_when_no_latency():
    assert estimate_latency_slippage_bps(0.001, 0.0) == 0.0


def test_latency_estimator_scales_with_sqrt_time():
    # Doubling latency should increase slippage by sqrt(2) ≈ 1.414
    base = estimate_latency_slippage_bps(0.001, 60.0, bar_seconds=60.0)
    doubled = estimate_latency_slippage_bps(0.001, 120.0, bar_seconds=60.0)
    assert base > 0
    assert doubled == pytest.approx(base * math.sqrt(2), rel=1e-6)


def test_latency_estimator_full_bar_latency_equals_atr():
    # latency == bar_seconds → adverse move = atr_pct → bps = atr_pct * 10000
    bps = estimate_latency_slippage_bps(0.001, 60.0, bar_seconds=60.0)
    assert bps == pytest.approx(10.0, rel=1e-6)  # 0.001 * 10000 = 10 bps


def test_latency_estimator_clamps_to_500bps():
    bps = estimate_latency_slippage_bps(1.0, 3600.0, bar_seconds=60.0)
    assert bps == 500.0


def test_latency_estimator_safety_factor_scales():
    base = estimate_latency_slippage_bps(0.001, 60.0, bar_seconds=60.0)
    doubled = estimate_latency_slippage_bps(0.001, 60.0, bar_seconds=60.0, safety_factor=2.0)
    assert doubled == pytest.approx(base * 2.0, rel=1e-6)


# --- CostModel latency integration ----------------------------------------


def test_cost_model_default_latency_is_zero():
    cm = CostModel.from_config({})
    assert cm.latency_slippage_bps == 0.0


def test_cost_model_loads_latency_from_config():
    cm = CostModel.from_config({"latency_slippage_bps": 5.0})
    assert cm.latency_slippage_bps == 5.0


def test_cost_model_latency_adds_to_effective_slippage():
    cm = CostModel(slippage_bps=2.0, latency_slippage_bps=3.0)
    eff = cm.effective_slippage_bps()
    assert eff >= 5.0  # base 2 + latency 3


def test_cost_model_latency_applies_even_with_zero_base_slippage():
    cm = CostModel(slippage_bps=0.0, latency_slippage_bps=4.0)
    eff = cm.effective_slippage_bps()
    assert eff >= 4.0


def test_cost_model_latency_independent_of_volume_participation():
    # Latency cost is time-based, not size-based — it should apply regardless
    cm = CostModel(
        slippage_bps=1.0,
        latency_slippage_bps=2.0,
        volume_participation=True,
    )
    small = cm.effective_slippage_bps(order_notional=100, bar_volume_notional=1_000_000)
    large = cm.effective_slippage_bps(order_notional=100_000, bar_volume_notional=1_000_000)
    # Both should include the +2 bps latency component
    assert small >= 2.0
    assert large >= 2.0
    # Large order pays more impact, but both pay the same latency
    assert large >= small


def test_cost_model_fill_price_includes_latency():
    cm = CostModel(slippage_bps=0.0, latency_slippage_bps=10.0)
    fill = cm.fill_price(100.0, "BUY")
    # 10 bps slippage on a BUY → 100 * (1 + 0.001) = 100.1
    assert fill == pytest.approx(100.1, rel=1e-6)


def test_cost_model_to_dict_includes_latency():
    cm = CostModel(latency_slippage_bps=3.5)
    d = cm.to_dict()
    assert "latency_slippage_bps" in d
    assert d["latency_slippage_bps"] == 3.5


# --- Legacy flat helpers unchanged ---------------------------------------


def test_parse_cost_config_latency_ignored_in_legacy():
    # parse_cost_config only returns (slippage, fee); latency lives on CostModel
    slip, fee = parse_cost_config({"latency_slippage_bps": 5.0, "slippage_bps": 2.0})
    assert slip == 2.0
    assert fee == 0.0


def test_entry_fill_price_unchanged():
    assert entry_fill_price(100.0, "BUY", 10.0) == pytest.approx(100.1)
    assert entry_fill_price(100.0, "SELL", 10.0) == pytest.approx(99.9)


def test_exit_fill_price_unchanged():
    assert exit_fill_price(100.0, "SELL", 10.0) == pytest.approx(99.9)
    assert exit_fill_price(100.0, "BUY", 10.0) == pytest.approx(100.1)


def test_trade_fee_unchanged():
    assert trade_fee(10_000.0, 10.0) == pytest.approx(10.0)
    assert trade_fee(10_000.0, 0.0) == 0.0


# --- Sqrt market impact (existing) still works ----------------------------


def test_sqrt_impact_scales_with_participation():
    cm = CostModel(
        slippage_bps=10.0,
        volume_participation=True,
        participation_exponent=0.5,
    )
    small = cm.effective_slippage_bps(order_notional=1_000, bar_volume_notional=1_000_000)
    large = cm.effective_slippage_bps(order_notional=100_000, bar_volume_notional=1_000_000)
    # 0.1% vs 10% participation → large should pay more
    assert large > small
