"""Strategy readiness diagnostics for Backtest Lab warnings."""

from __future__ import annotations

from collections import Counter

from app.services.bots.strategy_readiness import build_strategy_readiness, static_trade_notes


def test_no_signals_flags_agent_strategy():
    out = build_strategy_readiness(
        "CVD_DIVERGENCE",
        trade_count=0,
        bars_evaluated=200,
        signal_counts={"BUY": 0, "SELL": 0, "NONE": 200},
        reject_reasons=Counter({"no CVD divergence": 180, "warming up": 20}),
    )
    assert out["ok"] is False
    assert out["status"] == "no_signals"
    assert out["message"]
    assert out["top_reject_reasons"][0]["reason"] == "no CVD divergence"
    assert any("CVD" in n for n in out["notes"])


def test_signals_blocked_when_directional_but_no_fills():
    out = build_strategy_readiness(
        "ORDERFLOW_IMBALANCE",
        trade_count=0,
        bars_evaluated=100,
        signal_counts={"BUY": 12, "SELL": 0, "NONE": 88},
    )
    assert out["status"] == "signals_blocked"
    assert out["ok"] is False


def test_long_only_explains_short_heavy_zero_fills():
    out = build_strategy_readiness(
        "TRANSFORMER_SIGNAL",
        trade_count=0,
        bars_evaluated=1700,
        signal_counts={"BUY": 311, "SELL": 923, "NONE": 522},
        blocked_entries=923,
        blocked_events=[{"kind": "direction_mode"}] * 50,
        direction_mode="LONG_ONLY",
        sim_mode="live_aligned",
    )
    assert out["status"] == "signals_blocked"
    assert any("LONG_ONLY" in w for w in out["warnings"])
    assert out["top_block_kinds"][0]["kind"] == "direction_mode"


def test_ok_when_trades_exist():
    out = build_strategy_readiness(
        "ABSORPTION_AGENT",
        trade_count=5,
        bars_evaluated=300,
        signal_counts={"BUY": 8, "SELL": 2, "NONE": 290},
    )
    assert out["ok"] is True
    assert out["status"] == "ok"


def test_broken_on_evaluate_errors():
    out = build_strategy_readiness(
        "VPOC_REVERSION",
        trade_count=0,
        bars_evaluated=80,
        signal_counts={"NONE": 80},
        reject_reasons=Counter({"evaluate error: NameError": 80}),
        evaluate_errors=80,
    )
    assert out["status"] == "broken"
    assert out["evaluate_errors"] == 80


def test_static_notes_for_orderflow():
    notes = static_trade_notes("ORDERFLOW_IMBALANCE")
    assert notes and "proxy" in notes[0].lower()
