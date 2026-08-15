"""HTF causal-series parity: incremental O(n) vs naive snapshot-every-bar."""

from __future__ import annotations

import numpy as np

from app.services.bots.ml_feature_htf import (
    HTF_FEATURE_NAMES,
    _causal_htf_series,
    _fields,
    _htf_indicator_snapshot,
    _bar_unix,
    compute_htf_feature_matrix,
    compute_htf_features_for_bar,
)


def _make_bar(i: int):
    base = 100.0 + i * 0.05 + (i % 7) * 0.02
    return {
        "time": 1_700_000_000 + i * 60,
        "open": base - 0.1,
        "high": base + 1.0 + (i % 3) * 0.1,
        "low": base - 1.0 - (i % 2) * 0.1,
        "close": base + 0.2 * ((i % 5) - 2),
        "volume": 1000.0 + i * 3,
    }


def _naive_causal_series(rows, target_secs, kind, *, default):
    """Independent oracle: resample prefix + snapshot at every source bar."""
    n = len(rows)
    out = np.full(n, default, dtype=np.float64)
    if n == 0 or target_secs <= 0:
        return out
    completed = []
    cur = None
    cur_bucket = None
    for i, bar in enumerate(rows):
        t = _bar_unix(bar)
        fields = _fields(bar)
        if t is None or fields is None:
            out[i] = out[i - 1] if i else default
            continue
        bucket = int(t // target_secs) * target_secs
        if cur is None:
            cur_bucket = bucket
            cur = {"time": bucket, **fields}
        elif bucket == cur_bucket:
            cur["high"] = max(cur["high"], fields["high"])
            cur["low"] = min(cur["low"], fields["low"])
            cur["close"] = fields["close"]
            cur["volume"] += fields["volume"]
        else:
            completed.append(cur)
            cur_bucket = bucket
            cur = {"time": bucket, **fields}
        series = completed + ([cur] if cur is not None else [])
        out[i] = _htf_indicator_snapshot(series, kind)
    return out


def test_incremental_htf_matches_naive_snapshot():
    rows = [_make_bar(i) for i in range(400)]
    for kind, default, secs in (
        ("trend", 0.0, 3600),
        ("rsi", 0.5, 3600),
        ("atr_ratio", 0.0, 14400),
        ("regime", 0.0, 86400),
    ):
        got = _causal_htf_series(rows, secs, kind, default=default)
        exp = _naive_causal_series(rows, secs, kind, default=default)
        assert np.allclose(got, exp, atol=1e-9, rtol=1e-9), kind


def test_last_bar_helper_matches_matrix_tail():
    rows = [_make_bar(i) for i in range(250)]
    mat = compute_htf_feature_matrix(rows, timeframe="1m")
    last = compute_htf_features_for_bar(rows[-1], rows[:-1], timeframe="1m")
    for name in HTF_FEATURE_NAMES:
        assert abs(last[name] - float(mat[name][-1])) < 1e-9, name


def test_htf_no_lookahead_forming_bucket():
    """A later bar in the same hour must not change an earlier bar's HTF value."""
    rows = [_make_bar(i) for i in range(120)]
    mat_full = compute_htf_feature_matrix(rows, timeframe="1m")
    mat_prefix = compute_htf_feature_matrix(rows[:60], timeframe="1m")
    for name in HTF_FEATURE_NAMES:
        assert abs(float(mat_full[name][59]) - float(mat_prefix[name][59])) < 1e-9, name
