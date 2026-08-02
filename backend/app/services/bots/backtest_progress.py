"""Shared helpers for backtest progress throttling and ETA enrichment."""

from __future__ import annotations

import time
from typing import Any, Callable


ProgressCb = Callable[[int, int], None]


class ProgressThrottle:
    """Emit progress on bar stride *or* wall-clock interval (whichever comes first).

    Long ML/RL runs used to sit at "bar 0" for minutes when stride alone gated
    updates. Time-based emits keep the UI alive without flooding the WS.
    """

    def __init__(
        self,
        cb: ProgressCb | None,
        *,
        total: int,
        stride: int | None = None,
        min_interval_sec: float = 2.0,
    ) -> None:
        self._cb = cb
        self.total = max(1, int(total or 1))
        self.stride = max(1, int(stride if stride is not None else min(200, self.total // 100)))
        self.min_interval = max(0.25, float(min_interval_sec or 2.0))
        self._t0 = time.monotonic()
        self._last_emit_t = 0.0
        self._last_done = -1

    def __call__(self, done: int, total: int | None = None) -> None:
        if not self._cb:
            return
        tot = max(1, int(total if total is not None else self.total))
        d = max(0, int(done))
        now = time.monotonic()
        force = d >= tot or self._last_done < 0
        by_stride = (d - max(self._last_done, 0)) >= self.stride
        # Heartbeat even when `done` is unchanged so elapsed_sec/message refresh
        # during long precompute phases that only occasionally bump the counter.
        by_time = (now - self._last_emit_t) >= self.min_interval and d != self._last_done
        heartbeat = (
            d == self._last_done
            and (now - self._last_emit_t) >= max(self.min_interval, 5.0)
        )
        if not (force or by_stride or by_time or heartbeat):
            return
        self._last_emit_t = now
        self._last_done = d
        self._cb(d, tot)

    def elapsed_sec(self) -> float:
        return max(0.0, time.monotonic() - self._t0)


def enrich_bar_progress(
    done: int,
    total: int,
    *,
    elapsed_sec: float,
    phase: str = "simulate",
    message: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a progress payload with bars/sec + ETA for the UI."""
    tot = max(1, int(total or 1))
    d = max(0, min(int(done), tot))
    elapsed = max(0.001, float(elapsed_sec or 0.001))
    rate = d / elapsed if d > 0 else 0.0
    remaining = (tot - d) / rate if rate > 0 else None
    pct = min(99, max(0, int((d / tot) * 100))) if d < tot else 100
    msg = message or f"Simulating bar {d}/{tot}…"
    if remaining is not None and d > 0 and d < tot:
        eta = int(remaining)
        if eta >= 120:
            msg = f"{msg} · ~{eta // 60}m left"
        elif eta >= 5:
            msg = f"{msg} · ~{eta}s left"
    payload: dict[str, Any] = {
        "pct": pct,
        "phase": phase,
        "message": msg,
        "bar": d,
        "bars": tot,
        "bars_per_sec": round(rate, 2) if rate > 0 else None,
        "eta_sec": int(remaining) if remaining is not None else None,
        "elapsed_sec": round(elapsed, 1),
    }
    for k, v in extra.items():
        if v is not None:
            payload[k] = v
    return payload


def make_bar_progress_cb(
    enqueue: Callable[[dict], None],
    *,
    phase: str = "simulate",
    pct_base: float = 10.0,
    pct_span: float = 85.0,
    message_prefix: str | None = None,
) -> tuple[ProgressCb, Callable[[], float]]:
    """Return (progress_cb, elapsed_fn) that enqueues enriched bar progress."""
    t0 = time.monotonic()

    def _elapsed() -> float:
        return max(0.001, time.monotonic() - t0)

    def _cb(done: int, total: int) -> None:
        tot = max(1, int(total or 1))
        d = max(0, int(done))
        frac = d / tot
        pct = pct_base + frac * pct_span
        prefix = message_prefix or ("Simulating" if phase == "simulate" else phase.replace("_", " ").title())
        msg = f"{prefix}: bar {d}/{tot}…"
        payload = enrich_bar_progress(
            d,
            tot,
            elapsed_sec=_elapsed(),
            phase=phase,
            message=msg,
        )
        payload["pct"] = min(int(pct), 99) if d < tot else min(int(pct_base + pct_span), 100)
        enqueue(payload)

    return _cb, _elapsed
