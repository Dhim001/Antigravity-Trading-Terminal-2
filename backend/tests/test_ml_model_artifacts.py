"""Tests for on-disk ML model versioning helpers."""

from __future__ import annotations

import json
import os

from unittest.mock import patch

from app.services.bots.ml_model_artifacts import (
    activate_model_version,
    dataset_summary_from_metadata,
    delete_model_version,
    list_model_versions,
    resolve_model_dir,
    snapshot_current_version,
    validation_summary_from_metadata,
    version_id_from_iso,
)


def test_version_id_from_iso_normalizes():
    vid = version_id_from_iso("2026-07-15T22:30:00.123456Z")
    assert vid == "20260715T223000Z"
    assert ":" not in vid
    assert "-" not in vid
    assert vid.startswith("20260715T")


def test_snapshot_and_resolve(tmp_path):
    root = tmp_path / "BTCUSDT"
    root.mkdir()
    meta = {
        "trained_at": "2026-07-15T12:00:00Z",
        "model_type": "ml_signal_boost",
        "sample_count": 1000,
        "label_distribution": {"BUY": 100, "SELL": 90, "NONE": 810},
        "feature_names": ["rsi", "macd"],
        "metrics": {"val_accuracy": 0.62, "train_samples": 800, "val_samples": 200},
    }
    (root / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    (root / "model.joblib").write_bytes(b"fake-model")

    entry = snapshot_current_version(str(root), strategy="ML_SIGNAL_BOOST")
    assert entry is not None
    assert entry["version_id"]
    assert (root / "versions" / entry["version_id"] / "model.joblib").is_file()
    assert (root / "versions" / "index.json").is_file()

    versions = list_model_versions(str(root))
    assert len(versions) >= 1
    assert versions[0]["is_current"] is True

    resolved = resolve_model_dir(str(root), meta["trained_at"])
    assert resolved.endswith(entry["version_id"]) or os.path.basename(resolved) == entry["version_id"]

    # Unknown pin falls back to current root
    fallback = resolve_model_dir(str(root), "1999-01-01T00:00:00Z")
    assert fallback == str(root)


def test_dataset_summary_from_metadata():
    summary = dataset_summary_from_metadata({
        "sample_count": 500,
        "label_distribution": {"BUY": 1},
        "feature_names": ["a", "b"],
        "metrics": {"train_samples": 400, "val_samples": 100},
        "model_type": "lstm",
        "trained_at": "2026-01-01T00:00:00Z",
        "version_id": "20260101T000000Z",
    })
    assert summary["sample_count"] == 500
    assert summary["train_samples"] == 400
    assert summary["feature_names"] == ["a", "b"]
    assert summary["version_id"] == "20260101T000000Z"


def test_snapshot_prunes_old_versions(tmp_path):
    root = tmp_path / "ETHUSDT"
    root.mkdir()
    for i in range(3):
        meta = {
            "trained_at": f"2026-07-1{i}T10:00:00Z",
            "model_type": "test",
        }
        (root / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        (root / "model.joblib").write_bytes(b"x")
        snapshot_current_version(
            str(root), strategy="ML_SIGNAL_BOOST", max_kept=2, replace_current=False,
        )

    versions = list_model_versions(str(root))
    assert len(versions) <= 2


def test_retrain_replaces_current_version_in_place(tmp_path):
    """Trigger retrain must update the same history row, not append a duplicate."""
    root = tmp_path / "BTCUSDT"
    root.mkdir()
    meta1 = {
        "trained_at": "2026-08-08T14:50:34Z",
        "model_type": "ml_signal_boost",
        "sample_count": 2050,
    }
    (root / "metadata.json").write_text(json.dumps(meta1), encoding="utf-8")
    (root / "model.joblib").write_bytes(b"v1")
    e1 = snapshot_current_version(str(root), strategy="ML_SIGNAL_BOOST")
    assert e1 is not None
    vid = e1["version_id"]

    # User rename / Keep must survive retrain.
    from app.services.bots.ml_model_artifacts import update_model_version_meta

    with patch(
        "app.services.bots.ml_model_artifacts.model_root_for",
        return_value=str(root),
    ):
        update_model_version_meta(
            "ML_SIGNAL_BOOST", "BTCUSDT", vid,
            display_name="Modelbase_00", protected=True,
        )

    meta2 = {
        "trained_at": "2026-08-08T15:40:53Z",
        "model_type": "ml_signal_boost",
        "sample_count": 24950,
    }
    (root / "metadata.json").write_text(json.dumps(meta2), encoding="utf-8")
    (root / "model.joblib").write_bytes(b"v2-retrained")
    e2 = snapshot_current_version(str(root), strategy="ML_SIGNAL_BOOST")
    assert e2 is not None
    assert e2["version_id"] == vid
    assert e2["sample_count"] == 24950
    assert e2.get("display_name") == "Modelbase_00"
    assert e2.get("protected") is True

    versions = list_model_versions(str(root))
    assert len(versions) == 1
    assert versions[0]["version_id"] == vid
    assert versions[0]["is_current"] is True
    assert (root / "versions" / vid / "model.joblib").read_bytes() == b"v2-retrained"


def test_activate_model_version_promotes_snapshot(tmp_path):
    root = tmp_path / "BNBUSDT"
    root.mkdir()
    # v1 current
    meta1 = {"trained_at": "2026-07-10T10:00:00Z", "model_type": "ml_signal_boost", "tag": "v1"}
    (root / "metadata.json").write_text(json.dumps(meta1), encoding="utf-8")
    (root / "model.joblib").write_bytes(b"model-v1")
    e1 = snapshot_current_version(
        str(root), strategy="ML_SIGNAL_BOOST", replace_current=False,
    )
    assert e1 is not None

    # v2 current
    meta2 = {"trained_at": "2026-07-11T10:00:00Z", "model_type": "ml_signal_boost", "tag": "v2"}
    (root / "metadata.json").write_text(json.dumps(meta2), encoding="utf-8")
    (root / "model.joblib").write_bytes(b"model-v2")
    e2 = snapshot_current_version(
        str(root), strategy="ML_SIGNAL_BOOST", replace_current=False,
    )
    assert e2 is not None
    assert (root / "model.joblib").read_bytes() == b"model-v2"

    with patch(
        "app.services.bots.ml_model_artifacts.model_root_for",
        return_value=str(root),
    ):
        result = activate_model_version("ML_SIGNAL_BOOST", "BNBUSDT", meta1["trained_at"])

    assert result["ok"] is True
    assert result["version_id"] == e1["version_id"]
    assert (root / "model.joblib").read_bytes() == b"model-v1"
    current = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    assert current.get("tag") == "v1"
    versions = list_model_versions(str(root))
    current_rows = [v for v in versions if v.get("is_current")]
    assert len(current_rows) == 1
    assert current_rows[0]["version_id"] == e1["version_id"]


def test_delete_model_version_removes_snapshot(tmp_path):
    root = tmp_path / "SOLUSDT"
    root.mkdir()
    meta1 = {"trained_at": "2026-07-10T10:00:00Z", "model_type": "ml_signal_boost", "tag": "v1"}
    (root / "metadata.json").write_text(json.dumps(meta1), encoding="utf-8")
    (root / "model.joblib").write_bytes(b"model-v1")
    e1 = snapshot_current_version(
        str(root), strategy="ML_SIGNAL_BOOST", replace_current=False,
    )

    meta2 = {"trained_at": "2026-07-11T10:00:00Z", "model_type": "ml_signal_boost", "tag": "v2"}
    (root / "metadata.json").write_text(json.dumps(meta2), encoding="utf-8")
    (root / "model.joblib").write_bytes(b"model-v2")
    e2 = snapshot_current_version(
        str(root), strategy="ML_SIGNAL_BOOST", replace_current=False,
    )
    assert e1 and e2

    with patch(
        "app.services.bots.ml_model_artifacts.model_root_for",
        return_value=str(root),
    ):
        # Cannot delete active (v2)
        blocked = delete_model_version("ML_SIGNAL_BOOST", "SOLUSDT", meta2["trained_at"])
        assert blocked["ok"] is False
        assert "active" in blocked["error"].lower()

        # Delete older snapshot
        deleted = delete_model_version("ML_SIGNAL_BOOST", "SOLUSDT", meta1["trained_at"])
        assert deleted["ok"] is True
        assert deleted["deleted_version_id"] == e1["version_id"]
        assert not (root / "versions" / e1["version_id"]).exists()
        # Live root untouched
        assert (root / "model.joblib").read_bytes() == b"model-v2"
        ids = {v["version_id"] for v in list_model_versions(str(root))}
        assert e1["version_id"] not in ids
        assert e2["version_id"] in ids


def test_validation_summary_from_metadata_empty():
    empty = validation_summary_from_metadata(None)
    assert empty["validated_at"] is None
    assert empty["walk_forward"] is None
    assert empty["pbo"] is None


def test_validation_summary_from_metadata_full():
    summary = validation_summary_from_metadata({
        "validated_at": "2026-07-20T10:00:00Z",
        "walk_forward": {
            "ok": True,
            "mean_oos_accuracy": 0.61,
            "n_folds": 3,
            "successful_folds": 3,
            "recommendation": "deploy",
            "mode": "rolling",
        },
        "pbo": 0.25,
        "pbo_audit": {"recommendation": "low risk"},
    })
    assert summary["validated_at"] == "2026-07-20T10:00:00Z"
    assert summary["walk_forward"]["ok"] is True
    assert summary["walk_forward"]["mean_oos_accuracy"] == 0.61
    assert summary["walk_forward"]["n_folds"] == 3
    assert summary["pbo"]["pbo"] == 0.25
    assert summary["pbo"]["ok"] is True
    assert summary["pbo"]["skipped"] is False


def test_validation_summary_pbo_high_and_skipped():
    high = validation_summary_from_metadata({
        "validated_at": "2026-07-20T10:00:00Z",
        "walk_forward": {"ok": True},
        "pbo": 0.72,
    })
    assert high["pbo"]["ok"] is False

    skipped = validation_summary_from_metadata({
        "validated_at": "2026-07-20T10:00:00Z",
        "walk_forward": {"ok": True},
        "pbo": None,
        "pbo_audit": {"skipped": True, "error": "rl_too_expensive"},
    })
    assert skipped["pbo"]["skipped"] is True
    assert skipped["pbo"]["ok"] is False
    assert "rl_too_expensive" in (skipped["pbo"]["error"] or "")


def test_persist_validation_sidecar_survives_metadata_wipe(tmp_path, monkeypatch):
    """Activate/restore can wipe WF keys from metadata.json; sidecar must still apply."""
    from app.services.bots import ml_model_artifacts as arts

    root = tmp_path / "BNBUSDT__5M"
    root.mkdir()
    meta = {
        "trained_at": "2026-07-20T21:06:06Z",
        "version_id": "20260720T210606Z",
        "symbol": "BNBUSDT",
        "timeframe": "5m",
    }
    (root / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    (root / "model.joblib").write_bytes(b"fake")

    monkeypatch.setattr(
        arts,
        "model_root_for",
        lambda strategy, symbol, timeframe=None: str(root),
    )

    res = arts.persist_ml_validation_metadata(
        "ML_SIGNAL_BOOST",
        "BNBUSDT",
        {
            "ok": True,
            "mode": "rolling",
            "n_folds": 3,
            "successful_folds": 3,
            "recommendation": "DEPLOY_WITH_CAUTION — test",
            "aggregate": {"mean_oos_accuracy": 0.48},
            "validated_at": "2026-07-20T21:09:44Z",
        },
        pbo_result={"ok": True, "pbo": 0.25, "recommendation": "ok"},
        timeframe="5m",
    )
    assert res["ok"] is True
    assert (root / "validation.json").is_file()

    # Simulate Activate restoring a stamp-less version snapshot over live metadata.
    wiped = dict(meta)
    wiped["version_path"] = "versions/20260720T210606Z"
    (root / "metadata.json").write_text(json.dumps(wiped), encoding="utf-8")

    merged = arts.apply_validation_sidecar(wiped, str(root))
    assert merged.get("validated_at") == "2026-07-20T21:09:44Z"
    assert merged.get("walk_forward", {}).get("ok") is True
    assert merged.get("pbo") == 0.25

    # Retrain fingerprint mismatch invalidates sidecar.
    wiped2 = dict(wiped)
    wiped2["trained_at"] = "2026-07-20T22:00:00Z"
    (root / "metadata.json").write_text(json.dumps(wiped2), encoding="utf-8")
    assert arts.apply_validation_sidecar(wiped2, str(root)).get("walk_forward") is None

    arts.clear_ml_validation_stamp(str(root))
    assert not (root / "validation.json").is_file()


def test_protected_versions_skip_prune_and_block_delete(tmp_path):
    root = tmp_path / "ADAUSDT"
    root.mkdir()
    ids = []
    for i in range(4):
        meta = {"trained_at": f"2026-08-0{i + 1}T10:00:00Z", "model_type": "test"}
        (root / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        (root / "model.joblib").write_bytes(b"x")
        entry = snapshot_current_version(
            str(root), strategy="ML_SIGNAL_BOOST", max_kept=2, replace_current=False,
        )
        ids.append(entry["version_id"])

    # Protect the oldest remaining unprotected by renaming first snapshot we still have
    with patch(
        "app.services.bots.ml_model_artifacts.model_root_for",
        return_value=str(root),
    ):
        from app.services.bots.ml_model_artifacts import update_model_version_meta

        versions = list_model_versions(str(root))
        assert len(versions) <= 2
        keep_id = versions[-1]["version_id"]
        updated = update_model_version_meta(
            "ML_SIGNAL_BOOST",
            "ADAUSDT",
            keep_id,
            display_name="My best ADA",
            protected=True,
        )
        assert updated["ok"] is True
        assert updated["display_name"] == "My best ADA"
        assert updated["protected"] is True

        # Add more snapshots — protected must survive prune
        for i in range(4, 8):
            meta = {"trained_at": f"2026-08-1{i}T10:00:00Z", "model_type": "test"}
            (root / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
            (root / "model.joblib").write_bytes(b"x")
            snapshot_current_version(
                str(root), strategy="ML_SIGNAL_BOOST", max_kept=2, replace_current=False,
            )

        versions = list_model_versions(str(root))
        kept = [v for v in versions if v.get("version_id") == keep_id]
        assert len(kept) == 1
        assert kept[0].get("protected") is True
        assert kept[0].get("display_name") == "My best ADA"

        blocked = delete_model_version("ML_SIGNAL_BOOST", "ADAUSDT", keep_id)
        assert blocked["ok"] is False
        assert "protected" in blocked["error"].lower()


def test_merge_live_model_train_hyperparams(monkeypatch):
    from app.services.bots import optimization_store as store

    def fake_meta(strategy, symbol, timeframe=None):
        return {
            "config": {
                "gbm_max_depth": 7,
                "gbm_learning_rate": 0.05,
                "gbm_max_iter": 400,
            },
            "trained_at": "2026-08-01T00:00:00Z",
        }

    monkeypatch.setattr(
        "app.services.bots.ml_retrain_scheduler.get_model_metadata",
        fake_meta,
    )
    out = store.merge_live_model_train_hyperparams(
        {"timeframe": "1m", "champion_train": True},
        "BTCUSDT",
        "ML_SIGNAL_BOOST",
        timeframe="1m",
    )
    assert out["gbm_max_depth"] == 7
    assert out["gbm_learning_rate"] == 0.05
    assert out["retrain_from_live_model"] is True
    # Explicit client key wins
    out2 = store.merge_live_model_train_hyperparams(
        {"gbm_max_depth": 3},
        "BTCUSDT",
        "ML_SIGNAL_BOOST",
    )
    assert out2["gbm_max_depth"] == 3


def test_live_hyperparams_do_not_block_optuna_when_not_requested(monkeypatch):
    """Apply & Retrain: bare champion_train must not inject live HPs before Optuna."""
    from app.services.bots import optimization_store as store

    monkeypatch.setattr(
        "app.services.bots.ml_retrain_scheduler.get_model_metadata",
        lambda *a, **k: {"config": {"gbm_learning_rate": 0.3, "gbm_max_depth": 9}},
    )
    monkeypatch.setattr(
        store,
        "get_latest_optimized_hyperparams",
        lambda *a, **k: {"gbm_learning_rate": 0.02, "gbm_max_iter": 250},
    )
    # Mimic /ml/train gate: only merge live when flag is set.
    cfg = {"champion_train": True, "use_optimized_hyperparams": True}
    if cfg.get("retrain_from_live_model"):
        cfg = store.merge_live_model_train_hyperparams(cfg, "BTCUSDT", "ML_SIGNAL_BOOST")
    cfg = store.merge_optimized_train_hyperparams(
        cfg, "BTCUSDT", "ML_SIGNAL_BOOST", require_opt_in=True,
    )
    assert cfg["gbm_learning_rate"] == 0.02
    assert cfg["gbm_max_iter"] == 250
    assert "gbm_max_depth" not in cfg  # live must not have injected depth=9

    # Queue path: live first, then Optuna gap-fill only.
    q = {
        "champion_train": True,
        "retrain_from_live_model": True,
        "use_optimized_hyperparams": True,
    }
    q = store.merge_live_model_train_hyperparams(q, "BTCUSDT", "ML_SIGNAL_BOOST")
    q = store.merge_optimized_train_hyperparams(
        q, "BTCUSDT", "ML_SIGNAL_BOOST", require_opt_in=True,
    )
    assert q["gbm_learning_rate"] == 0.3  # live wins
    assert q["gbm_max_depth"] == 9
    assert q["gbm_max_iter"] == 250  # Optuna fills gap
