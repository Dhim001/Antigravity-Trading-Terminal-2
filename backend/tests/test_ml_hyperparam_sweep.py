"""Unit tests for ML hyperparam sweep + optimization store helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.bots.ml_hyperparam_sweep import (
    SWEEPABLE_ML_STRATEGIES,
    _pick_progress_metrics,
    _trial_warning,
    default_search_space,
    extract_objective_score,
    merge_search_space,
    run_ml_hyperparam_sweep,
)
from app.services.bots.optimization_store import (
    get_best_config,
    get_param_importance,
    save_optimization_run,
)


def test_default_search_spaces_cover_strategies():
    for strat in SWEEPABLE_ML_STRATEGIES:
        space = default_search_space(strat)
        assert isinstance(space, dict) and space, f"empty space for {strat}"


def test_progress_snapshot_metrics_and_warning():
    snap = _pick_progress_metrics({
        "ok": True,
        "metrics": {"accuracy": 0.61234, "f1": 0.55},
        "aggregate": {"sharpe": 1.25, "noise": "x"},
    })
    assert snap["accuracy"] == 0.6123
    assert snap["f1"] == 0.55
    assert snap["sharpe"] == 1.25
    assert _trial_warning({"ok": False, "error": "boom"}) == "boom"
    assert _trial_warning({"ok": True}, score=-1e9) == "objective unscored (fallback floor)"
    assert _trial_warning({"ok": True}, score=0.7) is None


def test_merge_search_space_overrides():
    space = merge_search_space(
        "ML_SIGNAL_BOOST",
        {"gbm_max_depth": {"type": "int", "low": 3, "high": 6}},
    )
    assert space["gbm_max_depth"]["low"] == 3
    assert space["gbm_max_depth"]["high"] == 6
    assert "gbm_learning_rate" in space


def test_extract_objective_score_val_accuracy():
    score = extract_objective_score(
        {"ok": True, "metrics": {"val_accuracy": 0.72}},
        "ML_SIGNAL_BOOST",
    )
    assert score == pytest.approx(0.72)
    fail = extract_objective_score({"ok": False}, "ML_SIGNAL_BOOST")
    assert fail < -1e8


def test_extract_objective_score_prefers_purged_cv_aggregate():
    score = extract_objective_score(
        {
            "ok": True,
            "aggregate": {"mean_oos_accuracy": 0.61},
            "metrics": {"val_accuracy": 0.99},  # IS — should lose to OOS aggregate
        },
        "ML_SIGNAL_BOOST",
    )
    assert score == pytest.approx(0.61)


def test_run_ml_hyperparam_sweep_budget_and_best(monkeypatch):
    candles = [{"close": 100 + i * 0.1, "high": 101, "low": 99, "volume": 1, "ATR_14": 1} for i in range(200)]
    call_n = {"n": 0}

    def fake_train(symbol, bars, config=None):
        call_n["n"] += 1
        cfg = config or {}
        # Prefer deeper trees in this fake world
        depth = float(cfg.get("gbm_max_depth") or 3)
        return {
            "ok": True,
            "metrics": {"val_accuracy": 0.5 + depth * 0.02},
        }

    result = run_ml_hyperparam_sweep(
        "ML_SIGNAL_BOOST",
        "BTCUSDT",
        candles,
        config={"timeframe": "1m", "skip_persist": True},
        max_trials=5,
        time_budget_sec=120,
        patience=10,
        multi_fidelity=False,
        objective_kind="val_holdout",
        train_fn=fake_train,
    )
    assert result["ok"] is True
    assert result["trials_completed"] >= 1
    assert call_n["n"] == result["trials_completed"]
    assert isinstance(result["best_hyperparams"], dict)
    assert result["best_hyperparams"].get("gbm_max_depth") is not None
    # Best should tend toward higher depth given fake objective
    assert float(result["best_score"]) > 0.5


def test_evaluate_trial_purged_cv_falls_back_on_short_series():
    from app.services.bots.ml_hyperparam_sweep import evaluate_trial_purged_cv

    short = [{"close": 1.0}] * 50

    def fake_train(symbol, bars, config=None):
        return {"ok": True, "metrics": {"val_accuracy": 0.55}}

    out = evaluate_trial_purged_cv(
        "ML_SIGNAL_BOOST", "BTCUSDT", short, {}, train_fn=fake_train,
    )
    assert out["ok"] is True
    assert out.get("objective_kind") == "val_holdout_fallback"


def test_get_best_config_and_importance(tmp_path, monkeypatch):
    # Use real DB path via existing connection — just save a run
    run_id = save_optimization_run(
        symbol="ETHUSDT",
        strategy="ML_SIGNAL_BOOST",
        objective="ml_val_score",
        request={
            "kind": "ml_hyperparam_sweep",
            "importance_ranking": {"gbm_max_depth": 0.6, "gbm_learning_rate": 0.4},
        },
        results=[{"trial": 1, "score": 0.7, "params": {"gbm_max_depth": 6}}],
        best_config={"gbm_max_depth": 6, "gbm_learning_rate": 0.05},
    )
    assert run_id
    best = get_best_config(run_id, source="best")
    assert best["gbm_max_depth"] == 6
    imp = get_param_importance(run_id)
    assert imp["gbm_max_depth"] == pytest.approx(0.6)


def test_sobol_mode_in_sweep_modes():
    from app.services.bots.backtest_sweep import SWEEP_MODES, MAX_SWEEP_COMBOS_EXTENDED, expand_sweep_grid

    assert "sobol" in SWEEP_MODES
    assert MAX_SWEEP_COMBOS_EXTENDED == 200
    configs = expand_sweep_grid(
        {"trailing_stop_percent": 1},
        {
            "sweep_mode": "sobol",
            "trailing_stop_percent": [1, 2, 3],
            "take_profit_percent": [2, 3, 5],
            "max_combos": 8,
            "sweep_seed": 1,
        },
    )
    assert len(configs) >= 1
    assert len(configs) <= 8


def test_nsga2_selection_ranks():
    from app.services.bots.backtest_multi_objective import crowding_distance, run_nsga2_selection

    rows = [
        {"label": "a", "total_pnl": 100, "summary": {"max_drawdown": 5, "sharpe_ratio": 1.2}, "trade_count": 10},
        {"label": "b", "total_pnl": 80, "summary": {"max_drawdown": 2, "sharpe_ratio": 1.5}, "trade_count": 12},
        {"label": "c", "total_pnl": 90, "summary": {"max_drawdown": 8, "sharpe_ratio": 0.9}, "trade_count": 8},
    ]
    elite = run_nsga2_selection(rows, population=3)
    assert len(elite) >= 1
    assert "crowding_distance" in elite[0]
    cds = crowding_distance(rows)
    assert len(cds) == 3


def test_nsga2_handles_duplicate_labels_without_hang():
    from app.services.bots.backtest_multi_objective import run_nsga2_selection

    # Same truncated label, different metrics — must still peel and finish
    rows = [
        {"label": "SL=1 TP=2", "total_pnl": 100, "summary": {"max_drawdown": 5, "sharpe_ratio": 1.0}, "config": {"x": 1}},
        {"label": "SL=1 TP=2", "total_pnl": 50, "summary": {"max_drawdown": 2, "sharpe_ratio": 1.2}, "config": {"x": 2}},
        {"label": "SL=1 TP=2", "total_pnl": 75, "summary": {"max_drawdown": 9, "sharpe_ratio": 0.5}, "config": {"x": 3}},
    ]
    elite = run_nsga2_selection(rows, population=3)
    assert len(elite) == 3


def test_fidelity_caps_do_not_inject_early_stop():
    from app.services.bots.ml_hyperparam_sweep import _apply_fidelity_caps

    capped = _apply_fidelity_caps({"gbm_max_depth": 6, "epochs": 90}, screen=True)
    assert "early_stop_patience" not in capped
    # Opt #7: epochs/5 screen fidelity (90 → 18)
    assert capped["epochs"] == 18
    assert capped["epochs"] <= 30
    with_patience = _apply_fidelity_caps({"early_stop_patience": 15}, screen=True)
    assert with_patience["early_stop_patience"] == 8


def test_optuna_startup_parallel_records_all_trials(monkeypatch):
    """Opt #3: parallel startup ask/tell must complete every trial."""
    from app.services.bots import ml_hyperparam_sweep as sweep

    monkeypatch.setattr(sweep, "_optuna_startup_workers", lambda n, s: min(3, n))

    candles = [
        {"close": 100 + i * 0.1, "high": 101, "low": 99, "volume": 1, "ATR_14": 1}
        for i in range(200)
    ]
    seen = {"n": 0}

    def fake_train(symbol, bars, config=None):
        seen["n"] += 1
        depth = float((config or {}).get("gbm_max_depth") or 3)
        return {"ok": True, "metrics": {"val_accuracy": 0.5 + depth * 0.01}}

    result = sweep.run_ml_hyperparam_sweep(
        "ML_SIGNAL_BOOST",
        "BTCUSDT",
        candles,
        config={"timeframe": "1m", "skip_persist": True},
        max_trials=5,
        time_budget_sec=120,
        patience=20,
        multi_fidelity=False,
        objective_kind="val_holdout",
        train_fn=fake_train,
    )
    assert result["ok"] is True
    assert result["trials_completed"] == seen["n"]
    assert result["trials_completed"] >= 1


def test_optuna_startup_parallel_leaves_no_orphan_trials(monkeypatch):
    """Parallel ask/tell must complete every asked trial (no RUNNING orphans)."""
    import optuna
    from app.services.bots import ml_hyperparam_sweep as sweep

    monkeypatch.setattr(sweep, "_optuna_startup_workers", lambda n, s: min(3, n))
    studies: list = []
    real_create = optuna.create_study

    def _capture_study(*args, **kwargs):
        study = real_create(*args, **kwargs)
        studies.append(study)
        return study

    monkeypatch.setattr(optuna, "create_study", _capture_study)

    candles = [
        {"close": 100 + i * 0.1, "high": 101, "low": 99, "volume": 1, "ATR_14": 1}
        for i in range(200)
    ]

    def fake_train(symbol, bars, config=None):
        return {"ok": True, "metrics": {"val_accuracy": 0.6}}

    result = sweep.run_ml_hyperparam_sweep(
        "ML_SIGNAL_BOOST",
        "BTCUSDT",
        candles,
        config={"timeframe": "1m", "skip_persist": True},
        max_trials=5,
        time_budget_sec=120,
        patience=20,
        multi_fidelity=False,
        objective_kind="val_holdout",
        train_fn=fake_train,
    )
    assert result["ok"] is True
    assert studies
    states = {t.state for t in studies[0].trials}
    assert optuna.trial.TrialState.RUNNING not in states
    assert optuna.trial.TrialState.WAITING not in states
    complete = [
        t for t in studies[0].trials if t.state == optuna.trial.TrialState.COMPLETE
    ]
    assert len(complete) >= 3


def test_optuna_parallel_startup_disables_nested_wf_folds(monkeypatch):
    from app.services.bots import ml_hyperparam_sweep as sweep

    monkeypatch.setattr(sweep, "_optuna_startup_workers", lambda n, s: min(2, n))
    seen_flags: list[bool] = []

    candles = [
        {"close": 100 + i * 0.1, "high": 101, "low": 99, "volume": 1, "ATR_14": 1}
        for i in range(200)
    ]

    def fake_train(symbol, bars, config=None):
        cfg = config or {}
        seen_flags.append(bool(cfg.get("_disable_wf_fold_parallel")))
        return {"ok": True, "metrics": {"val_accuracy": 0.6}}

    sweep.run_ml_hyperparam_sweep(
        "ML_SIGNAL_BOOST",
        "BTCUSDT",
        candles,
        config={"timeframe": "1m", "skip_persist": True},
        max_trials=4,
        time_budget_sec=60,
        patience=20,
        multi_fidelity=False,
        objective_kind="val_holdout",
        train_fn=fake_train,
    )
    # Parallel startup batch (first 2) should disable nested WF fold pools.
    assert len(seen_flags) >= 2
    assert seen_flags[0] is True
    assert seen_flags[1] is True


def test_merge_optimized_requires_opt_in_on_lab_path(monkeypatch):
    from app.services.bots import optimization_store as store

    monkeypatch.setattr(
        store,
        "get_latest_optimized_hyperparams",
        lambda *a, **k: {"learning_rate": 0.002, "lookback": 90},
    )
    # Bare Trigger (no champion / use_optimized) must not inherit Optuna knobs.
    bare = store.merge_optimized_train_hyperparams(
        {"epochs": 60}, "AAPL", "LSTM_DIRECTION", require_opt_in=True,
    )
    assert "learning_rate" not in bare
    assert bare["epochs"] == 60
    # Apply & Retrain opts in.
    opted = store.merge_optimized_train_hyperparams(
        {"epochs": 60, "champion_train": True},
        "AAPL",
        "LSTM_DIRECTION",
        require_opt_in=True,
    )
    assert opted["learning_rate"] == 0.002
    assert opted["lookback"] == 90
    # Scheduler drain keeps require_opt_in=False (always merge).
    drained = store.merge_optimized_train_hyperparams(
        {"epochs": 60}, "AAPL", "LSTM_DIRECTION", require_opt_in=False,
    )
    assert drained["learning_rate"] == 0.002


def test_merge_optimized_train_hyperparams_fills_gaps_only(monkeypatch):
    from app.services.bots import optimization_store as store

    monkeypatch.setattr(
        store,
        "get_latest_optimized_hyperparams",
        lambda symbol, strategy, prefer_ml_sweep=True: {
            "learning_rate": 0.002,
            "lookback": 90,
            "epochs": 120,
            "gbm_learning_rate": 0.07,
            "trailing_stop_percent": 99,  # risk key — must never be copied
        },
    )
    merged = store.merge_optimized_train_hyperparams(
        {"epochs": 60, "timeframe": "5m"},
        "AAPL",
        "LSTM_DIRECTION",
    )
    # Explicit client key wins
    assert merged["epochs"] == 60
    # Gaps filled from Optuna best
    assert merged["learning_rate"] == 0.002
    assert merged["lookback"] == 90
    assert merged["retrain_from_optimized"] is True
    assert "learning_rate" in merged["_optimized_hyperparams_applied"]
    # Risk / non-train keys stay out
    assert "trailing_stop_percent" not in merged
    assert merged["timeframe"] == "5m"


def test_apply_champion_train_overrides_clears_wf():
    from app.services.bots.ml_training_window import apply_champion_train_overrides

    cfg = apply_champion_train_overrides(
        {"_wf_mode": True, "skip_persist": True, "epochs": 40},
        {"champion_train": True},
    )
    assert cfg["champion_train"] is True
    assert "_wf_mode" not in cfg
    assert cfg["skip_persist"] is False
    assert apply_champion_train_overrides({"_wf_mode": True}, {})["_wf_mode"] is True



def test_merge_optimized_skips_when_opted_out(monkeypatch):
    from app.services.bots import optimization_store as store

    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("should not look up")

    monkeypatch.setattr(store, "get_latest_optimized_hyperparams", _boom)
    out = store.merge_optimized_train_hyperparams(
        {"skip_optimized_hyperparams": True, "epochs": 10},
        "AAPL",
        "LSTM_DIRECTION",
    )
    assert out["epochs"] == 10
    assert called["n"] == 0


def test_merge_optimized_noop_without_store(monkeypatch):
    from app.services.bots import optimization_store as store

    monkeypatch.setattr(
        store, "get_latest_optimized_hyperparams", lambda *a, **k: None,
    )
    out = store.merge_optimized_train_hyperparams({}, "AAPL", "ML_SIGNAL_BOOST")
    assert out == {}
    assert "retrain_from_optimized" not in out


def test_prepare_lab_champion_strips_trial_flags():
    from app.services.bots.ml_training_window import (
        prepare_lab_champion_train_config,
        skip_live_artifact_writes,
    )

    dirty = {
        "epochs": 80,
        "learning_rate": 0.001,
        "skip_persist": True,
        "skip_snapshot": True,
        "skip_refit": True,
        "_wf_mode": True,
        "wf_mode": True,
        "_hyperparam_screen": True,
        "hyperparam_cv_max_bars": 1200,
        "skip_onnx_export": True,
        "champion_train": True,
    }
    clean = prepare_lab_champion_train_config(dirty)
    assert clean["skip_persist"] is False
    assert clean["skip_snapshot"] is False
    assert "_wf_mode" not in clean
    assert "wf_mode" not in clean
    assert "_hyperparam_screen" not in clean
    assert "hyperparam_cv_max_bars" not in clean
    assert "skip_onnx_export" not in clean
    assert "skip_refit" not in clean  # popped so calendar can setdefault
    assert clean["epochs"] == 80
    assert skip_live_artifact_writes(clean) is False


def test_slim_keys_keep_best_hyperparams_for_apply(tmp_path, monkeypatch):
    """Offloaded sweep results must still expose best_hyperparams in the slim headline."""
    from app.services.bots import ml_job_store as store

    monkeypatch.setattr(store, "_result_dir", lambda: str(tmp_path))
    monkeypatch.setattr("app.config.ML_JOB_RESULT_OFFLOAD_BYTES", 100)
    store.reset_ml_job_store_for_tests()

    jid = store.create_ml_job(kind="hyperparam_sweep", strategy="LSTM_DIRECTION", symbol="AAPL")
    big = {
        "ok": True,
        "best_hyperparams": {"epochs": 90, "learning_rate": 0.001, "hidden_dim": 128},
        "best_score": 0.61,
        "trials_completed": 12,
        "optimization_run_id": "run-abc",
        "trial_history": [{"trial": i, "pad": "x" * 200} for i in range(40)],
    }
    store.finish_ml_job(jid, "done", result=big)
    raw = store.get_ml_job(jid)["result"]
    assert raw.get("_slimmed") is True
    assert raw.get("best_hyperparams", {}).get("epochs") == 90
    pub = store.public_ml_job(store.get_ml_job(jid))
    assert pub["result"]["best_hyperparams"]["epochs"] == 90
    assert pub["result"]["best_score"] == 0.61
