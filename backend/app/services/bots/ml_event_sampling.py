"""CUSUM event sampling + uniqueness helpers for ML trainers.

AFML-style symmetric CUSUM on log-close (snippet 2.4). Triple-barrier still
runs on the full bar path so barrier hits are correct; training rows are
restricted to CUSUM events when ``event_filter="cusum"``.

Uniqueness is recomputed among the event subset (concurrent count still
indexes into the full bar timeline).
"""

from __future__ import annotations

import logging
import math
from typing import Any, Iterable, Sequence

import numpy as np

logger = logging.getLogger(__name__)

UNIQUENESS_FLOOR = 0.05
UNIQUENESS_CEIL = 1.0
DEFAULT_EVENT_FILTER = "cusum"
DEFAULT_CUSUM_THRESHOLD = 1.0
DEFAULT_CUSUM_VOL_LOOKBACK = 20
DEFAULT_CUSUM_MIN_BAR_GAP = 5


def clamp_uniqueness(value: Any, default: float = 1.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = default
    if not math.isfinite(v):
        v = default
    return float(min(UNIQUENESS_CEIL, max(UNIQUENESS_FLOOR, v)))


def resolve_event_filter(config: dict | None) -> str:
    cfg = config if isinstance(config, dict) else {}
    raw = str(cfg.get("event_filter") or DEFAULT_EVENT_FILTER).strip().lower()
    if raw in ("all", "none", "off", "bars"):
        return "all"
    return "cusum"


def resolve_cusum_threshold(config: dict | None) -> float:
    cfg = config if isinstance(config, dict) else {}
    try:
        k = float(cfg.get("cusum_threshold", DEFAULT_CUSUM_THRESHOLD))
    except (TypeError, ValueError):
        k = DEFAULT_CUSUM_THRESHOLD
    return float(min(5.0, max(0.1, k)))


def resolve_cusum_vol_lookback(config: dict | None) -> int:
    cfg = config if isinstance(config, dict) else {}
    try:
        n = int(cfg.get("cusum_vol_lookback", DEFAULT_CUSUM_VOL_LOOKBACK))
    except (TypeError, ValueError):
        n = DEFAULT_CUSUM_VOL_LOOKBACK
    return max(5, min(200, n))


def resolve_cusum_min_bar_gap(config: dict | None) -> int:
    cfg = config if isinstance(config, dict) else {}
    try:
        n = int(cfg.get("cusum_min_bar_gap", DEFAULT_CUSUM_MIN_BAR_GAP))
    except (TypeError, ValueError):
        n = DEFAULT_CUSUM_MIN_BAR_GAP
    return max(0, min(50, n))


def _close_array(candles: list[dict] | None) -> np.ndarray:
    n = len(candles or [])
    out = np.zeros(n, dtype=np.float64)
    for i, row in enumerate(candles or []):
        try:
            out[i] = float((row or {}).get("close") or 0.0)
        except (TypeError, ValueError):
            out[i] = 0.0
    return out


def cusum_event_indices(
    candles: list[dict] | None,
    *,
    threshold: float = DEFAULT_CUSUM_THRESHOLD,
    vol_lookback: int = DEFAULT_CUSUM_VOL_LOOKBACK,
    min_bar_gap: int = DEFAULT_CUSUM_MIN_BAR_GAP,
) -> list[int]:
    """Symmetric CUSUM on log-close. Vol is prior-only (no look-ahead)."""
    close = _close_array(candles)
    n = len(close)
    if n < 3:
        return list(range(n))

    logc = np.log(np.maximum(close, 1e-12))
    ret = np.zeros(n, dtype=np.float64)
    ret[1:] = np.diff(logc)

    k = float(min(5.0, max(0.1, threshold)))
    lookback = max(5, int(vol_lookback))
    gap = max(0, int(min_bar_gap))

    events: list[int] = []
    s_pos = 0.0
    s_neg = 0.0
    last_event = -10**9

    for i in range(1, n):
        start = max(1, i - lookback)
        window = ret[start:i]  # prior returns only — excludes ret[i]
        if len(window) < 3:
            continue
        vol = float(np.std(window))
        if not math.isfinite(vol):
            continue
        vol = max(vol, 1e-8)
        h = k * vol
        x = float(ret[i])
        if not math.isfinite(x):
            continue
        s_pos = max(0.0, s_pos + x)
        s_neg = min(0.0, s_neg + x)
        fired = False
        if s_pos > h:
            s_pos = 0.0
            fired = True
        if s_neg < -h:
            s_neg = 0.0
            fired = True
        if fired:
            if i - last_event >= gap:
                events.append(i)
                last_event = i

    return events


def resolve_event_indices(candles: list[dict] | None, config: dict | None = None) -> list[int]:
    """CUSUM indices, or all bars. Falls back to all bars when events are too sparse."""
    n = len(candles or [])
    if n <= 0:
        return []
    if resolve_event_filter(config) == "all":
        return list(range(n))

    events = cusum_event_indices(
        candles,
        threshold=resolve_cusum_threshold(config),
        vol_lookback=resolve_cusum_vol_lookback(config),
        min_bar_gap=resolve_cusum_min_bar_gap(config),
    )
    min_events = max(8, int(0.05 * n))
    if len(events) < min_events:
        logger.info(
            "CUSUM events %d < min %d on %d bars — falling back to all bars",
            len(events), min_events, n,
        )
        return list(range(n))
    return events


def _labels_already_event_annotated(labels: list[dict]) -> bool:
    """True when a cache already flagged every row (do not re-CUSUM a fold slice)."""
    if not labels:
        return False
    return all(isinstance(row, dict) and "is_event" in row for row in labels)


def filter_labels_to_events(
    labels: list[dict],
    event_indices: Iterable[int],
) -> list[dict]:
    """Annotate ``is_event`` without dropping rows (position stays 1:1 with candles).

    Membership uses the row's position in ``labels``, not stored ``index``.
    Walk-forward gather keeps the global TBM index, which would otherwise miss
    every CUSUM hit on folds that do not start at bar 0.
    """
    event_set = {int(i) for i in event_indices}
    out: list[dict] = []
    for i, item in enumerate(labels or []):
        row = dict(item)
        row["is_event"] = i in event_set
        out.append(row)
    return out


def annotate_event_labels(
    labels: list[dict] | None,
    candles: list[dict] | None,
    config: dict | None = None,
) -> list[dict]:
    """Flag CUSUM events and recompute uniqueness among events only.

    If ``is_event`` is already present on every row (WfFeatureCache), keep those
    flags and uniqueness so a fold slice is not re-CUSUMed on concatenated
    embargo windows. Fresh triple-barrier labels are annotated against the
    candle list they are paired with.
    """
    from app.services.bots.ml_triple_barrier import compute_sample_uniqueness

    src = list(labels or [])
    n = len(candles or [])
    if _labels_already_event_annotated(src):
        for row in src:
            row["uniqueness"] = clamp_uniqueness(row.get("uniqueness", 1.0))
        return src

    event_idx = resolve_event_indices(candles, config)
    out = filter_labels_to_events(src, event_idx)
    event_items = [row for row in out if row.get("is_event")]
    if event_items and n > 0:
        # Concurrent count is on this candle list [0, n). Rebase t0 to the
        # local position so gathered global ``index`` values cannot clamp
        # every interval onto the last fold bar.
        uniqueness_src = []
        for i, row in enumerate(out):
            if not row.get("is_event"):
                continue
            item = dict(row)
            item["index"] = i
            uniqueness_src.append(item)
        uniq = compute_sample_uniqueness(uniqueness_src, n)
        for item, u in zip(event_items, uniq):
            item["uniqueness"] = clamp_uniqueness(u)
    for row in out:
        if "uniqueness" in row:
            row["uniqueness"] = clamp_uniqueness(row.get("uniqueness", 1.0))
        else:
            row["uniqueness"] = 1.0
    return out


def keep_train_row(label_info: dict | None, config: dict | None = None) -> bool:
    """True when this labelled bar should enter a trainer."""
    info = label_info if isinstance(label_info, dict) else {}
    if info.get("barrier_hit") == "invalid":
        return False
    if resolve_event_filter(config) == "all":
        return True
    # Missing is_event (legacy cache) → keep, matching pre-CUSUM behaviour.
    return bool(info.get("is_event", True))


def sample_weight_for_label(label_info: dict | None) -> float:
    info = label_info if isinstance(label_info, dict) else {}
    return clamp_uniqueness(info.get("uniqueness", 1.0))


def class_adjusted_weights(
    uniqueness: Sequence[float],
    y: Sequence[int],
    class_weights: Sequence[float] | None = None,
) -> np.ndarray:
    u = np.array([clamp_uniqueness(v) for v in uniqueness], dtype=np.float32)
    if class_weights is None:
        return u
    cw = np.asarray(class_weights, dtype=np.float32)
    yi = np.asarray(y, dtype=np.int64)
    safe = np.clip(yi, 0, max(0, len(cw) - 1))
    return u * cw[safe]


def uniqueness_weighted_accuracy(
    hits: Sequence[bool | int],
    weights: Sequence[float] | None = None,
) -> float:
    h = np.asarray(hits, dtype=np.float64)
    if h.size == 0:
        return 0.0
    if weights is None:
        return float(h.mean())
    w = np.array([clamp_uniqueness(v) for v in weights], dtype=np.float64)
    if w.size != h.size:
        w = np.ones_like(h)
    total = float(w.sum())
    if total <= 0:
        return 0.0
    return float(np.dot(h, w) / total)


def should_score_oos_event(label_info: dict | None, config: dict | None = None) -> bool:
    if resolve_event_filter(config) == "all":
        return True
    info = label_info if isinstance(label_info, dict) else {}
    return bool(info.get("is_event", True))


def directional_hit(signal: str, label_info: dict | None) -> bool:
    info = label_info if isinstance(label_info, dict) else {}
    actual = int(info.get("label", 0) or 0)
    sig = str(signal or "").upper()
    return (sig == "BUY" and actual == 1) or (sig == "SELL" and actual == -1)


def finalize_oos_metrics(
    *,
    raw_correct: int,
    raw_total: int,
    weighted_correct: float,
    weighted_total: float,
    counts: dict[str, int],
    total_bars: int,
) -> dict[str, Any]:
    raw_acc = (raw_correct / raw_total) if raw_total > 0 else 0.0
    w_acc = (weighted_correct / weighted_total) if weighted_total > 0 else raw_acc
    sig_count = int(counts.get("BUY", 0) + counts.get("SELL", 0))
    signal_rate = round(sig_count / total_bars, 4) if total_bars > 0 else 0.0
    return {
        "accuracy": round(float(w_acc), 4),
        "raw_accuracy": round(float(raw_acc), 4),
        "uniqueness_weighted_accuracy": round(float(w_acc), 4),
        "n_signals": int(raw_total),
        "n_correct": int(raw_correct),
        "buy_count": int(counts.get("BUY", 0)),
        "sell_count": int(counts.get("SELL", 0)),
        "none_count": int(counts.get("NONE", 0)),
        "signal_rate": signal_rate,
        "total_bars": int(total_bars),
    }


def event_filter_metadata(config: dict | None = None) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    return {
        "event_filter": resolve_event_filter(cfg),
        "cusum_threshold": resolve_cusum_threshold(cfg),
        "cusum_vol_lookback": resolve_cusum_vol_lookback(cfg),
        "cusum_min_bar_gap": resolve_cusum_min_bar_gap(cfg),
    }
