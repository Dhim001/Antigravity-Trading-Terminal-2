"""Tests for ML Training & Validation Philosophy components."""

from __future__ import annotations

import json
import os
import tempfile
import time
import pytest
from unittest.mock import patch, MagicMock

# ── Walk-Forward Validator Tests ──────────────────────────────────────────


class TestWalkForwardFolds:
    def test_generate_folds_basic(self):
        from app.services.bots.ml_walk_forward_validator import generate_wf_folds
        folds = generate_wf_folds(
            2000, n_folds=5, mode="rolling",
            purge_bars=30, embargo_pct=1.0,
        )
        assert len(folds) >= 3
        for f in folds:
            assert f["train_start"] < f["train_end"]
            assert f["test_start"] < f["test_end"]
            assert f["train_end"] <= f["test_start"] - f["purge_bars"] + 1

    def test_generate_folds_insufficient_data(self):
        from app.services.bots.ml_walk_forward_validator import generate_wf_folds
        folds = generate_wf_folds(100, n_folds=5, purge_bars=30)
        assert len(folds) == 0

    def test_generate_folds_anchored(self):
        from app.services.bots.ml_walk_forward_validator import generate_wf_folds
        folds = generate_wf_folds(
            3000, n_folds=4, mode="anchored", purge_bars=20,
        )
        for f in folds:
            assert f["train_start"] == 0, "Anchored mode should start from 0"

    def test_fold_no_overlap(self):
        from app.services.bots.ml_walk_forward_validator import generate_wf_folds
        folds = generate_wf_folds(5000, n_folds=5, purge_bars=30)
        for f in folds:
            assert f["train_end"] + f["purge_bars"] <= f["test_start"]


class TestWalkForwardTrain:
    def test_unknown_strategy_error(self):
        from app.services.bots.ml_walk_forward_validator import walk_forward_ml_train
        result = walk_forward_ml_train(
            "NONEXISTENT_STRATEGY", "BTCUSDT", [{"close": 100}] * 1000,
        )
        assert result["ok"] is False
        assert "No trainer" in result["error"]

    def test_insufficient_candles_error(self):
        from app.services.bots.ml_walk_forward_validator import walk_forward_ml_train
        result = walk_forward_ml_train(
            "ML_SIGNAL_BOOST", "BTCUSDT", [{"close": 100}] * 50,
        )
        assert result["ok"] is False
        assert "Insufficient" in result["error"]


class TestAggregation:
    def test_aggregate_metrics(self):
        from app.services.bots.ml_walk_forward_validator import _aggregate_fold_metrics

        folds = [
            {"oos_metrics": {"accuracy": 0.55, "n_signals": 20}},
            {"oos_metrics": {"accuracy": 0.60, "n_signals": 15}},
            {"oos_metrics": {"accuracy": 0.52, "n_signals": 18}},
        ]
        agg = _aggregate_fold_metrics(folds)
        assert 0.50 < agg["mean_oos_accuracy"] < 0.65
        assert agg["total_oos_signals"] == 53

    def test_stability_declining(self):
        from app.services.bots.ml_walk_forward_validator import _compute_stability

        folds = [
            {"oos_metrics": {"accuracy": 0.7}},
            {"oos_metrics": {"accuracy": 0.6}},
            {"oos_metrics": {"accuracy": 0.5}},
            {"oos_metrics": {"accuracy": 0.4}},
            {"oos_metrics": {"accuracy": 0.3}},
        ]
        stab = _compute_stability(folds)
        assert stab["trend"] == "declining"
        assert stab["stable"] is False

    def test_stability_stable(self):
        from app.services.bots.ml_walk_forward_validator import _compute_stability

        folds = [
            {"oos_metrics": {"accuracy": 0.55}},
            {"oos_metrics": {"accuracy": 0.56}},
            {"oos_metrics": {"accuracy": 0.54}},
            {"oos_metrics": {"accuracy": 0.55}},
        ]
        stab = _compute_stability(folds)
        assert stab["trend"] == "stable"
        assert stab["stable"] is True


class TestRecommendation:
    def test_deploy_recommendation(self):
        from app.services.bots.ml_walk_forward_validator import _make_recommendation

        rec = _make_recommendation(
            {"mean_oos_accuracy": 0.55, "total_oos_signals": 100},
            {"cv": 0.1, "trend": "stable"},
            5, 5,
        )
        assert "DEPLOY" in rec

    def test_reject_recommendation(self):
        from app.services.bots.ml_walk_forward_validator import _make_recommendation

        rec = _make_recommendation(
            {"mean_oos_accuracy": 0.2, "total_oos_signals": 5},
            {"cv": 0.6, "trend": "declining"},
            2, 5,
        )
        assert "REJECT" in rec

    def test_review_recommendation(self):
        from app.services.bots.ml_walk_forward_validator import _make_recommendation

        rec = _make_recommendation(
            {"mean_oos_accuracy": 0.45, "total_oos_signals": 50},
            {"cv": 0.5, "trend": "stable"},
            5, 5,
        )
        assert "REVIEW" in rec


# ── PBO Validator Tests ───────────────────────────────────────────────────


class TestPBOGate:
    def test_pbo_gate_pass(self):
        from app.services.bots.ml_pbo_validator import pbo_gate_check

        ok, reason = pbo_gate_check({"ok": True, "pbo": 0.3, "degradation": 0.05})
        assert ok is True
        assert "passed" in reason

    def test_pbo_gate_block_high_pbo(self):
        from app.services.bots.ml_pbo_validator import pbo_gate_check

        ok, reason = pbo_gate_check({"ok": True, "pbo": 0.7, "degradation": 0.1})
        assert ok is False
        assert "exceeds" in reason

    def test_pbo_gate_block_degradation(self):
        from app.services.bots.ml_pbo_validator import pbo_gate_check

        ok, reason = pbo_gate_check({"ok": True, "pbo": 0.4, "degradation": 0.3})
        assert ok is False
        assert "degradation" in reason

    def test_pbo_gate_skip_if_not_computed(self):
        from app.services.bots.ml_pbo_validator import pbo_gate_check

        ok, reason = pbo_gate_check({"ok": False})
        assert ok is True

    def test_pbo_recommendation_strings(self):
        from app.services.bots.ml_pbo_validator import _pbo_recommendation

        assert "REJECT" in _pbo_recommendation(0.8, 0.1)
        assert "REVIEW" in _pbo_recommendation(0.55, 0.1)
        assert "DEPLOY" in _pbo_recommendation(0.2, 0.05)
        assert "degradation" in _pbo_recommendation(0.3, 0.2).lower()


# ── Retrain Scheduler Tests ──────────────────────────────────────────────


class TestRetrainScheduler:
    def test_scheduler_detects_missing_model(self):
        from app.services.bots.ml_retrain_scheduler import MlRetrainScheduler

        scheduler = MlRetrainScheduler()
        bots = [{"strategy": "ML_SIGNAL_BOOST", "symbol": "BTCUSDT"}]

        with patch("app.services.bots.ml_retrain_scheduler.get_model_age_hours", return_value=None):
            actions = scheduler.check(bots)
        assert len(actions) == 1
        assert actions[0]["reason"] == "no_model"
        assert actions[0]["priority"] == 10

    def test_scheduler_detects_stale_model(self):
        from app.services.bots.ml_retrain_scheduler import MlRetrainScheduler

        scheduler = MlRetrainScheduler(max_age_hours=24)
        bots = [{"strategy": "LSTM_DIRECTION", "symbol": "ETHUSDT"}]

        with patch("app.services.bots.ml_retrain_scheduler.get_model_age_hours", return_value=48.0):
            actions = scheduler.check(bots)
        assert len(actions) == 1
        assert actions[0]["reason"] == "stale"

    def test_scheduler_detects_alpha_decay(self):
        from app.services.bots.ml_retrain_scheduler import MlRetrainScheduler

        scheduler = MlRetrainScheduler()
        bots = [{"strategy": "ML_SIGNAL_BOOST", "symbol": "BTCUSDT"}]
        alpha_scores = {"BTCUSDT:ML_SIGNAL_BOOST": 0.7}

        with patch("app.services.bots.ml_retrain_scheduler.get_model_age_hours", return_value=12.0):
            actions = scheduler.check(bots, alpha_scores=alpha_scores)
        assert len(actions) == 1
        assert actions[0]["reason"] == "alpha_decay"

    def test_scheduler_respects_cooldown(self):
        from app.services.bots.ml_retrain_scheduler import MlRetrainScheduler

        scheduler = MlRetrainScheduler(cooldown_hours=24)
        scheduler.record_retrain("ML_SIGNAL_BOOST", "BTCUSDT")
        bots = [{"strategy": "ML_SIGNAL_BOOST", "symbol": "BTCUSDT"}]

        with patch("app.services.bots.ml_retrain_scheduler.get_model_age_hours", return_value=None):
            actions = scheduler.check(bots)
        assert len(actions) == 0, "Should be on cooldown"

    def test_scheduler_ignores_non_ml(self):
        from app.services.bots.ml_retrain_scheduler import MlRetrainScheduler

        scheduler = MlRetrainScheduler()
        bots = [{"strategy": "MACD_RSI", "symbol": "BTCUSDT"}]
        actions = scheduler.check(bots)
        assert len(actions) == 0

    def test_should_retrain_quick_check(self):
        from app.services.bots.ml_retrain_scheduler import MlRetrainScheduler

        scheduler = MlRetrainScheduler()
        should, reason = scheduler.should_retrain("MACD_RSI", "BTCUSDT")
        assert should is False
        assert reason == "not_ml"

        with patch("app.services.bots.ml_retrain_scheduler.get_model_age_hours", return_value=None):
            should, reason = scheduler.should_retrain("ML_SIGNAL_BOOST", "BTCUSDT")
        assert should is True
        assert reason == "no_model"

    def test_scheduler_deduplicates_bots(self):
        from app.services.bots.ml_retrain_scheduler import MlRetrainScheduler

        scheduler = MlRetrainScheduler()
        bots = [
            {"strategy": "ML_SIGNAL_BOOST", "symbol": "BTCUSDT"},
            {"strategy": "ML_SIGNAL_BOOST", "symbol": "BTCUSDT"},
        ]
        with patch("app.services.bots.ml_retrain_scheduler.get_model_age_hours", return_value=None):
            actions = scheduler.check(bots)
        assert len(actions) == 1

    def test_scheduler_priority_sorting(self):
        from app.services.bots.ml_retrain_scheduler import MlRetrainScheduler

        scheduler = MlRetrainScheduler(max_age_hours=24)
        bots = [
            {"strategy": "LSTM_DIRECTION", "symbol": "ETHUSDT"},
            {"strategy": "ML_SIGNAL_BOOST", "symbol": "BTCUSDT"},
        ]
        alpha_scores = {"BTCUSDT:ML_SIGNAL_BOOST": 0.7}

        def mock_age(strategy, symbol):
            if symbol == "ETHUSDT":
                return 48.0  # stale, priority 5
            return 12.0  # not stale, alpha_decay priority 8

        with patch("app.services.bots.ml_retrain_scheduler.get_model_age_hours", side_effect=mock_age):
            actions = scheduler.check(bots, alpha_scores=alpha_scores)
        assert len(actions) == 2
        # Alpha decay (priority 8) should come before stale (priority 5)
        assert actions[0]["reason"] == "alpha_decay"
        assert actions[1]["reason"] == "stale"


# ── Deploy Gate ML Checks ─────────────────────────────────────────────────


class TestDeployGateML:
    def test_deploy_gate_blocks_missing_ml_model(self):
        from app.services.bots.deploy_gate import evaluate_deploy_gate

        results = {"total_pnl": 100, "trade_count": 20, "summary": {"total_pnl": 100, "total_trades": 20}}
        with patch("app.services.bots.ml_retrain_scheduler.get_model_age_hours", return_value=None):
            gate = evaluate_deploy_gate(
                results,
                symbol="BTCUSDT",
                run_config={"strategy": "ML_SIGNAL_BOOST"},
            )
        ml_checks = [c for c in gate["checks"] if c["id"] == "ml_model_exists"]
        assert len(ml_checks) == 1
        assert ml_checks[0]["ok"] is False
        assert ml_checks[0]["level"] == "block"

    def test_deploy_gate_warns_stale_ml_model(self):
        from app.services.bots.deploy_gate import evaluate_deploy_gate

        results = {"total_pnl": 100, "trade_count": 20, "summary": {"total_pnl": 100, "total_trades": 20}}
        with patch("app.services.bots.ml_retrain_scheduler.get_model_age_hours", return_value=200.0), \
             patch("app.services.bots.ml_retrain_scheduler.get_model_metadata", return_value={}):
            gate = evaluate_deploy_gate(
                results,
                symbol="BTCUSDT",
                run_config={"strategy": "ML_SIGNAL_BOOST"},
            )
        age_checks = [c for c in gate["checks"] if c["id"] == "ml_model_age"]
        assert len(age_checks) == 1
        assert age_checks[0]["level"] == "warn"

    def test_deploy_gate_blocks_high_pbo(self):
        from app.services.bots.deploy_gate import evaluate_deploy_gate

        results = {"total_pnl": 100, "trade_count": 20, "summary": {"total_pnl": 100, "total_trades": 20}}
        with patch("app.services.bots.ml_retrain_scheduler.get_model_age_hours", return_value=10.0), \
             patch("app.services.bots.ml_retrain_scheduler.get_model_metadata", return_value={"pbo": 0.7}):
            gate = evaluate_deploy_gate(
                results,
                symbol="BTCUSDT",
                run_config={"strategy": "ML_SIGNAL_BOOST"},
            )
        pbo_checks = [c for c in gate["checks"] if c["id"] == "ml_pbo"]
        assert len(pbo_checks) == 1
        assert pbo_checks[0]["level"] == "block"

    def test_deploy_gate_passes_healthy_ml_model(self):
        from app.services.bots.deploy_gate import evaluate_deploy_gate

        results = {"total_pnl": 100, "trade_count": 20, "summary": {"total_pnl": 100, "total_trades": 20}}
        with patch("app.services.bots.ml_retrain_scheduler.get_model_age_hours", return_value=10.0), \
             patch("app.services.bots.ml_retrain_scheduler.get_model_metadata", return_value={"pbo": 0.2}):
            gate = evaluate_deploy_gate(
                results,
                symbol="BTCUSDT",
                run_config={"strategy": "ML_SIGNAL_BOOST"},
            )
        ml_checks = [c for c in gate["checks"] if c["id"] == "ml_model_exists"]
        assert all(c["ok"] for c in ml_checks)


# ── ML Strategy Detection ────────────────────────────────────────────────


class TestMLStrategyDetection:
    def test_is_ml_strategy(self):
        from app.services.bots.ml_walk_forward_validator import is_ml_strategy

        assert is_ml_strategy("ML_SIGNAL_BOOST") is True
        assert is_ml_strategy("LSTM_DIRECTION") is True
        assert is_ml_strategy("RL_PPO_AGENT") is True
        assert is_ml_strategy("TCN_MULTI_HORIZON") is True
        assert is_ml_strategy("VAE_REGIME_DETECTOR") is True
        assert is_ml_strategy("TRANSFORMER_SIGNAL") is True
        assert is_ml_strategy("GNN_CROSS_ASSET") is True
        assert is_ml_strategy("MACD_RSI") is False
        assert is_ml_strategy("CHART_AGENT") is False

    def test_gnn_registered_when_importable(self):
        from app.services.bots.ml_walk_forward_validator import get_trainer, _TRAINER_REGISTRY
        _TRAINER_REGISTRY.clear()
        trainer = get_trainer("GNN_CROSS_ASSET")
        if trainer is not None:
            assert callable(trainer)

    def test_lstm_http_alias_exists(self):
        from app.services.bots.ml_lstm_trainer import train_lstm_model, train_lstm_signal_model
        assert train_lstm_model is train_lstm_signal_model
