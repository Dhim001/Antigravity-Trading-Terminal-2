"""Purged / embargoed walk-forward splits (López de Prado AFML Ch. 7–12)."""

from __future__ import annotations

import copy
from typing import Any

from app.services.bots.indicators import MIN_WARMUP_BARS

DEFAULT_EMBARGO_PCT = 1.0
MIN_TEST_BARS = 50
MIN_TRAIN_BARS = 50


def estimate_purge_bars(config: dict | None, *, timeframe: str = "1m") -> int:
    """Estimate label overlap horizon from risk params (bars to purge before OOS).

    Always at least ``triple_barrier_max_bars`` (label vertical barrier) so
    purged CV matches AFML when Lab calendar holdout is active.
    """
    cfg = config or {}
    _ = timeframe
    base = max(20, MIN_WARMUP_BARS // 2)
    try:
        label_horizon = max(1, int(cfg.get("triple_barrier_max_bars", 30)))
    except (TypeError, ValueError):
        label_horizon = 30
    trailing = float(cfg.get("trailing_stop_percent") or cfg.get("stop_loss_percent") or 2)
    tp = float(cfg.get("take_profit_percent") or 0)
    hold_hint = float(cfg.get("avg_hold_hours") or 0)
    sl_bars = int(max(10, trailing * 4))
    tp_bars = int(max(10, tp * 3)) if tp > 0 else 0
    hold_bars = int(max(0, hold_hint * 12)) if hold_hint > 0 else 0
    return min(200, max(base, label_horizon, sl_bars, tp_bars, hold_bars))


def embargo_bars_for_segment(segment_len: int, embargo_pct: float) -> int:
    pct = max(0.0, min(5.0, float(embargo_pct or 0)))
    if pct <= 0 or segment_len <= 0:
        return 0
    return max(0, int(segment_len * pct / 100.0))


def purge_train_before_test(
    train: list[dict],
    test: list[dict],
    purge_bars: int,
) -> tuple[list[dict], dict[str, Any]]:
    """Remove tail of IS window that overlaps OOS label horizon."""
    purge_bars = max(0, int(purge_bars or 0))
    if purge_bars <= 0 or not train or not test:
        return list(train), {"purge_bars": 0, "purged": False}
    if len(train) <= purge_bars + MIN_TRAIN_BARS // 2:
        keep = max(MIN_TRAIN_BARS // 2, len(train) // 2)
        return train[:keep], {
            "purge_bars": purge_bars,
            "purged": True,
            "truncated_to": keep,
            "note": "Train shortened to preserve minimum IS size",
        }
    return train[:-purge_bars], {
        "purge_bars": purge_bars,
        "purged": True,
        "removed_bars": purge_bars,
    }


def apply_embargo_after_test(
    candles: list[dict],
    test_end_idx: int,
    embargo_bars: int,
) -> int:
    """Return index after embargo buffer following a test segment."""
    embargo_bars = max(0, int(embargo_bars or 0))
    return min(len(candles), test_end_idx + embargo_bars)


def split_final_holdout(
    candles: list[dict],
    meta: dict,
    holdout_pct: float,
) -> tuple[list[dict], list[dict], dict, dict]:
    """Reserve trailing holdout segment never used in optimization."""
    if not candles:
        return [], [], dict(meta or {}), dict(meta or {})
    pct = max(5.0, min(30.0, float(holdout_pct)))
    split = int(len(candles) * (1.0 - pct / 100.0))
    split = max(MIN_TRAIN_BARS + MIN_TEST_BARS, min(split, len(candles) - MIN_TEST_BARS))
    if split <= 0 or split >= len(candles):
        return list(candles), [], dict(meta or {}), dict(meta or {})

    wf_candles = candles[:split]
    holdout = candles[split:]
    wf_meta = copy.deepcopy(meta or {})
    holdout_meta = copy.deepcopy(meta or {})
    wf_meta["window"] = "walk_forward"
    wf_meta["count"] = len(wf_candles)
    wf_meta["final_holdout_pct"] = pct
    if wf_candles:
        wf_meta["newest"] = wf_candles[-1].get("time", wf_meta.get("newest"))
    holdout_meta["window"] = "final_holdout"
    holdout_meta["count"] = len(holdout)
    holdout_meta["final_holdout_pct"] = pct
    if holdout:
        holdout_meta["oldest"] = holdout[0].get("time", holdout_meta.get("oldest"))
        holdout_meta["newest"] = holdout[-1].get("time", holdout_meta.get("newest"))
    return wf_candles, holdout, wf_meta, holdout_meta


def partition_candles(candles: list[dict], n_groups: int) -> list[list[dict]]:
    """Split candles into n contiguous groups for CSCV / PBO."""
    n_groups = max(2, int(n_groups or 2))
    if not candles:
        return []
    size = len(candles) // n_groups
    if size < 10:
        return [candles]
    groups: list[list[dict]] = []
    for i in range(n_groups):
        start = i * size
        end = len(candles) if i == n_groups - 1 else (i + 1) * size
        chunk = candles[start:end]
        if len(chunk) >= 10:
            groups.append(chunk)
    return groups


def parse_wf_validation_options(
    sweep: dict | None = None,
    *,
    msg: dict | None = None,
    base_config: dict | None = None,
    timeframe: str = "1m",
) -> dict[str, Any]:
    """Normalize Tier 3 WF validation flags from request / sweep block."""
    sweep = sweep or {}
    msg = msg or {}
    purged = sweep.get("purged_splits")
    if purged is None:
        purged = msg.get("purged_splits", True)
    purge_bars = sweep.get("purge_bars") or msg.get("purge_bars")
    if purge_bars is None and purged:
        purge_bars = estimate_purge_bars(base_config, timeframe=timeframe)
    embargo_pct = sweep.get("embargo_pct")
    if embargo_pct is None:
        embargo_pct = msg.get("embargo_pct", DEFAULT_EMBARGO_PCT)
    wf_mode = str(sweep.get("wf_mode") or msg.get("wf_mode") or "rolling").lower()
    if wf_mode not in ("rolling", "anchored"):
        wf_mode = "rolling"
    wf_step_pct = sweep.get("wf_step_pct")
    if wf_step_pct is None:
        wf_step_pct = msg.get("wf_step_pct", 25.0)
    holdout_pct = sweep.get("final_holdout_pct")
    if holdout_pct is None:
        holdout_pct = msg.get("final_holdout_pct")
    pbo_audit = bool(sweep.get("pbo_audit") or msg.get("pbo_audit"))
    pbo_top_k = int(sweep.get("pbo_top_k") or msg.get("pbo_top_k") or 8)
    return {
        "purged_splits": bool(purged),
        "purge_bars": max(0, int(purge_bars or 0)),
        "embargo_pct": float(embargo_pct),
        "wf_mode": wf_mode,
        "wf_step_pct": max(5.0, min(50.0, float(wf_step_pct))),
        "final_holdout_pct": float(holdout_pct) if holdout_pct else None,
        "pbo_audit": pbo_audit,
        "pbo_top_k": max(3, min(16, pbo_top_k)),
        "pbo_groups": int(sweep.get("pbo_groups") or msg.get("pbo_groups") or 8),
    }
