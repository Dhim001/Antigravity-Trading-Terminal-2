"""Unit tests for discrete backtest/optimizer checkpoints."""

from __future__ import annotations

from app.services.bots.backtest_checkpoint import (
    checkpoint_compatible,
    completed_index_set,
    empty_sweep_checkpoint,
    is_resumable_job,
    merge_sweep_progress,
    request_fingerprint,
)


def test_request_fingerprint_stable_and_ignores_tier():
    a = {"symbol": "BTC", "strategy": "MACD_RSI", "days": 7, "tier": "deferred", "estimated_sec": 9}
    b = {"symbol": "BTC", "strategy": "MACD_RSI", "days": 7, "tier": "inline", "estimated_sec": 1}
    assert request_fingerprint(a) == request_fingerprint(b)
    c = {"symbol": "ETH", "strategy": "MACD_RSI", "days": 7}
    assert request_fingerprint(a) != request_fingerprint(c)


def test_merge_and_skip_indices():
    req = {"symbol": "BTC", "strategy": "ML_SIGNAL_BOOST", "sweep": {"max_combos": 3}}
    cp = empty_sweep_checkpoint(req)
    cp = merge_sweep_progress(
        cp,
        request=req,
        run_idx=0,
        label="a",
        row={"label": "a", "total_pnl": 1},
        best_config={"x": 1},
    )
    cp = merge_sweep_progress(
        cp,
        request=req,
        run_idx=2,
        label="c",
        row={"label": "c", "total_pnl": 3},
        best_config={"x": 2},
    )
    assert completed_index_set(cp) == {0, 2}
    assert cp["completed_labels"] == ["a", "c"]
    assert len(cp["sweep_rows"]) == 2
    assert checkpoint_compatible(cp, req)


def test_incompatible_fingerprint_resets():
    req = {"symbol": "BTC", "strategy": "ML_SIGNAL_BOOST"}
    other = {"symbol": "ETH", "strategy": "ML_SIGNAL_BOOST"}
    cp = merge_sweep_progress(
        empty_sweep_checkpoint(req),
        request=req,
        run_idx=0,
        label="a",
        row={"label": "a"},
    )
    assert not checkpoint_compatible(cp, other)
    rebuilt = merge_sweep_progress(cp, request=other, run_idx=1, label="b", row={"label": "b"})
    assert completed_index_set(rebuilt) == {1}
    assert rebuilt["completed_labels"] == ["b"]


def test_is_resumable_job():
    req = {"symbol": "BTC", "strategy": "X", "days": 7}
    cp = empty_sweep_checkpoint(req)
    cp = merge_sweep_progress(cp, request=req, run_idx=0, label="a", row={"label": "a"})
    job = {"status": "failed", "request": req, "checkpoint": cp}
    assert is_resumable_job(job) is True
    job2 = {"status": "completed", "request": req, "checkpoint": cp}
    assert is_resumable_job(job2) is False
    job3 = {"status": "failed", "request": req, "checkpoint": None}
    assert is_resumable_job(job3) is False


def test_walk_forward_fold_merge_and_skip():
    from app.services.bots.backtest_checkpoint import (
        completed_fold_index_set,
        empty_walk_forward_checkpoint,
        merge_walk_forward_progress,
    )

    req = {"symbol": "BTC", "strategy": "MACD_RSI", "walk_forward": True, "rolling_folds": 3}
    cp = empty_walk_forward_checkpoint(req)
    cp = merge_walk_forward_progress(
        cp,
        request=req,
        fold_idx=0,
        fold_entry={"fold": 1, "best_config": {"a": 1}},
    )
    cp = merge_walk_forward_progress(
        cp,
        request=req,
        fold_idx=1,
        fold_entry={"fold": 2, "best_config": {"a": 2}},
    )
    assert completed_fold_index_set(cp) == {0, 1}
    assert len(cp["fold_results"]) == 2
    assert cp["kind"] == "walk_forward"
    assert checkpoint_compatible(cp, req)
    job = {"status": "failed", "request": req, "checkpoint": cp}
    assert is_resumable_job(job) is True
