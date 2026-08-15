"""DQ train-gate integrity — mild crypto empty-minutes must not hard-block."""

from app.services.bots.ml_train_executor import (
    _assess_candle_integrity,
    _dq_train_should_block,
)


def _bars(n: int, step: int = 60, *, skip_every: int | None = None, start: int = 1_700_000_000):
    """Build 1m-like candles; optionally skip every Nth bar (empty minutes)."""
    out = []
    t = start
    i = 0
    while len(out) < n:
        i += 1
        if skip_every and i % skip_every == 0:
            t += step
            continue
        out.append({"time": t, "close": 1.0})
        t += step
    return out


def test_mild_empty_minutes_do_not_hard_block():
    # ~20% empty minutes → mild gap_rate ≈ 0.20 (matches ADA Apply & Retrain failure).
    candles = _bars(5000, skip_every=5)
    dq = _assess_candle_integrity(candles, "1m")
    assert dq["gap_rate"] > 0.05
    assert dq["severe_gap_rate"] < 0.05
    assert dq["missing_frac"] < 0.40
    # Default warn mode never hard-blocks.
    block, _ = _dq_train_should_block(dq, {})
    assert block is False
    # Explicit true still allows mild gaps (coverage/severe only).
    block, _ = _dq_train_should_block(dq, {"dq_train_gate": True})
    assert block is False


def test_severe_holes_hard_block():
    # Large multi-hour holes every 50 bars.
    candles = []
    t = 1_700_000_000
    for i in range(500):
        candles.append({"time": t, "close": 1.0})
        t += 60
        if i % 50 == 49:
            t += 60 * 120  # 2h hole
    dq = _assess_candle_integrity(candles, "1m")
    block, reason = _dq_train_should_block(dq, {"dq_train_gate": True})
    assert block is True
    assert "severe_gap_rate" in reason or "missing_frac" in reason


def test_strict_mode_blocks_mild_gaps():
    candles = _bars(2000, skip_every=5)
    dq = _assess_candle_integrity(candles, "1m")
    block, _ = _dq_train_should_block(dq, {"dq_train_gate": "strict"})
    assert block is True


def test_gate_off_never_blocks():
    candles = _bars(100, skip_every=2)
    dq = _assess_candle_integrity(candles, "1m")
    block, _ = _dq_train_should_block(dq, {"dq_train_gate": False})
    assert block is False


def test_clean_series_passes():
    candles = _bars(1000)
    dq = _assess_candle_integrity(candles, "1m")
    assert dq["gap_rate"] == 0.0
    assert dq["missing_frac"] == 0.0
    block, _ = _dq_train_should_block(dq, {"dq_train_gate": True})
    assert block is False
