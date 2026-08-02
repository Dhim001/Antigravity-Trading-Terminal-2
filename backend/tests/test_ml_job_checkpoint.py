"""Tests for ML Optuna / walk-forward job checkpoints."""

from __future__ import annotations

from app.services.bots.ml_job_checkpoint import (
    checkpoint_resume_ok,
    completed_fold_indices,
    empty_hyperparam_checkpoint,
    empty_wf_checkpoint,
    merge_hyperparam_trial,
    merge_wf_fold,
    optuna_study_path,
)


def test_hyperparam_merge_tracks_trials():
    cp = empty_hyperparam_checkpoint(
        job_id="j1", strategy="ML_SIGNAL_BOOST", symbol="BTC", config={"max_trials": 5}, max_trials=5,
    )
    assert checkpoint_resume_ok(cp)
    cp = merge_hyperparam_trial(
        cp,
        job_id="j1",
        strategy="ML_SIGNAL_BOOST",
        symbol="BTC",
        config={"max_trials": 5},
        max_trials=5,
        trial_row={"trial": 1, "score": 0.5, "params": {"lr": 0.01}},
        best_hyperparams={"lr": 0.01},
        best_score=0.5,
    )
    assert cp["trials_completed"] == 1
    assert cp["best_score"] == 0.5
    assert cp["study_path"] == optuna_study_path("j1")


def test_wf_fold_merge_and_skip():
    cp = empty_wf_checkpoint(
        job_id="j2", strategy="ML_SIGNAL_BOOST", symbol="ETH", config={}, n_folds=3,
    )
    cp = merge_wf_fold(
        cp, job_id="j2", strategy="ML_SIGNAL_BOOST", symbol="ETH", config={},
        n_folds=3, fold_idx=0, fold_entry={"fold": 1, "ok": True},
    )
    cp = merge_wf_fold(
        cp, job_id="j2", strategy="ML_SIGNAL_BOOST", symbol="ETH", config={},
        n_folds=3, fold_idx=1, fold_entry={"fold": 2, "ok": True},
    )
    assert completed_fold_indices(cp) == {0, 1}
    assert len(cp["fold_results"]) == 2
    assert checkpoint_resume_ok(cp)
