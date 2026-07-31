"""EXECUTION_RISK_INTELLIGENCE_PLAN Phase 3 — adaptive execution (MPC-lite):
arrival-anchored pacing, stats-informed algo choice, and the POV inter-slice
gap indexing fix."""

import asyncio

import pytest

from app.services.bots.execution_algos import (
    AdaptivePacer,
    OrderSlice,
    build_slices,
    choose_adaptive_algo,
    execute_sliced_order,
    resolve_execution_algo,
    slice_pov,
    slice_twap,
)


class _FakeOMS:
    def __init__(self, fill_price=100.0):
        self.fill_price = fill_price
        self.calls = 0

    async def place_order(self, req):
        self.calls += 1
        return {
            "status": "success",
            "order_id": f"ord-{self.calls}",
            "filled_quantity": req["quantity"],
            "average_fill_price": self.fill_price,
        }


def _recorded_sleeps(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return sleeps


# ---------------------------------------------------------------------------
# AdaptivePacer — pure pacing math
# ---------------------------------------------------------------------------

class TestAdaptivePacer:
    def test_neutral_drift_keeps_planned_gap(self):
        pacer = AdaptivePacer(side="BUY", arrival_price=100.0, drift_threshold_bps=15.0)
        gap = pacer.adjust_gap(60.0, planned_offset_next=60.0, mark_price=100.05)
        assert gap == pytest.approx(60.0)

    def test_favourable_drift_compresses_gap(self):
        pacer = AdaptivePacer(side="BUY", arrival_price=100.0, drift_threshold_bps=15.0)
        # BUY: mark 0.5% below arrival = -50bps (very favourable)
        gap = pacer.adjust_gap(60.0, planned_offset_next=60.0, mark_price=99.50)
        # planned 60 × 0.5 = 30, but deviation = (0 + 30) - 60 = -30 → clamped
        # to -0.5 × 60 = -30 → gap stays 30.
        assert gap == pytest.approx(30.0)

    def test_adverse_drift_stretches_gap(self):
        pacer = AdaptivePacer(side="BUY", arrival_price=100.0, drift_threshold_bps=15.0)
        # BUY: mark 0.5% above arrival = +50bps adverse
        gap = pacer.adjust_gap(60.0, planned_offset_next=60.0, mark_price=100.50)
        # planned 60 × 2 = 120, deviation = 120 - 60 = +60 → clamp to +30 → gap 90
        assert gap == pytest.approx(90.0)

    def test_sell_side_sign_flip(self):
        pacer = AdaptivePacer(side="SELL", arrival_price=100.0, drift_threshold_bps=15.0)
        # SELL: mark 0.5% ABOVE arrival is favourable (sell higher)
        assert pacer.drift_bps(100.50) == pytest.approx(-50.0)
        gap = pacer.adjust_gap(60.0, planned_offset_next=60.0, mark_price=100.50)
        assert gap == pytest.approx(30.0)

    def test_no_mark_passthrough(self):
        pacer = AdaptivePacer(side="BUY", arrival_price=100.0)
        gap = pacer.adjust_gap(45.0, planned_offset_next=45.0, mark_price=None)
        assert gap == pytest.approx(45.0)

    def test_zero_arrival_passthrough(self):
        pacer = AdaptivePacer(side="BUY", arrival_price=0.0)
        assert pacer.drift_bps(100.0) is None
        gap = pacer.adjust_gap(45.0, planned_offset_next=45.0, mark_price=100.0)
        assert gap == pytest.approx(45.0)

    def test_deviation_bound_upper_across_calls(self):
        """Cumulative slowdown can never exceed +50% of the planned schedule."""
        pacer = AdaptivePacer(side="BUY", arrival_price=100.0, drift_threshold_bps=15.0)
        # Persistent adverse drift across 4 gaps planned at offsets 60..240.
        gaps = []
        for next_offset in (60.0, 120.0, 180.0, 240.0):
            gaps.append(pacer.adjust_gap(60.0, next_offset, mark_price=100.60))
        # First gap: 120 → clamped to 90 (dev +30 of 60). Elapsed 90.
        # Second: planned 120 → elapsed 90 + 120 = 210 vs plan 120 → dev +90
        #         clamped to +0.5×120 = +60 → gap 90. Elapsed 180. Etc.
        assert gaps[0] == pytest.approx(90.0)
        for g in gaps:
            assert g <= 90.0 + 1e-9
        total = sum(gaps)
        assert total <= 240.0 * 1.5 + 1e-9

    def test_deviation_bound_lower_prevents_finish_too_early(self):
        """Cumulative speedup can never exceed -50% of the planned schedule."""
        pacer = AdaptivePacer(side="BUY", arrival_price=100.0, drift_threshold_bps=15.0)
        gaps = []
        for next_offset in (60.0, 120.0, 180.0):
            gaps.append(pacer.adjust_gap(60.0, next_offset, mark_price=99.40))
        # Each gap compresses to 30 (dev clamp -50% each step).
        assert gaps == [pytest.approx(30.0)] * 3
        # Elapsed 90 vs planned 180 → deviation -90 = exactly -50%.
        assert sum(gaps) >= 180.0 * 0.5 - 1e-9

    def test_nonpositive_gap_returns_zero(self):
        pacer = AdaptivePacer(side="BUY", arrival_price=100.0)
        assert pacer.adjust_gap(0.0, 60.0, 100.0) == 0.0
        assert pacer.adjust_gap(-5.0, 60.0, 100.0) == 0.0


# ---------------------------------------------------------------------------
# POV gap indexing fix — non-sequential slice indices
# ---------------------------------------------------------------------------

class TestPovGapIndexing:
    def test_non_sequential_indices_sleep_correct_gaps(self, monkeypatch):
        """POV skips zero-volume bars: indices [0, 2, 3] must sleep by list
        position — gaps [2×interval, 1×interval] — not by sl.index."""
        sleeps = _recorded_sleeps(monkeypatch)
        oms = _FakeOMS()
        # bar_volumes with a zero at index 1 → slice indices 0, 2, 3
        slices = slice_pov(30.0, bar_volumes=[100.0, 0.0, 100.0, 100.0],
                           participation_rate=0.10, interval_sec=60.0)
        assert [s.index for s in slices] == [0, 2, 3]

        asyncio.run(execute_sliced_order(oms, {}, slices=slices))
        assert len(sleeps) == 2
        # Slice 0 (t=0) → slice at t=120: gap 120. Slice at t=120 → t=180: gap 60.
        assert sleeps[0] == pytest.approx(120.0)
        assert sleeps[1] == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# execute_sliced_order with adaptive pacing
# ---------------------------------------------------------------------------

class TestAdaptiveExecutionIntegration:
    def test_pacer_adjusts_sleeps_on_adverse_drift(self, monkeypatch):
        sleeps = _recorded_sleeps(monkeypatch)
        oms = _FakeOMS()
        slices = slice_twap(30.0, n_slices=3, interval_sec=60.0)
        pacer = AdaptivePacer(side="BUY", arrival_price=100.0, drift_threshold_bps=15.0)
        asyncio.run(execute_sliced_order(
            oms, {}, slices=slices,
            pacer=pacer, mark_price_fn=lambda: 100.60,  # adverse
        ))
        assert len(sleeps) == 2
        # 60×2=120 clamped to +50% dev → 90 each
        assert sleeps[0] == pytest.approx(90.0)
        assert sleeps[1] <= 90.0 + 1e-9

    def test_pacer_adjusts_sleeps_on_favourable_drift(self, monkeypatch):
        sleeps = _recorded_sleeps(monkeypatch)
        oms = _FakeOMS()
        slices = slice_twap(30.0, n_slices=3, interval_sec=60.0)
        pacer = AdaptivePacer(side="BUY", arrival_price=100.0, drift_threshold_bps=15.0)
        asyncio.run(execute_sliced_order(
            oms, {}, slices=slices,
            pacer=pacer, mark_price_fn=lambda: 99.40,  # favourable
        ))
        assert sleeps[0] == pytest.approx(30.0)
        assert sleeps[1] == pytest.approx(30.0)

    def test_failing_mark_fn_falls_back_to_planned_gap(self, monkeypatch):
        sleeps = _recorded_sleeps(monkeypatch)
        oms = _FakeOMS()
        slices = slice_twap(30.0, n_slices=3, interval_sec=60.0)
        pacer = AdaptivePacer(side="BUY", arrival_price=100.0)

        def boom():
            raise RuntimeError("feed down")

        asyncio.run(execute_sliced_order(
            oms, {}, slices=slices, pacer=pacer, mark_price_fn=boom,
        ))
        assert sleeps == [pytest.approx(60.0)] * 2

    def test_no_pacer_keeps_legacy_timing(self, monkeypatch):
        sleeps = _recorded_sleeps(monkeypatch)
        oms = _FakeOMS()
        slices = slice_twap(30.0, n_slices=3, interval_sec=60.0)
        asyncio.run(execute_sliced_order(oms, {}, slices=slices))
        assert sleeps == [pytest.approx(60.0)] * 2


# ---------------------------------------------------------------------------
# Stats-informed algo choice
# ---------------------------------------------------------------------------

class TestChooseAdaptiveAlgo:
    def test_high_impact_prefers_pov(self):
        algo = choose_adaptive_algo(
            "AAPL", impact_threshold_bps=10.0,
            measured={"n": 25, "avg_impact_bps": 14.5},
        )
        assert algo == "pov"

    def test_low_impact_prefers_vwap(self):
        algo = choose_adaptive_algo(
            "AAPL", impact_threshold_bps=10.0,
            measured={"n": 25, "avg_impact_bps": 4.0},
        )
        assert algo == "vwap"

    def test_no_measurements_falls_back_to_default(self, monkeypatch):
        from app.services.bots import execution_tca

        # Isolate from any real TCA database — no measurements on disk.
        monkeypatch.setattr(execution_tca, "measured_symbol_impact", lambda symbol: None)
        assert choose_adaptive_algo("AAPL", measured=None) == "vwap"
        assert choose_adaptive_algo("AAPL", measured={"n": 0}) == "vwap"
        assert choose_adaptive_algo(None, measured={"n": 5, "avg_impact_bps": 99}) == "pov"

    def test_missing_impact_field_prefers_vwap(self):
        algo = choose_adaptive_algo("AAPL", measured={"n": 25, "avg_impact_bps": None})
        assert algo == "vwap"


# ---------------------------------------------------------------------------
# Config resolver
# ---------------------------------------------------------------------------

class TestResolveAdaptive:
    def test_adaptive_passes_through(self):
        assert resolve_execution_algo({"execution_algo": "adaptive"}) == "adaptive"
        assert resolve_execution_algo({"execution_algo": "ADAPTIVE"}) == "adaptive"

    def test_garbage_still_defaults_single(self):
        assert resolve_execution_algo({"execution_algo": "banana"}) == "single"
        assert resolve_execution_algo(None) == "single"

    def test_build_slices_rejects_adaptive(self):
        """adaptive must be resolved to a concrete algo before build_slices."""
        assert build_slices(10.0, algo="adaptive", config={}) == []
