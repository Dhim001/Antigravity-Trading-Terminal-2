"""On-disk ML model versioning helpers.

Layout (per strategy subdir + symbol)::

    data/{subdir}/{SYMBOL}/                 # "current" / latest (back-compat)
      metadata.json
      *.onnx | model.joblib | scaler.json …
      versions/
        index.json                          # [{version_id, trained_at, …}]
        {version_id}/
          metadata.json
          …artifacts…

Training still writes to the current root first, then ``snapshot_current_version``
copies artifacts into ``versions/<id>/``. Inference loaders can resolve a pinned
``model_version`` (ISO ``trained_at``) via ``resolve_model_dir``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from typing import Any

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

MAX_KEPT_VERSIONS = 10

# Strategy → artifact filenames expected under model root
STRATEGY_ARTIFACTS: dict[str, list[str]] = {
    "ML_SIGNAL_BOOST": ["model.joblib", "metadata.json"],
    "LSTM_DIRECTION": ["lstm_direction.onnx", "scaler.json", "metadata.json"],
    "RL_PPO_AGENT": ["ppo_policy.onnx", "scaler.json", "metadata.json"],
    "TCN_MULTI_HORIZON": ["tcn_multi_horizon.onnx", "scaler.json", "metadata.json"],
    "VAE_REGIME_DETECTOR": ["vae_regime.onnx", "scaler.json", "metadata.json"],
    "TRANSFORMER_SIGNAL": ["transformer_signal.onnx", "scaler.json", "metadata.json"],
    "GNN_CROSS_ASSET": ["gnn_cross_asset.onnx", "scaler.json", "metadata.json"],
}

MODEL_SUBDIRS = {
    "ML_SIGNAL_BOOST": "ml_signal_models",
    "LSTM_DIRECTION": "lstm_signal_models",
    "RL_PPO_AGENT": "rl_ppo_models",
    "TCN_MULTI_HORIZON": "tcn_signal_models",
    "VAE_REGIME_DETECTOR": "vae_regime_models",
    "TRANSFORMER_SIGNAL": "transformer_signal_models",
    "GNN_CROSS_ASSET": "gnn_signal_models",
}


def safe_symbol_key(symbol: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(symbol or "").upper())


def version_id_from_iso(trained_at: str | None) -> str:
    """Normalize ISO timestamp into a filesystem-safe version id."""
    raw = (trained_at or "").strip()
    if not raw:
        raw = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    # 2026-07-15T22:30:00.123456Z → 20260715T223000Z
    cleaned = (
        raw.replace("+00:00", "Z")
        .replace("-", "")
        .replace(":", "")
        .replace(".", "")
    )
    if "T" in cleaned:
        date, _, rest = cleaned.partition("T")
        time_part = "".join(c for c in rest if c.isalnum())[:7]  # HHMMSSZ
        return f"{date}T{time_part}" if time_part else date
    return "".join(c for c in cleaned if c.isalnum())[:24] or "unknown"


def model_root_for(strategy: str, symbol: str) -> str | None:
    sub = MODEL_SUBDIRS.get(str(strategy or "").upper())
    if not sub:
        return None
    return os.path.join(BASE_DIR, "data", sub, safe_symbol_key(symbol))


def versions_dir(model_root: str) -> str:
    return os.path.join(model_root, "versions")


def version_index_path(model_root: str) -> str:
    return os.path.join(versions_dir(model_root), "index.json")


def _read_index(model_root: str) -> list[dict[str, Any]]:
    path = version_index_path(model_root)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_index(model_root: str, entries: list[dict[str, Any]]) -> None:
    os.makedirs(versions_dir(model_root), exist_ok=True)
    path = version_index_path(model_root)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def list_model_versions(model_root: str | None) -> list[dict[str, Any]]:
    """Return version index entries (newest first), with is_current flag."""
    if not model_root or not os.path.isdir(model_root):
        return []
    entries = _read_index(model_root)
    # Fallback: discover directories if index missing
    vroot = versions_dir(model_root)
    if not entries and os.path.isdir(vroot):
        for name in sorted(os.listdir(vroot), reverse=True):
            if name == "index.json":
                continue
            vdir = os.path.join(vroot, name)
            if not os.path.isdir(vdir):
                continue
            meta_path = os.path.join(vdir, "metadata.json")
            trained_at = None
            if os.path.isfile(meta_path):
                try:
                    with open(meta_path, encoding="utf-8") as f:
                        trained_at = json.load(f).get("trained_at")
                except Exception:
                    pass
            entries.append({
                "version_id": name,
                "trained_at": trained_at,
                "path": f"versions/{name}",
            })
        if entries:
            _write_index(model_root, entries)

    current_meta = os.path.join(model_root, "metadata.json")
    current_at = None
    if os.path.isfile(current_meta):
        try:
            with open(current_meta, encoding="utf-8") as f:
                current_at = json.load(f).get("trained_at")
        except Exception:
            pass

    out = []
    for e in entries:
        row = dict(e)
        row["is_current"] = bool(
            current_at and row.get("trained_at") and str(row["trained_at"]) == str(current_at)
        )
        out.append(row)
    out.sort(key=lambda r: str(r.get("trained_at") or r.get("version_id") or ""), reverse=True)
    return out


def resolve_model_dir(model_root: str, model_version: str | None = None) -> str:
    """Return directory to load artifacts from (versioned or current)."""
    if not model_root:
        return model_root
    if not model_version:
        return model_root
    vid = version_id_from_iso(str(model_version))
    # Exact dir
    candidate = os.path.join(versions_dir(model_root), vid)
    if os.path.isdir(candidate):
        return candidate
    # Match by trained_at in index
    for entry in list_model_versions(model_root):
        if str(entry.get("trained_at")) == str(model_version) or entry.get("version_id") == vid:
            path = os.path.join(model_root, entry.get("path") or f"versions/{entry['version_id']}")
            if os.path.isdir(path):
                return path
    # Fallback: current
    logger.warning(
        "Pinned model_version %s not found under %s — using current",
        model_version,
        model_root,
    )
    return model_root


def find_version_entry(model_root: str, model_version: str | None) -> dict[str, Any] | None:
    """Locate a version index row by trained_at or version_id."""
    if not model_root or not model_version:
        return None
    needle = str(model_version).strip()
    vid = version_id_from_iso(needle)
    for entry in list_model_versions(model_root):
        if str(entry.get("trained_at") or "") == needle or str(entry.get("version_id") or "") in (needle, vid):
            return entry
    # Direct directory even if index is stale
    candidate = os.path.join(versions_dir(model_root), vid)
    if os.path.isdir(candidate):
        meta_path = os.path.join(candidate, "metadata.json")
        trained_at = None
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    trained_at = json.load(f).get("trained_at")
            except Exception:
                pass
        return {
            "version_id": vid,
            "trained_at": trained_at or needle,
            "path": f"versions/{vid}",
        }
    return None


def activate_model_version(
    strategy: str,
    symbol: str,
    model_version: str,
) -> dict[str, Any]:
    """
    Promote a historical snapshot to the live model root (current).

    Copies artifacts from ``versions/<id>/`` over the current root so unpinned
    bots / backtests load that checkpoint. Does not delete other versions.
    """
    strat = str(strategy or "").upper()
    sym = str(symbol or "").upper()
    root = model_root_for(strat, sym)
    if not root or not os.path.isdir(root):
        return {"ok": False, "error": f"No model directory for {strat}/{sym}"}

    entry = find_version_entry(root, model_version)
    if not entry:
        return {"ok": False, "error": f"Version not found: {model_version}"}

    src_dir = os.path.join(root, entry.get("path") or f"versions/{entry['version_id']}")
    if not os.path.isdir(src_dir):
        return {"ok": False, "error": f"Version directory missing: {entry.get('version_id')}"}

    copied: list[str] = []
    for fname in os.listdir(src_dir):
        src = os.path.join(src_dir, fname)
        if not os.path.isfile(src):
            continue
        if fname == "versions":
            continue
        dst = os.path.join(root, fname)
        shutil.copy2(src, dst)
        copied.append(fname)

    if "metadata.json" not in copied:
        return {"ok": False, "error": "Version has no metadata.json"}

    # Ensure current metadata reflects this version id
    meta_path = os.path.join(root, "metadata.json")
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    meta["version_id"] = entry.get("version_id")
    if entry.get("trained_at") and not meta.get("trained_at"):
        meta["trained_at"] = entry["trained_at"]
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    # Keep index entry; refresh is_current via list_model_versions
    logger.info(
        "Activated model version %s for %s/%s (%d files)",
        entry.get("version_id"),
        strat,
        sym,
        len(copied),
    )
    return {
        "ok": True,
        "strategy": strat,
        "symbol": sym,
        "version_id": entry.get("version_id"),
        "trained_at": meta.get("trained_at") or entry.get("trained_at"),
        "model_version": meta.get("trained_at") or entry.get("trained_at"),
        "artifacts": copied,
        "versions": list_model_versions(root),
    }


def delete_model_version(
    strategy: str,
    symbol: str,
    model_version: str,
) -> dict[str, Any]:
    """
    Remove a historical snapshot from disk and the version index.

    Refuses to delete the currently active version — activate another checkpoint
    first. Never deletes the live model root files.
    """
    strat = str(strategy or "").upper()
    sym = str(symbol or "").upper()
    root = model_root_for(strat, sym)
    if not root or not os.path.isdir(root):
        return {"ok": False, "error": f"No model directory for {strat}/{sym}"}

    entry = find_version_entry(root, model_version)
    if not entry:
        return {"ok": False, "error": f"Version not found: {model_version}"}

    vid = str(entry.get("version_id") or "")
    for row in list_model_versions(root):
        if row.get("is_current") and (
            str(row.get("version_id") or "") == vid
            or str(row.get("trained_at") or "") == str(entry.get("trained_at") or "")
        ):
            return {
                "ok": False,
                "error": (
                    "Cannot delete the active version. "
                    "Activate another version first, then delete this one."
                ),
            }

    rel = entry.get("path") or (f"versions/{vid}" if vid else None)
    if not rel:
        return {"ok": False, "error": "Version path missing"}
    vdir = os.path.join(root, rel)
    # Safety: only delete under versions/
    versions_root = os.path.realpath(versions_dir(root))
    real_vdir = os.path.realpath(vdir)
    if not real_vdir.startswith(versions_root + os.sep) and real_vdir != versions_root:
        return {"ok": False, "error": "Refusing to delete path outside versions/"}

    if os.path.isdir(vdir):
        shutil.rmtree(vdir)
    elif not os.path.exists(vdir):
        # Still drop stale index row
        pass
    else:
        return {"ok": False, "error": f"Not a version directory: {vid}"}

    entries = _read_index(root)
    trained_at = entry.get("trained_at")
    entries = [
        e
        for e in entries
        if str(e.get("version_id") or "") != vid
        and (not trained_at or str(e.get("trained_at") or "") != str(trained_at))
    ]
    _write_index(root, entries)

    logger.info("Deleted model version %s for %s/%s", vid, strat, sym)
    return {
        "ok": True,
        "strategy": strat,
        "symbol": sym,
        "deleted_version_id": vid,
        "deleted_trained_at": trained_at,
        "versions": list_model_versions(root),
    }


def invalidate_strategy_model_caches(strategy: str, symbol: str | None = None) -> None:
    """Drop in-memory ONNX/joblib caches after activate/retrain."""
    strat = str(strategy or "").upper()
    sym = (symbol or "").upper() or None
    try:
        if strat == "ML_SIGNAL_BOOST":
            from app.services.bots.strategies_ml import get_ml_signal_store
            get_ml_signal_store().invalidate(sym)
        elif strat == "LSTM_DIRECTION":
            from app.services.bots.strategies_lstm import get_lstm_store
            get_lstm_store().invalidate(sym)
        elif strat == "RL_PPO_AGENT":
            from app.services.bots.rl_ppo_trainer import get_ppo_store
            get_ppo_store().invalidate(sym)
        elif strat == "TCN_MULTI_HORIZON":
            from app.services.bots.ml_tcn_trainer import get_tcn_store
            get_tcn_store().invalidate(sym)
        elif strat == "VAE_REGIME_DETECTOR":
            from app.services.bots.ml_vae_regime import get_vae_store
            get_vae_store().invalidate(sym)
        elif strat == "TRANSFORMER_SIGNAL":
            from app.services.bots.ml_transformer_trainer import get_transformer_store
            get_transformer_store().invalidate(sym)
        elif strat == "GNN_CROSS_ASSET":
            from app.services.bots.ml_gnn_trainer import get_gnn_store
            get_gnn_store().invalidate(sym)
    except Exception as exc:
        logger.warning("Failed to invalidate model cache for %s/%s: %s", strat, sym, exc)


def snapshot_current_version(
    model_root: str,
    *,
    strategy: str | None = None,
    artifact_names: list[str] | None = None,
    max_kept: int = MAX_KEPT_VERSIONS,
) -> dict[str, Any] | None:
    """
    Copy current-root artifacts into versions/<id>/ and update index.json.

    Call after training has written metadata.json (+ model files) to model_root.
    """
    if not model_root or not os.path.isdir(model_root):
        return None
    meta_path = os.path.join(model_root, "metadata.json")
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        return None

    trained_at = meta.get("trained_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    vid = version_id_from_iso(trained_at)
    vdir = os.path.join(versions_dir(model_root), vid)
    os.makedirs(vdir, exist_ok=True)

    names = list(artifact_names or [])
    if not names and strategy:
        names = list(STRATEGY_ARTIFACTS.get(str(strategy).upper()) or ["metadata.json"])
    if "metadata.json" not in names:
        names.append("metadata.json")

    # Also pick up any loose scaler/joblib/onnx in root if not listed
    for fname in os.listdir(model_root):
        if fname in ("versions",):
            continue
        fpath = os.path.join(model_root, fname)
        if os.path.isfile(fpath) and (
            fname.endswith((".onnx", ".joblib", ".json")) or fname in names
        ):
            if fname not in names:
                names.append(fname)

    copied = []
    for fname in names:
        src = os.path.join(model_root, fname)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(vdir, fname)
        shutil.copy2(src, dst)
        copied.append(fname)

    # Enrich version metadata copy
    version_meta = dict(meta)
    version_meta["version_id"] = vid
    version_meta["version_path"] = f"versions/{vid}"
    with open(os.path.join(vdir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(version_meta, f, indent=2)

    entry = {
        "version_id": vid,
        "trained_at": trained_at,
        "path": f"versions/{vid}",
        "artifacts": copied,
        "metrics": meta.get("metrics") if isinstance(meta.get("metrics"), dict) else {},
        "sample_count": meta.get("sample_count"),
        "label_distribution": meta.get("label_distribution"),
        "feature_names": meta.get("feature_names"),
        "model_type": meta.get("model_type"),
    }

    index = [e for e in _read_index(model_root) if e.get("version_id") != vid]
    index.insert(0, entry)

    # Prune old versions
    while len(index) > max_kept:
        old = index.pop()
        old_dir = os.path.join(model_root, old.get("path") or f"versions/{old.get('version_id')}")
        if os.path.isdir(old_dir):
            try:
                shutil.rmtree(old_dir)
            except OSError as exc:
                logger.warning("Failed to prune model version %s: %s", old_dir, exc)

    _write_index(model_root, index)

    # Stamp current metadata with version_id
    meta["version_id"] = vid
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    logger.info("Snapshotted model version %s under %s (%d files)", vid, model_root, len(copied))
    return entry


def dataset_summary_from_metadata(meta: dict | None) -> dict[str, Any] | None:
    """Compact dataset browser payload from metadata.json."""
    if not isinstance(meta, dict):
        return None
    metrics = meta.get("metrics") if isinstance(meta.get("metrics"), dict) else {}
    return {
        "sample_count": meta.get("sample_count") or metrics.get("train_samples"),
        "train_samples": metrics.get("train_samples"),
        "val_samples": metrics.get("val_samples"),
        "label_distribution": meta.get("label_distribution"),
        "feature_names": meta.get("feature_names"),
        "feature_schema_version": meta.get("feature_schema_version"),
        "top_features": meta.get("top_features"),
        "horizons": meta.get("horizons"),
        "lookback": (meta.get("config") or {}).get("lookback") if isinstance(meta.get("config"), dict) else meta.get("lookback"),
        "model_type": meta.get("model_type"),
        "trained_at": meta.get("trained_at"),
        "version_id": meta.get("version_id"),
        "config": meta.get("config") if isinstance(meta.get("config"), dict) else None,
    }


def unlink_quiet(path: str | None) -> None:
    """Best-effort file delete (Windows mmap / race tolerant)."""
    if not path:
        return
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def export_onnx_single_file(
    model: Any,
    args: Any,
    dest_path: str,
    *,
    input_names: list[str],
    output_names: list[str],
    dynamic_axes: dict | None = None,
    opset_version: int = 17,
    invalidate: Any | None = None,
) -> str:
    """Export a Torch module to a single-file ONNX (no ``*.onnx.data`` sidecar).

    Torch 2.9+ may emit external weight files. On Windows those sidecars stay
    memory-mapped by onnxruntime after OOS eval, so the next walk-forward fold
    fails with ``[Errno 22] Invalid argument`` when overwriting the live path.

    Strategy:
      1. Drop ORT sessions via ``invalidate()`` + ``gc.collect()``
      2. Unlink existing ``.onnx`` / ``.onnx.data``
      3. Export to a temp file with ``external_data=False`` when supported
      4. If a sidecar still appears, re-save via onnx as a single file
      5. Atomically replace into ``dest_path``
    """
    import gc
    import tempfile
    import uuid
    import warnings

    if invalidate is not None:
        try:
            invalidate()
        except Exception:
            logger.debug("ONNX export invalidate callback failed", exc_info=True)
    gc.collect()

    dest = os.path.abspath(dest_path)
    dest_data = dest + ".data"
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    unlink_quiet(dest_data)
    unlink_quiet(dest)

    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("PyTorch required for ONNX export") from exc

    export_kwargs: dict[str, Any] = {
        "input_names": list(input_names),
        "output_names": list(output_names),
        "opset_version": int(opset_version),
    }
    if dynamic_axes:
        export_kwargs["dynamic_axes"] = dynamic_axes

    tmp_dir = tempfile.mkdtemp(prefix="ml_onnx_")
    tmp_path = os.path.join(tmp_dir, f"model_{uuid.uuid4().hex}.onnx")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                torch.onnx.export(
                    model,
                    args,
                    tmp_path,
                    dynamo=False,
                    external_data=False,
                    **export_kwargs,
                )
            except TypeError:
                # Older torch: no dynamo / external_data kwargs.
                torch.onnx.export(model, args, tmp_path, **export_kwargs)

        tmp_data = tmp_path + ".data"
        if os.path.isfile(tmp_data):
            try:
                import onnx

                onnx_model = onnx.load(tmp_path, load_external_data=True)
                onnx.save_model(onnx_model, dest, save_as_external_data=False)
            finally:
                unlink_quiet(tmp_data)
                unlink_quiet(tmp_path)
        else:
            os.replace(tmp_path, dest)

        unlink_quiet(dest_data)
        return dest
    finally:
        unlink_quiet(tmp_path)
        unlink_quiet(tmp_path + ".data")
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass

