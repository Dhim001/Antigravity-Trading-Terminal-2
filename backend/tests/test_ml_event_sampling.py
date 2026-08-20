"""CUSUM event sampling + uniqueness helpers."""

from __future__ import annotations

import numpy as np

from app.services.bots.ml_event_sampling import (
    annotate_event_labels,
    clamp_uniqueness,
    cusum_event_indices,
    filter_labels_to_events,
    keep_train_row,
    resolve_event_indices,
    uniqueness_weighted_accuracy,
)
from app.services.bots.ml_triple_barrier import label_triple_barrier


def _candles(n: int, *, spike_at: tuple[int, ...] = (), trend: float = 0.0):
    rows = []
    price = 100.0
    for i in range(n):
        shock = 8.0 if i in spike_at else 0.0
        price = price * (1.0 + trend) + shock * (1 if i % 2 == 0 else -1)
        rows.append({
            "time": 1_700_000_000 + i * 60,
            "open": price - 0.2,
            "high": price + 0.6,
            "low": price - 0.6,
            "close": price,
            "volume": 1000.0,
            "ATR_14": 1.5,
        })
    return rows


def test_cusum_resets_and_fires_on_jumps():
    candles = _candles(80, spike_at=(25, 55), trend=0.0001)
    idx = cusum_event_indices(candles, threshold=1.0, vol_lookback=20, min_bar_gap=3)
    assert any(abs(i - 25) <= 3 for i in idx)
    assert any(abs(i - 55) <= 3 for i in idx)


def test_cusum_no_lookahead():
    candles = _candles(60, spike_at=(20,), trend=0.0)
    prefix = cusum_event_indices(candles[:40], threshold=0.8, min_bar_gap=1)
    mutated = [dict(c) for c in candles]
    for i in range(40, 60):
        mutated[i]["close"] = 10_000.0
    full = cusum_event_indices(mutated, threshold=0.8, min_bar_gap=1)
    assert prefix == [i for i in full if i < 40]


def test_cusum_min_bar_gap():
    candles = _candles(50, spike_at=tuple(range(15, 35)), trend=0.002)
    tight = cusum_event_indices(candles, threshold=0.5, min_bar_gap=0)
    gapped = cusum_event_indices(candles, threshold=0.5, min_bar_gap=8)
    if len(gapped) >= 2:
        diffs = np.diff(gapped)
        assert int(np.min(diffs)) >= 8
    assert len(gapped) <= len(tight)


def test_fallback_to_all_bars_when_too_few_events():
    flat = []
    for i in range(40):
        flat.append({
            "time": 1_700_000_000 + i * 60,
            "open": 100.0,
            "high": 100.01,
            "low": 99.99,
            "close": 100.0,
            "volume": 1.0,
            "ATR_14": 0.01,
        })
    idx = resolve_event_indices(flat, {"event_filter": "cusum", "cusum_threshold": 5.0})
    assert idx == list(range(40))


def test_filter_keeps_label_alignment():
    candles = _candles(40, trend=0.001)
    labels = label_triple_barrier(candles, max_holding_bars=8)
    events = [5, 12, 20]
    flagged = filter_labels_to_events(labels, events)
    assert len(flagged) == len(labels)
    assert all("is_event" in row for row in flagged)
    kept = {int(r["index"]) for r in flagged if r["is_event"]}
    assert kept == set(events)


def test_filter_uses_list_position_not_stored_index():
    """WF gather keeps global TBM index; CUSUM events are local to the slice."""
    labels = [
        {"index": 40, "label": 1, "bars_held": 2},
        {"index": 41, "label": -1, "bars_held": 2},
        {"index": 42, "label": 0, "bars_held": 1},
    ]
    flagged = filter_labels_to_events(labels, [0, 2])
    assert [r["is_event"] for r in flagged] == [True, False, True]
    assert flagged[0]["index"] == 40


def test_annotate_preserves_cached_event_flags():
    candles = _candles(80, spike_at=(20, 55), trend=0.001)
    labels = label_triple_barrier(candles, max_holding_bars=8)
    full = annotate_event_labels(labels, candles, {"event_filter": "cusum"})
    subset_c = candles[40:]
    subset_l = [dict(r) for r in full[40:]]
    again = annotate_event_labels(subset_l, subset_c, {"event_filter": "cusum"})
    assert [r["is_event"] for r in again] == [r["is_event"] for r in full[40:]]


def test_annotate_fold_slice_without_flags_uses_local_cusum():
    candles = _candles(80, spike_at=(55,), trend=0.0001)
    labels = label_triple_barrier(candles, max_holding_bars=8)
    subset_c = candles[40:]
    subset_l = [{k: v for k, v in r.items() if k != "is_event"} for r in labels[40:]]
    assert subset_l[0]["index"] == 40
    out = annotate_event_labels(subset_l, subset_c, {"event_filter": "cusum"})
    assert any(r.get("is_event") for r in out)


def test_annotate_recomputes_uniqueness_on_events():
    candles = _candles(60, spike_at=(20, 40), trend=0.001)
    labels = label_triple_barrier(candles, max_holding_bars=10)
    out = annotate_event_labels(labels, candles, {"event_filter": "cusum"})
    assert len(out) == len(labels)
    events = [r for r in out if r.get("is_event")]
    assert events
    for row in out:
        u = float(row["uniqueness"])
        assert 0.05 <= u <= 1.0


def test_keep_train_row_defaults_missing_is_event_true():
    assert keep_train_row({"label": 1}, {"event_filter": "cusum"}) is True
    assert keep_train_row({"label": 1, "is_event": False}, {"event_filter": "cusum"}) is False
    assert keep_train_row({"label": 1, "is_event": False}, {"event_filter": "all"}) is True
    assert keep_train_row({"barrier_hit": "invalid", "is_event": True}) is False


def test_lstm_sequence_weights_follow_uniqueness():
    from app.services.bots.ml_lstm_trainer import build_sequences

    candles = _candles(220, trend=0.001)
    labels = label_triple_barrier(candles, max_holding_bars=10)
    for i, lab in enumerate(labels):
        lab["is_event"] = True
        lab["uniqueness"] = 0.2 if i % 2 == 0 else 0.9
    X, y, w = build_sequences(
        candles, labels, lookback=20, max_holding_bars=10,
        config={"event_filter": "all"},
    )
    assert len(w) == len(y) == len(X)
    assert float(np.min(w)) >= 0.05
    assert float(np.max(w)) <= 1.0


def test_uniqueness_weighted_accuracy_differs_from_raw():
    hits = [True, False, True, False]
    weights = [1.0, 1.0, 0.05, 0.05]
    raw = uniqueness_weighted_accuracy(hits, [1, 1, 1, 1])
    weighted = uniqueness_weighted_accuracy(hits, weights)
    assert abs(raw - 0.5) < 1e-9
    assert weighted > raw
    assert clamp_uniqueness(0.0) == 0.05
    assert clamp_uniqueness(2.0) == 1.0
