"""Discrete checkpoints for deferred backtest / optimizer jobs.

Persists after each sweep combo or walk-forward fold so a restart can skip
completed units instead of re-running from bar 0.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


CHECKPOINT_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def request_fingerprint(request: dict | None) -> str:
    """Stable hash of the job request used to validate resume compatibility."""
    payload = request or {}
    # Exclude volatile server-only keys.
    skip = {"tier", "estimated_sec", "client_key"}
    cleaned = {k: v for k, v in sorted(payload.items()) if k not in skip}
    raw = json.dumps(cleaned, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def config_label(config: dict | None, *, fallback_index: int = 0) -> str:
    """Stable label for a sweep config row (prefer sweep_label fields)."""
    cfg = config or {}
    try:
        from app.services.bots.backtest_sweep import sweep_label

        label = sweep_label(cfg)
        if label:
            return str(label)
    except Exception:
        pass
    raw = json.dumps(cfg, sort_keys=True, default=str, separators=(",", ":"))
    return f"idx:{fallback_index}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"


def empty_sweep_checkpoint(request: dict | None) -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "kind": "sweep",
        "request_fp": request_fingerprint(request),
        "completed_labels": [],
        "completed_indices": [],
        "sweep_rows": [],
        "best_config": None,
        "best_summary": None,
        "updated_at": _now_iso(),
        "resume_ok": True,
    }


def empty_walk_forward_checkpoint(request: dict | None) -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "kind": "walk_forward",
        "request_fp": request_fingerprint(request),
        "completed_fold_indices": [],
        "fold_results": [],
        "updated_at": _now_iso(),
        "resume_ok": True,
    }


def checkpoint_compatible(checkpoint: dict | None, request: dict | None) -> bool:
    if not isinstance(checkpoint, dict):
        return False
    if int(checkpoint.get("version") or 0) != CHECKPOINT_VERSION:
        return False
    if not checkpoint.get("resume_ok", True):
        return False
    fp = checkpoint.get("request_fp")
    if not fp:
        return False
    return fp == request_fingerprint(request)


def merge_sweep_progress(
    checkpoint: dict | None,
    *,
    request: dict | None,
    run_idx: int,
    label: str,
    row: dict | None,
    best_config: dict | None = None,
    best_summary: dict | None = None,
) -> dict[str, Any]:
    base = dict(checkpoint) if isinstance(checkpoint, dict) else empty_sweep_checkpoint(request)
    if not checkpoint_compatible(base, request):
        base = empty_sweep_checkpoint(request)
    labels = list(base.get("completed_labels") or [])
    indices = list(base.get("completed_indices") or [])
    rows = list(base.get("sweep_rows") or [])
    if label not in labels:
        labels.append(label)
    if run_idx not in indices:
        indices.append(run_idx)
    if row is not None:
        rows.append(row)
    base.update({
        "kind": "sweep",
        "completed_labels": labels,
        "completed_indices": indices,
        "sweep_rows": rows,
        "next_index": max(indices) + 1 if indices else run_idx + 1,
        "updated_at": _now_iso(),
        "resume_ok": True,
    })
    if best_config is not None:
        base["best_config"] = best_config
    if best_summary is not None:
        base["best_summary"] = best_summary
    return base


def completed_index_set(checkpoint: dict | None) -> set[int]:
    if not isinstance(checkpoint, dict):
        return set()
    out: set[int] = set()
    for raw in checkpoint.get("completed_indices") or []:
        try:
            out.add(int(raw))
        except (TypeError, ValueError):
            continue
    return out


def completed_label_set(checkpoint: dict | None) -> set[str]:
    if not isinstance(checkpoint, dict):
        return set()
    return {str(x) for x in (checkpoint.get("completed_labels") or []) if x is not None}


def completed_fold_index_set(checkpoint: dict | None) -> set[int]:
    if not isinstance(checkpoint, dict):
        return set()
    out: set[int] = set()
    for raw in checkpoint.get("completed_fold_indices") or []:
        try:
            out.add(int(raw))
        except (TypeError, ValueError):
            continue
    return out


def merge_walk_forward_progress(
    checkpoint: dict | None,
    *,
    request: dict | None,
    fold_idx: int,
    fold_entry: dict | None,
) -> dict[str, Any]:
    """Append a finished walk-forward fold to the durable checkpoint."""
    base = (
        dict(checkpoint)
        if isinstance(checkpoint, dict)
        else empty_walk_forward_checkpoint(request)
    )
    if not checkpoint_compatible(base, request) or base.get("kind") != "walk_forward":
        base = empty_walk_forward_checkpoint(request)
    indices = list(base.get("completed_fold_indices") or [])
    folds = list(base.get("fold_results") or [])
    if fold_idx not in indices:
        indices.append(fold_idx)
    if fold_entry is not None:
        # Replace prior entry for the same fold number when re-running.
        fold_num = fold_entry.get("fold")
        folds = [f for f in folds if f.get("fold") != fold_num]
        folds.append(fold_entry)
    base.update({
        "kind": "walk_forward",
        "completed_fold_indices": indices,
        "fold_results": folds,
        "next_fold": max(indices) + 1 if indices else fold_idx + 1,
        "updated_at": _now_iso(),
        "resume_ok": True,
    })
    return base


def is_resumable_job(job: dict | None) -> bool:
    """True when Jobs UI should offer Resume."""
    if not isinstance(job, dict):
        return False
    status = str(job.get("status") or "")
    if status not in ("failed", "cancelled", "pending"):
        return False
    cp = job.get("checkpoint")
    if not isinstance(cp, dict):
        return False
    if not cp.get("resume_ok", True):
        return False
    req = job.get("request") or {}
    return checkpoint_compatible(cp, req)
