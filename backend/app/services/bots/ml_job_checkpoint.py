"""Durable checkpoints for ML train / Auto-Tune / walk-forward jobs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

CHECKPOINT_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def optuna_study_path(job_id: str) -> str:
    from app.config import DATA_DIR

    root = os.path.join(DATA_DIR, "optuna")
    os.makedirs(root, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(job_id or "job"))
    return os.path.join(root, f"{safe}.db")


def deep_checkpoint_dir(job_id: str) -> str:
    from app.config import DATA_DIR

    root = os.path.join(DATA_DIR, "ml_checkpoints", str(job_id or "job"))
    os.makedirs(root, exist_ok=True)
    return root


def empty_hyperparam_checkpoint(
    *,
    job_id: str | None,
    strategy: str,
    symbol: str,
    config: dict | None,
    max_trials: int,
) -> dict[str, Any]:
    cfg = dict(config or {})
    return {
        "version": CHECKPOINT_VERSION,
        "kind": "hyperparam_sweep",
        "job_id": job_id,
        "strategy": str(strategy or "").upper(),
        "symbol": str(symbol or "").upper(),
        "config": cfg,
        "max_trials": int(max_trials or 0),
        "trials_completed": 0,
        "trial_history": [],
        "best_hyperparams": None,
        "best_score": None,
        "study_path": optuna_study_path(job_id) if job_id else None,
        "updated_at": _now_iso(),
        "resume_ok": True,
    }


def empty_wf_checkpoint(
    *,
    job_id: str | None,
    strategy: str,
    symbol: str,
    config: dict | None,
    n_folds: int,
) -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "kind": "walk_forward",
        "job_id": job_id,
        "strategy": str(strategy or "").upper(),
        "symbol": str(symbol or "").upper(),
        "config": dict(config or {}),
        "n_folds": int(n_folds or 0),
        "completed_fold_indices": [],
        "fold_results": [],
        "updated_at": _now_iso(),
        "resume_ok": True,
    }


def empty_epoch_checkpoint(
    *,
    job_id: str | None,
    strategy: str,
    symbol: str,
    epochs_budget: int,
) -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "kind": "epoch",
        "job_id": job_id,
        "strategy": str(strategy or "").upper(),
        "symbol": str(symbol or "").upper(),
        "last_epoch": 0,
        "epochs_budget": int(epochs_budget or 0),
        "checkpoint_dir": deep_checkpoint_dir(job_id) if job_id else None,
        "updated_at": _now_iso(),
        "resume_ok": True,
    }


def checkpoint_resume_ok(checkpoint: dict | None) -> bool:
    if not isinstance(checkpoint, dict):
        return False
    if int(checkpoint.get("version") or 0) != CHECKPOINT_VERSION:
        return False
    return bool(checkpoint.get("resume_ok", True))


def merge_hyperparam_trial(
    checkpoint: dict | None,
    *,
    job_id: str | None,
    strategy: str,
    symbol: str,
    config: dict | None,
    max_trials: int,
    trial_row: dict | None,
    best_hyperparams: dict | None = None,
    best_score: float | None = None,
    study_path: str | None = None,
) -> dict[str, Any]:
    base = (
        dict(checkpoint)
        if isinstance(checkpoint, dict) and checkpoint.get("kind") == "hyperparam_sweep"
        else empty_hyperparam_checkpoint(
            job_id=job_id, strategy=strategy, symbol=symbol, config=config, max_trials=max_trials,
        )
    )
    history = list(base.get("trial_history") or [])
    if trial_row is not None:
        history.append(trial_row)
    base.update({
        "kind": "hyperparam_sweep",
        "job_id": job_id or base.get("job_id"),
        "strategy": str(strategy or base.get("strategy") or "").upper(),
        "symbol": str(symbol or base.get("symbol") or "").upper(),
        "config": dict(config or base.get("config") or {}),
        "max_trials": int(max_trials or base.get("max_trials") or 0),
        "trials_completed": len(history),
        "trial_history": history,
        "updated_at": _now_iso(),
        "resume_ok": True,
    })
    if best_hyperparams is not None:
        base["best_hyperparams"] = best_hyperparams
    if best_score is not None:
        base["best_score"] = best_score
    if study_path:
        base["study_path"] = study_path
    elif job_id and not base.get("study_path"):
        base["study_path"] = optuna_study_path(job_id)
    return base


def merge_wf_fold(
    checkpoint: dict | None,
    *,
    job_id: str | None,
    strategy: str,
    symbol: str,
    config: dict | None,
    n_folds: int,
    fold_idx: int,
    fold_entry: dict | None,
) -> dict[str, Any]:
    base = (
        dict(checkpoint)
        if isinstance(checkpoint, dict) and checkpoint.get("kind") == "walk_forward"
        else empty_wf_checkpoint(
            job_id=job_id, strategy=strategy, symbol=symbol, config=config, n_folds=n_folds,
        )
    )
    indices = list(base.get("completed_fold_indices") or [])
    folds = list(base.get("fold_results") or [])
    if fold_idx not in indices:
        indices.append(fold_idx)
    if fold_entry is not None:
        fold_num = fold_entry.get("fold")
        folds = [f for f in folds if f.get("fold") != fold_num]
        folds.append(fold_entry)
    base.update({
        "kind": "walk_forward",
        "completed_fold_indices": indices,
        "fold_results": folds,
        "n_folds": int(n_folds or base.get("n_folds") or 0),
        "updated_at": _now_iso(),
        "resume_ok": True,
    })
    return base


def completed_fold_indices(checkpoint: dict | None) -> set[int]:
    if not isinstance(checkpoint, dict):
        return set()
    out: set[int] = set()
    for raw in checkpoint.get("completed_fold_indices") or []:
        try:
            out.add(int(raw))
        except (TypeError, ValueError):
            continue
    return out


def save_torch_epoch_checkpoint(
    job_id: str | None,
    *,
    model_state: dict,
    epoch: int,
    epochs_budget: int,
    extra: dict | None = None,
) -> str | None:
    """Persist last completed epoch weights under data/ml_checkpoints/{job_id}/."""
    if not job_id:
        return None
    try:
        import torch
    except ImportError:
        return None
    root = deep_checkpoint_dir(job_id)
    path = os.path.join(root, "last_epoch.pt")
    payload = {
        "epoch": int(epoch),
        "epochs_budget": int(epochs_budget),
        "model_state": model_state,
        "extra": dict(extra or {}),
        "updated_at": _now_iso(),
    }
    torch.save(payload, path)
    meta_path = os.path.join(root, "epoch_meta.json")
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump({
            "epoch": int(epoch),
            "epochs_budget": int(epochs_budget),
            "path": path,
            "updated_at": _now_iso(),
            "resume_ok": True,
        }, fh)
    return path


def load_torch_epoch_checkpoint(job_id: str | None) -> dict[str, Any] | None:
    if not job_id:
        return None
    try:
        import torch
    except ImportError:
        return None
    path = os.path.join(deep_checkpoint_dir(job_id), "last_epoch.pt")
    if not os.path.isfile(path):
        return None
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")
    except Exception:
        return None
