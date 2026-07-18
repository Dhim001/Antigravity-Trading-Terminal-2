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
    version_id_from_iso,
)


def test_version_id_from_iso_normalizes():
    vid = version_id_from_iso("2026-07-15T22:30:00.123456Z")
    assert vid.startswith("20260715T")
    assert ":" not in vid
    assert "-" not in vid


def test_snapshot_and_resolve(tmp_path):
    root = tmp_path / "BTCUSDT"
    root.mkdir()
    meta = {
        "trained_at": "2026-07-15T12:00:00Z",
        "model_type": "xgboost",
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
        snapshot_current_version(str(root), strategy="ML_SIGNAL_BOOST", max_kept=2)

    versions = list_model_versions(str(root))
    assert len(versions) <= 2


def test_activate_model_version_promotes_snapshot(tmp_path):
    root = tmp_path / "BNBUSDT"
    root.mkdir()
    # v1 current
    meta1 = {"trained_at": "2026-07-10T10:00:00Z", "model_type": "xgboost", "tag": "v1"}
    (root / "metadata.json").write_text(json.dumps(meta1), encoding="utf-8")
    (root / "model.joblib").write_bytes(b"model-v1")
    e1 = snapshot_current_version(str(root), strategy="ML_SIGNAL_BOOST")
    assert e1 is not None

    # v2 current
    meta2 = {"trained_at": "2026-07-11T10:00:00Z", "model_type": "xgboost", "tag": "v2"}
    (root / "metadata.json").write_text(json.dumps(meta2), encoding="utf-8")
    (root / "model.joblib").write_bytes(b"model-v2")
    e2 = snapshot_current_version(str(root), strategy="ML_SIGNAL_BOOST")
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
    meta1 = {"trained_at": "2026-07-10T10:00:00Z", "model_type": "xgboost", "tag": "v1"}
    (root / "metadata.json").write_text(json.dumps(meta1), encoding="utf-8")
    (root / "model.joblib").write_bytes(b"model-v1")
    e1 = snapshot_current_version(str(root), strategy="ML_SIGNAL_BOOST")

    meta2 = {"trained_at": "2026-07-11T10:00:00Z", "model_type": "xgboost", "tag": "v2"}
    (root / "metadata.json").write_text(json.dumps(meta2), encoding="utf-8")
    (root / "model.joblib").write_bytes(b"model-v2")
    e2 = snapshot_current_version(str(root), strategy="ML_SIGNAL_BOOST")
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
