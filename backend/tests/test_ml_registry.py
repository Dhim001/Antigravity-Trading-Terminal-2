"""Tests for ml_registry SSOT and related structure cleanup."""

from __future__ import annotations

import json
import os

from app.services.bots.ml_model_artifacts import (
    champion_sync_info,
    migrate_bare_base_model_dirs,
    version_id_from_iso,
)
from app.services.bots.ml_registry import (
    ML_STRATEGIES,
    MODEL_SUBDIRS,
    MODEL_TYPE_LABELS,
    STRATEGY_ARTIFACTS,
    TRAINER_IMPORTS,
    get_trainer_import,
    is_ml_strategy,
    model_type_label,
    primary_artifact_name,
)


def test_registry_covers_all_lab_strategies():
    assert len(ML_STRATEGIES) == 7
    for strat in ML_STRATEGIES:
        assert strat in MODEL_SUBDIRS
        assert strat in STRATEGY_ARTIFACTS
        assert strat in TRAINER_IMPORTS
        assert get_trainer_import(strat) is not None
        assert primary_artifact_name(strat)
        assert model_type_label(strat)
    assert is_ml_strategy("ML_SIGNAL_BOOST")
    assert not is_ml_strategy("HYBRID_ENSEMBLE")
    assert MODEL_TYPE_LABELS["ML_SIGNAL_BOOST"] == "ml_signal_boost"


def test_version_id_from_iso_drops_fractional_seconds():
    assert version_id_from_iso("2026-08-04T06:55:56.292031Z") == "20260804T065556Z"
    assert version_id_from_iso("2026-08-04T21:33:34.298028Z") == "20260804T213334Z"
    assert version_id_from_iso("2026-08-04T06:55:56Z") == "20260804T065556Z"
    assert ":" not in version_id_from_iso("2026-07-15T22:30:00.123456Z")
    assert "-" not in version_id_from_iso("2026-07-15T22:30:00.123456Z")


def test_champion_sync_desynced(tmp_path):
    root = tmp_path / "BTCUSDT"
    root.mkdir()
    (root / "metadata.json").write_text(
        json.dumps({
            "trained_at": "2026-08-04T06:55:56.292031Z",
            "version_id": "20260804T0655562",
        }),
        encoding="utf-8",
    )
    vroot = root / "versions"
    vroot.mkdir()
    newest = vroot / "20260804T2133342"
    newest.mkdir()
    (newest / "metadata.json").write_text(
        json.dumps({"trained_at": "2026-08-04T21:33:34.298028Z"}),
        encoding="utf-8",
    )
    (vroot / "index.json").write_text(
        json.dumps([
            {
                "version_id": "20260804T2133342",
                "trained_at": "2026-08-04T21:33:34.298028Z",
                "path": "versions/20260804T2133342",
            },
            {
                "version_id": "20260804T0655562",
                "trained_at": "2026-08-04T06:55:56.292031Z",
                "path": "versions/20260804T0655562",
            },
        ]),
        encoding="utf-8",
    )
    info = champion_sync_info(str(root))
    assert info["desynced"] is True
    assert info["newest_version_id"] == "20260804T2133342"
    assert info["root_trained_at"] == "2026-08-04T06:55:56.292031Z"


def test_migrate_bare_btc_archives_when_usdt_exists(tmp_path):
    data = tmp_path / "data"
    sub = data / "ml_signal_models"
    (sub / "BTC").mkdir(parents=True)
    (sub / "BTC" / "metadata.json").write_text(
        json.dumps({"symbol": "BTC", "trained_at": "2026-07-15T00:00:00Z"}),
        encoding="utf-8",
    )
    (sub / "BTCUSDT").mkdir()
    (sub / "BTCUSDT" / "metadata.json").write_text(
        json.dumps({"symbol": "BTCUSDT"}),
        encoding="utf-8",
    )
    moved = migrate_bare_base_model_dirs(data_root=str(data), dry_run=False)
    assert len(moved) == 1
    assert moved[0]["action"] == "archive_orphan"
    assert not (sub / "BTC").exists()
    assert (data / "_orphans" / "ml_signal_models" / "BTC").is_dir()
    assert (sub / "BTCUSDT").is_dir()


def test_ensure_validation_sidecar_stamps_fingerprint(tmp_path):
    from app.services.bots.ml_model_artifacts import (
        apply_validation_sidecar,
        ensure_validation_sidecar,
        read_validation_sidecar,
    )

    root = tmp_path / "SYM"
    root.mkdir()
    (root / "metadata.json").write_text(
        json.dumps({
            "trained_at": "2026-08-04T06:55:56Z",
            "version_id": "20260804T065556Z",
            "validated_at": "2026-08-04T07:00:00Z",
            "walk_forward": {"ok": True, "aggregate": {"oos_accuracy": 0.6}},
        }),
        encoding="utf-8",
    )
    assert ensure_validation_sidecar(str(root)) is True
    side = read_validation_sidecar(str(root))
    assert side is not None
    assert side["trained_at"] == "2026-08-04T06:55:56Z"
    assert side["version_id"] == "20260804T065556Z"

    # Activate a different champion — fingerprint must reject old sidecar
    (root / "metadata.json").write_text(
        json.dumps({
            "trained_at": "2026-08-04T21:33:34Z",
            "version_id": "20260804T213334Z",
        }),
        encoding="utf-8",
    )
    assert read_validation_sidecar(str(root)) is None
    merged = apply_validation_sidecar({}, str(root))
    assert "walk_forward" not in merged


def test_legacy_version_dir_ambiguous_prefix_returns_none(tmp_path):
    from app.services.bots.ml_model_artifacts import (
        _legacy_version_dir,
        find_version_entry,
        version_id_from_iso,
    )

    root = tmp_path / "SYM"
    vroot = root / "versions"
    vroot.mkdir(parents=True)
    (vroot / "20260804T065556Z").mkdir()
    (vroot / "20260804T0655562").mkdir()
    vid = version_id_from_iso("2026-08-04T06:55:56.292031Z")
    # Exact clean id still resolves
    assert _legacy_version_dir(str(root), vid, vid).endswith("20260804T065556Z")
    # Ambiguous stem-only needle must not pick randomly
    assert _legacy_version_dir(str(root), "20260804T065556", "20260804T065556") is None

    # Index with both clean + mangled ids — prefix must not return first hit
    idx = [
        {"version_id": "20260804T065556Z", "trained_at": "2026-08-04T06:55:56Z", "path": "versions/20260804T065556Z"},
        {"version_id": "20260804T0655562", "trained_at": "2026-08-04T06:55:56.2Z", "path": "versions/20260804T0655562"},
    ]
    (vroot / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    assert find_version_entry(str(root), "20260804T065556Z")["version_id"] == "20260804T065556Z"
    assert find_version_entry(str(root), "20260804T065556") is None

def test_load_feature_importance_passes_timeframe(tmp_path, monkeypatch):
    from app.services.bots import backtest_category_metrics as bcm

    root = tmp_path / "ETHUSDT__15M"
    root.mkdir()
    (root / "metadata.json").write_text(
        json.dumps({
            "feature_importance": [{"name": "rsi", "importance": 0.5}],
        }),
        encoding="utf-8",
    )

    def fake_root(strategy, symbol, timeframe=None):
        assert timeframe == "15m"
        return str(root)

    monkeypatch.setattr(
        "app.services.bots.ml_model_artifacts.model_root_for",
        fake_root,
    )
    fi = bcm.load_ml_feature_importance("ML_SIGNAL_BOOST", "ETHUSDT", timeframe="15m")
    assert fi is not None
    assert fi[0]["name"] == "rsi"
