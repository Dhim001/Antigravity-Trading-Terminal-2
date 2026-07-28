"""Execution algos — VWAP and POV order slicing.

Phase 4.10 of the Signal Enhancement Plan.

Large orders move the market against you. Splitting a parent order into child
slices executed over time reduces market impact and slippage. Two schedules:

1. **TWAP/VWAP** — split the parent order into N equal time-slices, optionally
   weighted by an intraday volume profile (U-shape). Each child is a market
   order submitted at the slice's scheduled time.

2. **POV (Percent of Volume)** — size each child order as a fixed percentage
   of the current bar's volume. Adapts to live volume: high-volume bars get
   larger slices, low-volume bars get smaller ones. Caps total at the parent
   quantity.

The slicers are pure functions (no I/O) so they can be unit-tested without an
OMS. The async ``execute_sliced_order`` submits child orders to the OMS with
   configurable spacing between slices.

Opt-in via ``execution_algo`` config: ``"single"`` (default, legacy) |
``"vwap"`` | ``"pov"``.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger(__name__)

DEFAULT_VWAP_SLICES = 5
DEFAULT_POV_RATE = 0.10          # 10% of bar volume per slice
DEFAULT_SLICE_INTERVAL_SEC = 60  # one slice per minute
MAX_SLICES = 50                 # safety cap


@dataclass
class OrderSlice:
    """A single child order within a sliced execution."""

    index: int
    quantity: float
    time_offset_sec: float       # delay from parent submission
    weight: float                # slice's share of total (diagnostic)


def slice_twap(
    total_qty: float,
    *,
    n_slices: int = DEFAULT_VWAP_SLICES,
    interval_sec: float = DEFAULT_SLICE_INTERVAL_SEC,
) -> list[OrderSlice]:
    """Equal-size time-sliced schedule (TWAP)."""
    n = max(1, min(MAX_SLICES, int(n_slices)))
    if total_qty <= 0 or n <= 0:
        return []
    per_slice = total_qty / n
    return [
        OrderSlice(
            index=i,
            quantity=per_slice,
            time_offset_sec=i * interval_sec,
            weight=1.0 / n,
        )
        for i in range(n)
    ]


def slice_vwap(
    total_qty: float,
    *,
    volume_profile: Sequence[float] | None = None,
    n_slices: int = DEFAULT_VWAP_SLICES,
    interval_sec: float = DEFAULT_SLICE_INTERVAL_SEC,
) -> list[OrderSlice]:
    """Volume-weighted time-sliced schedule (VWAP).

    ``volume_profile`` is an optional intraday volume shape (e.g. U-curve
    with higher volume at open/close). When omitted, falls back to TWAP
    (equal slices).
    """
    n = max(1, min(MAX_SLICES, int(n_slices)))
    if total_qty <= 0 or n <= 0:
        return []

    # No profile → equal weights (TWAP).
    if not volume_profile or len(volume_profile) == 0:
        return slice_twap(total_qty, n_slices=n, interval_sec=interval_sec)

    # Resample the profile to n slices by averaging buckets.
    prof = list(volume_profile)
    weights = []
    bucket = len(prof) / n
    for i in range(n):
        start = int(i * bucket)
        end = max(start + 1, int((i + 1) * bucket))
        chunk = prof[start:end] or [prof[-1]]
        weights.append(sum(chunk) / len(chunk))

    total_weight = sum(weights) or 1.0
    weights = [w / total_weight for w in weights]

    return [
        OrderSlice(
            index=i,
            quantity=total_qty * weights[i],
            time_offset_sec=i * interval_sec,
            weight=weights[i],
        )
        for i in range(n)
    ]


def slice_pov(
    total_qty: float,
    *,
    bar_volumes: Sequence[float],
    participation_rate: float = DEFAULT_POV_RATE,
    interval_sec: float = DEFAULT_SLICE_INTERVAL_SEC,
) -> list[OrderSlice]:
    """Percent-of-Volume schedule.

    Each slice sizes itself as ``participation_rate`` × the bar's volume,
    capped so the running total never exceeds ``total_qty``. The number of
    slices is determined by how many bars it takes to fill the parent order
    at the given participation rate.
    """
    if total_qty <= 0 or not bar_volumes or participation_rate <= 0:
        return []
    rate = max(0.01, min(1.0, float(participation_rate)))
    slices: list[OrderSlice] = []
    remaining = float(total_qty)
    for i, vol in enumerate(bar_volumes):
        if remaining <= 1e-9:
            break
        child = min(remaining, float(vol) * rate)
        if child <= 0:
            continue
        slices.append(OrderSlice(
            index=i,
            quantity=child,
            time_offset_sec=i * interval_sec,
            weight=child / total_qty,
        ))
        remaining -= child
        if len(slices) >= MAX_SLICES:
            break
    # If we ran out of bars before filling, dump the remainder into the last slice.
    if remaining > 1e-9 and slices:
        slices[-1] = OrderSlice(
            index=slices[-1].index,
            quantity=slices[-1].quantity + remaining,
            time_offset_sec=slices[-1].time_offset_sec,
            weight=(slices[-1].quantity + remaining) / total_qty,
        )
    return slices


# ── Async execution ────────────────────────────────────────────────────────


async def execute_sliced_order(
    oms,
    order_req: dict,
    *,
    slices: list[OrderSlice],
    on_fill=None,
) -> dict:
    """Submit child orders to the OMS over time.

    Returns a summary dict with ``total_filled``, ``slices_submitted``,
    ``slice_results``, and ``avg_fill_price``.

    ``on_fill`` is an optional async callback ``(slice, result)`` invoked
    after each child fill — useful for logging / telemetry.
    """
    if not slices:
        return {"ok": False, "error": "no slices", "total_filled": 0.0}

    base_req = dict(order_req)
    total_qty = sum(s.quantity for s in slices)
    results: list[dict] = []
    total_filled = 0.0
    weighted_price_sum = 0.0

    for sl in slices:
        if sl.quantity <= 0:
            continue
        child_req = dict(base_req)
        child_req["quantity"] = sl.quantity
        # Mark as a child slice for telemetry
        child_req["_slice_index"] = sl.index
        child_req["_slice_total"] = len(slices)

        try:
            result = await oms.place_order(child_req)
            results.append(result)
            filled = float(result.get("filled_quantity") or sl.quantity or 0)
            price = float(result.get("average_fill_price") or base_req.get("price") or 0)
            total_filled += filled
            weighted_price_sum += filled * price
            if on_fill:
                try:
                    await on_fill(sl, result)
                except Exception:
                    logger.debug("on_fill callback failed", exc_info=True)
        except Exception as exc:
            logger.warning("Slice %d failed: %s", sl.index, exc)
            results.append({"status": "error", "error": str(exc), "slice_index": sl.index})

        # Space out slices: sleep the gap between this slice and the next.
        # time_offset_sec is absolute (from parent submission), so the gap is
        # next.offset - current.offset. For uniform schedules this equals
        # interval_sec; for POV (variable offsets) it adapts correctly.
        if sl.index < len(slices) - 1:
            next_offset = slices[sl.index + 1].time_offset_sec
            gap = next_offset - sl.time_offset_sec
            if gap > 0:
                await asyncio.sleep(gap)

    avg_price = weighted_price_sum / total_filled if total_filled > 0 else 0.0
    # Collect real broker order_ids from successful slices (for reconciliation).
    order_ids = [
        r.get("order_id") for r in results
        if r.get("status") == "success" and r.get("order_id")
    ]
    # A sliced order is "live submitted" (pending fill) when at least one slice
    # was accepted by the broker but has no fill price yet.
    live_submitted = any(
        r.get("status") == "success" and r.get("average_fill_price") is None
        for r in results
    )
    return {
        "ok": total_filled > 0 or bool(order_ids),
        "total_filled": round(total_filled, 6),
        "slices_submitted": len(results),
        "slice_results": results,
        "avg_fill_price": round(avg_price, 6),
        "complete": total_filled >= total_qty * 0.999,
        "order_ids": order_ids,
        "live_submitted": live_submitted,
    }


# ── Config resolver ────────────────────────────────────────────────────────


def resolve_execution_algo(config: dict | None) -> str:
    """Return the configured execution algo: 'single' | 'vwap' | 'pov'."""
    cfg = config or {}
    algo = str(cfg.get("execution_algo") or "single").lower()
    if algo not in ("single", "vwap", "pov"):
        return "single"
    return algo


def build_slices(
    total_qty: float,
    *,
    algo: str,
    config: dict | None = None,
    bar_volumes: Sequence[float] | None = None,
) -> list[OrderSlice]:
    """Dispatch to the right slicer based on algo name."""
    cfg = config or {}
    if algo == "vwap":
        return slice_vwap(
            total_qty,
            n_slices=int(cfg.get("vwap_slices", DEFAULT_VWAP_SLICES)),
            interval_sec=float(cfg.get("slice_interval_sec", DEFAULT_SLICE_INTERVAL_SEC)),
        )
    if algo == "pov":
        return slice_pov(
            total_qty,
            bar_volumes=bar_volumes or [],
            participation_rate=float(cfg.get("pov_rate", DEFAULT_POV_RATE)),
            interval_sec=float(cfg.get("slice_interval_sec", DEFAULT_SLICE_INTERVAL_SEC)),
        )
    return []  # single-shot, no slicing
