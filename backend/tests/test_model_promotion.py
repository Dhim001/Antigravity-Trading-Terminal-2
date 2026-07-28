"""Tests for Phase 3.8 — champion-challenger model promotion."""

from __future__ import annotations

import pytest

from app.services.bots import model_promotion as mp


# --- PromotionPolicy ----------------------------------------------------


def test_policy_defaults():
    p = mp.PromotionPolicy()
    assert p.min_oos_improvement_pct == 5.0
    assert p.min_sample_size == 200
    assert p.require_champion_validation is False
    assert p.primary_metric == "oos_sharpe"


def test_policy_from_config():
    p = mp.PromotionPolicy.from_config({
        "cc_min_oos_improvement_pct": 10.0,
        "cc_min_sample_size": 500,
        "cc_require_champion_validation": True,
        "cc_primary_metric": "val_auc",
    })
    assert p.min_oos_improvement_pct == 10.0
    assert p.min_sample_size == 500
    assert p.require_champion_validation is True
    assert p.primary_metric == "val_auc"


def test_policy_from_config_empty():
    p = mp.PromotionPolicy.from_config(None)
    assert p.min_oos_improvement_pct == 5.0


# --- Metric extraction --------------------------------------------------


def test_extract_metric_primary():
    name, val = mp._extract_metric({"oos_sharpe": 1.5}, "oos_sharpe")
    assert name == "oos_sharpe"
    assert val == pytest.approx(1.5)


def test_extract_metric_fallback():
    name, val = mp._extract_metric({"val_auc": 0.7}, "oos_sharpe")
    assert name == "val_auc"
    assert val == pytest.approx(0.7)


def test_extract_metric_missing():
    name, val = mp._extract_metric({}, "oos_sharpe")
    assert val is None


def test_extract_metric_none():
    name, val = mp._extract_metric(None, "oos_sharpe")
    assert val is None


def test_extract_sample_size():
    assert mp._extract_sample_size({"oos_samples": 500}) == 500
    assert mp._extract_sample_size({"val_samples": 300}) == 300
    assert mp._extract_sample_size({}) == 0
    assert mp._extract_sample_size(None) == 0


# --- evaluate_challenger ------------------------------------------------


def test_challenger_wins_by_margin():
    policy = mp.PromotionPolicy(min_oos_improvement_pct=5.0, min_sample_size=100)
    v = mp.evaluate_challenger(
        {"oos_sharpe": 1.0, "oos_samples": 500},
        {"oos_sharpe": 1.2, "oos_samples": 500},
        policy,
    )
    assert v.decision == "promote"
    assert v.improvement_pct == pytest.approx(20.0)
    assert v.champion_value == 1.0
    assert v.challenger_value == 1.2


def test_challenger_loses():
    policy = mp.PromotionPolicy(min_oos_improvement_pct=5.0, min_sample_size=100)
    v = mp.evaluate_challenger(
        {"oos_sharpe": 1.5, "oos_samples": 500},
        {"oos_sharpe": 1.3, "oos_samples": 500},
        policy,
    )
    assert v.decision == "keep_champion"


def test_challenger_wins_but_below_margin():
    policy = mp.PromotionPolicy(min_oos_improvement_pct=10.0, min_sample_size=100)
    v = mp.evaluate_challenger(
        {"oos_sharpe": 1.0, "oos_samples": 500},
        {"oos_sharpe": 1.05, "oos_samples": 500},  # 5% < 10% required
        policy,
    )
    assert v.decision == "keep_champion"


def test_challenger_insufficient_samples():
    policy = mp.PromotionPolicy(min_sample_size=500)
    v = mp.evaluate_challenger(
        {"oos_sharpe": 1.0, "oos_samples": 1000},
        {"oos_sharpe": 2.0, "oos_samples": 100},  # too few
        policy,
    )
    assert v.decision == "shadow"
    assert "samples" in v.reason


def test_champion_unvalidated_cold_start_promotes():
    policy = mp.PromotionPolicy(require_champion_validation=False, min_sample_size=100)
    v = mp.evaluate_challenger(
        None,  # no champion metrics
        {"oos_sharpe": 1.0, "oos_samples": 500},
        policy,
    )
    assert v.decision == "promote"
    assert "cold-start" in v.reason


def test_champion_unvalidated_required_blocks():
    policy = mp.PromotionPolicy(require_champion_validation=True, min_sample_size=100)
    v = mp.evaluate_challenger(
        None,
        {"oos_sharpe": 1.0, "oos_samples": 500},
        policy,
    )
    assert v.decision == "keep_champion"


def test_both_unvalidated_shadows():
    policy = mp.PromotionPolicy(min_sample_size=100)
    v = mp.evaluate_challenger(
        None,
        {"oos_samples": 500},  # no metric
        policy,
    )
    assert v.decision == "shadow"


def test_log_loss_lower_is_better():
    policy = mp.PromotionPolicy(primary_metric="val_log_loss", min_sample_size=100)
    v = mp.evaluate_challenger(
        {"val_log_loss": 0.5, "oos_samples": 500},
        {"val_log_loss": 0.4, "oos_samples": 500},  # lower = better
        policy,
    )
    assert v.decision == "promote"
    assert v.improvement_pct > 0


def test_val_auc_higher_is_better():
    policy = mp.PromotionPolicy(primary_metric="val_auc", min_sample_size=100)
    v = mp.evaluate_challenger(
        {"val_auc": 0.65, "oos_samples": 500},
        {"val_auc": 0.70, "oos_samples": 500},
        policy,
    )
    assert v.decision == "promote"


# --- promote_challenger_if_better (integration) ------------------------


def test_promote_no_model_dir():
    result = mp.promote_challenger_if_better(
        "NONEXISTENT", "XYZ", "v1",
    )
    assert result["ok"] is False
    assert "No model directory" in result["error"]


def test_promote_full_flow_promotes(tmp_path, monkeypatch):
    """End-to-end: challenger beats champion → promoted."""
    # Build a fake model root with two versions
    root = tmp_path / "MODELS" / "ML_SIGNAL_BOOST" / "AAPL"
    versions_dir = root / "versions"
    champ_dir = versions_dir / "v1"
    chall_dir = versions_dir / "v2"
    champ_dir.mkdir(parents=True)
    chall_dir.mkdir(parents=True)
    # Champion artifact
    (champ_dir / "metadata.json").write_text('{"version_id":"v1"}')
    (champ_dir / "model.joblib").write_text("champ")
    # Challenger artifact
    (chall_dir / "metadata.json").write_text('{"version_id":"v2"}')
    (chall_dir / "model.joblib").write_text("chall")
    # Index
    import json
    index = [
        {"version_id": "v1", "status": "champion", "path": "versions/v1",
         "validation": {"oos_sharpe": 1.0, "oos_samples": 500}},
        {"version_id": "v2", "status": "challenger", "path": "versions/v2",
         "validation": {"oos_sharpe": 1.3, "oos_samples": 500}},
    ]
    (versions_dir / "index.json").write_text(json.dumps(index))
    # Current root copies champion
    (root / "metadata.json").write_text('{"version_id":"v1"}')
    (root / "model.joblib").write_text("champ")

    # Patch model_root_for in ml_model_artifacts (where promote imports it from)
    import app.services.bots.ml_model_artifacts as mma
    monkeypatch.setattr(mma, "model_root_for", lambda *a, **k: str(root))

    result = mp.promote_challenger_if_better(
        "ML_SIGNAL_BOOST", "AAPL", "v2",
        policy=mp.PromotionPolicy(min_oos_improvement_pct=5.0, min_sample_size=100),
    )
    assert result["ok"] is True
    assert result["decision"] == "promote"
    # The current root should now have the challenger's model
    assert (root / "model.joblib").read_text() == "chall"


def test_promote_full_flow_keeps_champion(tmp_path, monkeypatch):
    """End-to-end: challenger loses → champion stays."""
    root = tmp_path / "MODELS" / "ML_SIGNAL_BOOST" / "AAPL"
    versions_dir = root / "versions"
    champ_dir = versions_dir / "v1"
    chall_dir = versions_dir / "v2"
    champ_dir.mkdir(parents=True)
    chall_dir.mkdir(parents=True)
    (champ_dir / "metadata.json").write_text('{"version_id":"v1"}')
    (champ_dir / "model.joblib").write_text("champ")
    (chall_dir / "metadata.json").write_text('{"version_id":"v2"}')
    (chall_dir / "model.joblib").write_text("chall")
    import json
    index = [
        {"version_id": "v1", "status": "champion", "path": "versions/v1",
         "validation": {"oos_sharpe": 1.5, "oos_samples": 500}},
        {"version_id": "v2", "status": "challenger", "path": "versions/v2",
         "validation": {"oos_sharpe": 1.0, "oos_samples": 500}},  # worse
    ]
    (versions_dir / "index.json").write_text(json.dumps(index))
    (root / "metadata.json").write_text('{"version_id":"v1"}')
    (root / "model.joblib").write_text("champ")

    import app.services.bots.ml_model_artifacts as mma
    monkeypatch.setattr(mma, "model_root_for", lambda *a, **k: str(root))

    result = mp.promote_challenger_if_better(
        "ML_SIGNAL_BOOST", "AAPL", "v2",
        policy=mp.PromotionPolicy(min_oos_improvement_pct=5.0, min_sample_size=100),
    )
    assert result["ok"] is True
    assert result["decision"] == "keep_champion"
    # Champion's model stays in the current root
    assert (root / "model.joblib").read_text() == "champ"
