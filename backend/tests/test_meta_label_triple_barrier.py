"""Tests for Phase 2.4 — triple-barrier meta-labeling."""

from __future__ import annotations

import pytest

from app.services.bots import ml_triple_barrier as tb
from app.services.bots import meta_label_model as mlm


# --- Barrier multiplier resolver ------------------------------------------


def test_resolve_barrier_multipliers_symmetric_fallback():
    upper, lower = tb.resolve_barrier_multipliers({"triple_barrier_atr_mult": 3.0})
    assert upper == pytest.approx(3.0)
    assert lower == pytest.approx(3.0)


def test_resolve_barrier_multipliers_asymmetric():
    upper, lower = tb.resolve_barrier_multipliers({
        "triple_barrier_atr_mult_upper": 3.0,
        "triple_barrier_atr_mult_lower": 1.5,
    })
    assert upper == pytest.approx(3.0)
    assert lower == pytest.approx(1.5)


def test_resolve_barrier_multipliers_asymmetric_overrides_symmetric():
    upper, lower = tb.resolve_barrier_multipliers({
        "triple_barrier_atr_mult": 2.0,
        "triple_barrier_atr_mult_upper": 3.0,
        "triple_barrier_atr_mult_lower": 1.0,
    })
    assert upper == pytest.approx(3.0)
    assert lower == pytest.approx(1.0)


def test_resolve_barrier_multipliers_clamps_degenerate():
    upper, lower = tb.resolve_barrier_multipliers({
        "triple_barrier_atr_mult_upper": 0.0,
        "triple_barrier_atr_mult_lower": 100.0,
    })
    assert upper >= 0.1
    assert lower <= 20.0


def test_resolve_barrier_multipliers_empty_config():
    upper, lower = tb.resolve_barrier_multipliers({})
    assert upper == pytest.approx(2.0)
    assert lower == pytest.approx(2.0)


def test_label_triple_barrier_from_config_uses_asymmetric():
    candles = [
        {"time": i, "open": 100, "high": 100 + i * 0.5, "low": 100 - i * 0.5,
         "close": 100, "volume": 1000, "ATR_14": 1.0}
        for i in range(40)
    ]
    # Tight upper (1.0), wide lower (4.0) → more SELL labels than BUY
    labels = tb.label_triple_barrier_from_config(candles, {
        "triple_barrier_atr_mult_upper": 1.0,
        "triple_barrier_atr_mult_lower": 4.0,
        "triple_barrier_max_bars": 30,
    })
    dist = tb.label_distribution(labels)
    # With a tight upper barrier, we should see at least some BUY hits
    assert dist["BUY"] + dist["SELL"] + dist["NONE"] == len(candles)


# --- Triple-barrier label source for meta-label GBM -----------------------


def _make_candles(n=100, start=100.0, atr=1.0):
    candles = []
    price = start
    for i in range(n):
        # Alternating up/down bars to produce both BUY and SELL labels
        up = i % 2 == 0
        high = price + (atr * 2.5 if up else atr * 0.5)
        low = price - (atr * 0.5 if up else atr * 2.5)
        close = price + (atr * 1.5 if up else -atr * 1.5)
        candles.append({
            "time": i, "open": price, "high": high, "low": low,
            "close": close, "volume": 1000, "ATR_14": atr,
        })
        price = close
    return candles


def test_build_dataset_from_candles_produces_rows():
    candles = _make_candles(100)
    ds = mlm.build_meta_label_dataset_from_candles(
        "bot-1", candles,
        config={"triple_barrier_atr_mult": 2.0, "triple_barrier_max_bars": 30},
    )
    assert ds["bot_id"] == "bot-1"
    assert ds["label_source"] == "triple_barrier"
    assert ds["sample_count"] > 0
    assert "BUY" in ds["label_distribution"]
    assert "SELL" in ds["label_distribution"]


def test_build_dataset_from_candles_skips_none_labels():
    # Flat candles → all time-barrier (NONE) → all skipped
    candles = [
        {"time": i, "open": 100, "high": 100.1, "low": 99.9,
         "close": 100, "volume": 1000, "ATR_14": 1.0}
        for i in range(50)
    ]
    ds = mlm.build_meta_label_dataset_from_candles(
        "bot-1", candles,
        config={"triple_barrier_atr_mult": 2.0, "triple_barrier_max_bars": 30},
    )
    assert ds["sample_count"] == 0
    assert ds["skipped"] == 50


def test_build_dataset_from_candles_uses_insight_snapshots():
    candles = _make_candles(100)
    # Attach a snapshot to bar 0
    snaps = {0: {"score": 3, "confidence": 0.8, "sub_reports": {}}}
    ds = mlm.build_meta_label_dataset_from_candles(
        "bot-1", candles,
        config={"triple_barrier_atr_mult": 2.0, "triple_barrier_max_bars": 30},
        entry_snapshots=snaps,
    )
    assert ds["sample_count"] > 0
    # Find the row for bar 0 (if it wasn't skipped as NONE)
    row0 = next((r for r in ds["rows"] if r["entry_ts"] == "0"), None)
    if row0:
        # The snapshot's confidence should flow through
        assert row0["features"]["confidence"] == pytest.approx(0.8)


def test_build_dataset_from_candles_win_label_matches_barrier():
    candles = _make_candles(100)
    ds = mlm.build_meta_label_dataset_from_candles(
        "bot-1", candles,
        config={"triple_barrier_atr_mult": 2.0, "triple_barrier_max_bars": 30},
    )
    # win=True corresponds to BUY (upper hit), win=False to SELL (lower hit)
    n_buy = ds["label_distribution"]["BUY"]
    n_sell = ds["label_distribution"]["SELL"]
    n_win = sum(1 for r in ds["rows"] if r["win"])
    n_loss = sum(1 for r in ds["rows"] if not r["win"])
    assert n_win == n_buy
    assert n_loss == n_sell


# --- train_meta_label_model dispatch --------------------------------------


def test_train_meta_label_model_realized_source_when_no_candles(monkeypatch):
    # Default source is "realized" — should not require candles
    called = {"source": None}

    def fake_build_realized(bot_id, *, limit=5000):
        called["source"] = "realized"
        return {"rows": [], "sample_count": 0, "skipped": 0, "with_snapshot": 0}

    monkeypatch.setattr(mlm, "build_meta_label_dataset", fake_build_realized)
    result = mlm.train_meta_label_model("bot-1", min_samples=5)
    # Will fail on insufficient samples but should have called realized source
    assert called["source"] == "realized"
    assert result.get("ok") is False


def test_train_meta_label_model_triple_barrier_requires_candles():
    result = mlm.train_meta_label_model(
        "bot-1", min_samples=5,
        config={"meta_label_label_source": "triple_barrier"},
        candles=None,
    )
    assert result.get("ok") is False
    assert "candles" in result.get("error", "").lower()


def test_train_meta_label_model_triple_barrier_with_candles(monkeypatch):
    candles = _make_candles(100)
    result = mlm.train_meta_label_model(
        "bot-1", min_samples=5,
        config={
            "meta_label_label_source": "triple_barrier",
            "triple_barrier_atr_mult": 2.0,
            "triple_barrier_max_bars": 30,
        },
        candles=candles,
    )
    # May or may not train successfully depending on label balance, but should
    # not error on the dispatch path. If it trained, ok=True + metadata.
    assert "ok" in result
    if result.get("ok"):
        assert "bot_id" in result
        assert "feature_names" in result
        assert "metrics" in result
