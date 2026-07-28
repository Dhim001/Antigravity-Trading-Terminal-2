"""Unit tests for execution_algos (VWAP/POV slicing)."""

import asyncio

import pytest

from app.services.bots.execution_algos import (
    DEFAULT_VWAP_SLICES,
    DEFAULT_POV_RATE,
    DEFAULT_SLICE_INTERVAL_SEC,
    MAX_SLICES,
    build_slices,
    execute_sliced_order,
    resolve_execution_algo,
    slice_pov,
    slice_twap,
    slice_vwap,
)


# ── TWAP ────────────────────────────────────────────────────────────────────


def test_slice_twap_equal_weights():
    slices = slice_twap(100.0, n_slices=4, interval_sec=30)
    assert len(slices) == 4
    assert all(abs(s.quantity - 25.0) < 1e-9 for s in slices)
    assert slices[0].time_offset_sec == 0
    assert slices[1].time_offset_sec == 30
    assert slices[3].time_offset_sec == 90
    assert sum(s.weight for s in slices) == pytest.approx(1.0)


def test_slice_twap_zero_qty_returns_empty():
    assert slice_twap(0.0, n_slices=4) == []
    assert slice_twap(-5.0, n_slices=4) == []


def test_slice_twap_clamps_to_max_slices():
    slices = slice_twap(100.0, n_slices=1000)
    assert len(slices) == MAX_SLICES


# ── VWAP ────────────────────────────────────────────────────────────────────


def test_slice_vwap_no_profile_falls_back_to_twap():
    slices = slice_vwap(100.0, n_slices=5)
    assert len(slices) == 5
    assert all(abs(s.quantity - 20.0) < 1e-9 for s in slices)


def test_slice_vwap_with_u_shape_profile():
    # U-shape: heavy at open/close, light midday
    profile = [10.0, 2.0, 1.0, 2.0, 10.0]
    slices = slice_vwap(100.0, volume_profile=profile, n_slices=5, interval_sec=60)
    assert len(slices) == 5
    # First and last should be larger than middle
    assert slices[0].quantity > slices[2].quantity
    assert slices[-1].quantity > slices[2].quantity
    assert sum(s.quantity for s in slices) == pytest.approx(100.0)
    assert sum(s.weight for s in slices) == pytest.approx(1.0)


def test_slice_vwap_resamples_profile_to_n_slices():
    # 10-element profile resampled to 5 slices
    profile = [1.0] * 10
    slices = slice_vwap(50.0, volume_profile=profile, n_slices=5)
    assert len(slices) == 5
    assert all(abs(s.quantity - 10.0) < 1e-9 for s in slices)


# ── POV ─────────────────────────────────────────────────────────────────────


def test_slice_pov_sizes_to_participation_rate():
    bar_volumes = [1000.0, 2000.0, 500.0]
    slices = slice_pov(
        total_qty=300.0,
        bar_volumes=bar_volumes,
        participation_rate=0.10,
        interval_sec=60,
    )
    # First slice: 10% of 1000 = 100
    # Second: 10% of 2000 = 200, but only 200 left → 200
    # Third: nothing left
    assert len(slices) == 2
    assert slices[0].quantity == pytest.approx(100.0)
    assert slices[1].quantity == pytest.approx(200.0)
    assert sum(s.quantity for s in slices) == pytest.approx(300.0)


def test_slice_pov_dumps_remainder_into_last_slice_when_bars_run_out():
    bar_volumes = [100.0, 100.0]
    slices = slice_pov(
        total_qty=500.0,
        bar_volumes=bar_volumes,
        participation_rate=0.10,
    )
    # First: 10, Second: 10, remainder 480 dumped into last
    assert len(slices) == 2
    assert slices[0].quantity == pytest.approx(10.0)
    assert slices[-1].quantity == pytest.approx(490.0)
    assert sum(s.quantity for s in slices) == pytest.approx(500.0)


def test_slice_pov_empty_inputs():
    assert slice_pov(100.0, bar_volumes=[]) == []
    assert slice_pov(0.0, bar_volumes=[100.0]) == []
    assert slice_pov(100.0, bar_volumes=[100.0], participation_rate=0) == []


def test_slice_pov_clamps_rate():
    slices = slice_pov(1000.0, bar_volumes=[10000.0], participation_rate=5.0)
    # rate clamped to 1.0 → slice = 10000, but capped at 1000
    assert slices[0].quantity == pytest.approx(1000.0)


# ── Config resolver ────────────────────────────────────────────────────────


def test_resolve_execution_algo_defaults_to_single():
    assert resolve_execution_algo(None) == "single"
    assert resolve_execution_algo({}) == "single"
    assert resolve_execution_algo({"execution_algo": "unknown"}) == "single"


def test_resolve_execution_algo_known_values():
    assert resolve_execution_algo({"execution_algo": "vwap"}) == "vwap"
    assert resolve_execution_algo({"execution_algo": "POV"}) == "pov"
    assert resolve_execution_algo({"execution_algo": "single"}) == "single"


def test_build_slices_dispatches_correctly():
    vwap = build_slices(100.0, algo="vwap", config={"vwap_slices": 3})
    assert len(vwap) == 3
    pov = build_slices(100.0, algo="pov", config={"pov_rate": 0.5}, bar_volumes=[200.0, 200.0])
    assert len(pov) == 1  # 0.5 * 200 = 100 → filled in one slice
    assert build_slices(100.0, algo="single") == []


# ── Async execution ─────────────────────────────────────────────────────────


class _FakeOMS:
    def __init__(self, fill_price=100.0):
        self.fill_price = fill_price
        self.calls = 0

    async def place_order(self, req):
        self.calls += 1
        return {
            "status": "success",
            "order_id": f"order-{self.calls}",
            "average_fill_price": self.fill_price,
            "filled_quantity": req.get("quantity", 0),
        }


def test_execute_sliced_order_submits_all_slices():
    oms = _FakeOMS(fill_price=50.0)
    slices = slice_twap(100.0, n_slices=4, interval_sec=0)  # 0 interval to speed up test
    result = asyncio.run(execute_sliced_order(oms, {"symbol": "AAPL", "side": "buy"}, slices=slices))
    assert result["ok"] is True
    assert result["slices_submitted"] == 4
    assert result["total_filled"] == pytest.approx(100.0)
    assert result["avg_fill_price"] == pytest.approx(50.0)
    assert result["complete"] is True
    assert oms.calls == 4


def test_execute_sliced_order_handles_empty_slices():
    oms = _FakeOMS()
    result = asyncio.run(execute_sliced_order(oms, {}, slices=[]))
    assert result["ok"] is False
    assert "error" in result


def test_execute_sliced_order_on_fill_callback():
    oms = _FakeOMS()
    slices = slice_twap(50.0, n_slices=2, interval_sec=0)
    seen = []

    async def on_fill(sl, res):
        seen.append((sl.index, res.get("filled_quantity")))

    asyncio.run(execute_sliced_order(oms, {}, slices=slices, on_fill=on_fill))
    assert len(seen) == 2
    assert seen[0][0] == 0
    assert seen[1][0] == 1


def test_execute_sliced_order_marks_child_slices():
    oms = _FakeOMS()
    seen_reqs = []
    orig = oms.place_order

    async def capture(req):
        seen_reqs.append(req)
        return await orig(req)

    oms.place_order = capture
    slices = slice_twap(30.0, n_slices=3, interval_sec=0)
    asyncio.run(execute_sliced_order(oms, {"symbol": "BTC"}, slices=slices))
    assert all("_slice_index" in r for r in seen_reqs)
    assert all("_slice_total" in r for r in seen_reqs)
    assert seen_reqs[0]["_slice_total"] == 3


class _FailingOMS:
    async def place_order(self, req):
        raise RuntimeError("broker down")


def test_execute_sliced_order_continues_after_slice_failure():
    slices = slice_twap(60.0, n_slices=3, interval_sec=0)
    result = asyncio.run(execute_sliced_order(_FailingOMS(), {}, slices=slices))
    assert result["ok"] is False
    assert result["slices_submitted"] == 3
    assert result["total_filled"] == 0.0


# ── Bug-fix regression tests (Phase 4 audit) ───────────────────────────────


class _PartialFillOMS:
    """Slice 0 fills immediately; slice 1 is accepted but pending (no fill price)."""

    def __init__(self):
        self.calls = 0

    async def place_order(self, req):
        self.calls += 1
        if self.calls == 1:
            return {
                "status": "success",
                "order_id": "ord-1",
                "average_fill_price": 100.0,
                "filled_quantity": req.get("quantity", 0),
            }
        # Second slice: accepted, pending fill (no avg_fill_price)
        return {
            "status": "success",
            "order_id": "ord-2",
            "average_fill_price": None,
            "filled_quantity": 0,
        }


def test_execute_sliced_order_returns_real_order_ids():
    """Bug #4: sliced results must carry real broker order_ids, not 'sliced-N'."""
    oms = _PartialFillOMS()
    slices = slice_twap(50.0, n_slices=2, interval_sec=0)
    result = asyncio.run(execute_sliced_order(oms, {}, slices=slices))
    assert "order_ids" in result
    assert result["order_ids"] == ["ord-1", "ord-2"]


def test_execute_sliced_order_live_submitted_flag():
    """Bug #5: live_submitted must be True when any slice is pending fill."""
    oms = _PartialFillOMS()
    slices = slice_twap(50.0, n_slices=2, interval_sec=0)
    result = asyncio.run(execute_sliced_order(oms, {}, slices=slices))
    assert result["live_submitted"] is True


def test_execute_sliced_order_live_submitted_false_when_all_filled():
    """All slices filled immediately → live_submitted is False (paper OMS)."""
    oms = _FakeOMS(fill_price=100.0)
    slices = slice_twap(50.0, n_slices=2, interval_sec=0)
    result = asyncio.run(execute_sliced_order(oms, {}, slices=slices))
    assert result["live_submitted"] is False


def test_execute_sliced_order_sleeps_inter_slice_gap_not_absolute_offset():
    """Bug #1: sleep must be the GAP between slices, not the absolute offset.

    With interval_sec=0.01 and 3 slices, offsets are [0, 0.01, 0.02].
    The gap between consecutive slices is 0.01s each. Total sleep should be
    ~0.02s (two gaps of 0.01s), NOT 0.03s (0+0.01+0.02).
    """
    import time

    oms = _FakeOMS()
    slices = slice_twap(30.0, n_slices=3, interval_sec=0.01)
    start = time.monotonic()
    asyncio.run(execute_sliced_order(oms, {}, slices=slices))
    elapsed = time.monotonic() - start
    # Two gaps of 0.01s = ~0.02s. If the old bug (absolute offset) were present,
    # we'd sleep 0 + 0.01 + 0.02 = 0.03s. Allow tolerance for scheduler jitter.
    assert 0.015 <= elapsed <= 0.05, f"elapsed={elapsed:.3f}s — gap logic broken"
