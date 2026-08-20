"""Cross-asset model transfer — donor resolution, compatibility, lineage.

A *donor* is a trained model version for the same strategy on a different
symbol. The target asset's training job warm-starts from the donor's
trainable checkpoint (``.pt``) instead of random init. The transferred model
registers as a challenger and must still pass the existing walk-forward /
PBO / deploy gates — those gates are the negative-transfer arbiter.

Checkpoint sidecar filenames per strategy (optional; old versions without
them simply cannot serve as weight donors):

- RL_PPO_AGENT      → ``policy.pt``       (actor-critic state_dict)
- LSTM_DIRECTION    → ``lstm_direction.pt`` (already persisted)
- TCN_MULTI_HORIZON → ``checkpoint.pt``
- TRANSFORMER_SIGNAL→ ``checkpoint.pt``
- GNN_CROSS_ASSET   → ``checkpoint.pt``
- VAE_REGIME_DETECTOR → ``checkpoint.pt`` (encoder transfer only)
- ML_SIGNAL_BOOST   → no weights; recipe transfer via donor metadata
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# strategy → trainable checkpoint sidecar filename (None = recipe-only)
TRANSFER_CHECKPOINT_FILES: dict[str, str | None] = {
    "RL_PPO_AGENT": "policy.pt",
    "LSTM_DIRECTION": "lstm_direction.pt",
    "TCN_MULTI_HORIZON": "checkpoint.pt",
    "TRANSFORMER_SIGNAL": "checkpoint.pt",
    "GNN_CROSS_ASSET": "checkpoint.pt",
    "VAE_REGIME_DETECTOR": "checkpoint.pt",
    "ML_SIGNAL_BOOST": None,
}

SCALER_STRATEGIES = ("recompute", "carry")
DEFAULT_SCALER_STRATEGY = "recompute"

# Transfer methods recorded in lineage metadata.
METHOD_WEIGHT_WARM_START = "weight_warm_start"
METHOD_RECIPE = "recipe_transfer"


def transfer_enabled() -> bool:
    try:
        from app.config import MODEL_TRANSFER_ENABLED

        return bool(MODEL_TRANSFER_ENABLED)
    except Exception:
        return False


def _read_metadata(model_dir: str) -> dict[str, Any] | None:
    path = os.path.join(model_dir, "metadata.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        logger.debug("Failed reading donor metadata at %s", path, exc_info=True)
        return None


def resolve_donor(
    strategy: str,
    donor_symbol: str,
    timeframe: str | None,
    donor_version: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a donor model directory + metadata for ``strategy``.

    Returns ``{dir, metadata, symbol, version_id}`` or None when the donor
    does not exist. ``donor_version`` may be a version_id or trained_at; when
    omitted the current champion directory is used.
    """
    from app.services.bots.ml_model_artifacts import (
        list_model_versions,
        model_root_for,
        resolve_model_dir,
    )

    strat = str(strategy or "").upper()
    root = model_root_for(strat, donor_symbol, timeframe)
    if not root or not os.path.isdir(root):
        return None
    model_dir = resolve_model_dir(root, donor_version) if donor_version else root
    if not model_dir or not os.path.isdir(model_dir):
        return None
    meta = _read_metadata(model_dir)
    if meta is None:
        return None
    version_id = str(meta.get("version_id") or "").strip() or None
    if not version_id and donor_version:
        version_id = str(donor_version)
    if not version_id:
        for entry in list_model_versions(root):
            if entry.get("is_current"):
                version_id = str(entry.get("version_id") or "") or None
                break
    return {
        "dir": model_dir,
        "metadata": meta,
        "symbol": str(meta.get("symbol") or donor_symbol).upper(),
        "version_id": version_id,
    }


def donor_checkpoint_path(donor: dict[str, Any], strategy: str) -> str | None:
    """Absolute path to the donor's trainable checkpoint, if one exists."""
    fname = TRANSFER_CHECKPOINT_FILES.get(str(strategy or "").upper())
    if not fname:
        return None
    path = os.path.join(str(donor.get("dir") or ""), fname)
    return path if os.path.isfile(path) else None


def donor_scaler(donor: dict[str, Any]) -> dict[str, Any] | None:
    """Load the donor's scaler.json (for the ``carry`` scaler strategy)."""
    path = os.path.join(str(donor.get("dir") or ""), "scaler.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def check_compatibility(
    donor_meta: dict[str, Any],
    target_strategy: str,
    timeframe: str | None,
) -> list[str]:
    """Hard requirements for a donor→target transfer. Returns error strings."""
    errors: list[str] = []
    strat = str(target_strategy or "").upper()
    if not isinstance(donor_meta, dict):
        return ["donor metadata missing"]

    donor_type = str(donor_meta.get("model_type") or "").lower()
    from app.services.bots.ml_registry import model_type_label

    if donor_type and donor_type != model_type_label(strat):
        errors.append(f"strategy mismatch: donor is {donor_type}, target is {strat}")

    from app.services.bots.ml_feature_engineering import SIGNAL_FEATURE_VERSION

    donor_schema = donor_meta.get("feature_schema_version")
    if donor_schema is not None and int(donor_schema) != int(SIGNAL_FEATURE_VERSION):
        errors.append(
            f"feature schema mismatch: donor v{donor_schema}, current v{SIGNAL_FEATURE_VERSION}"
        )

    if strat == "RL_PPO_AGENT":
        from app.services.bots.rl_trading_env import N_ACTIONS, OBS_DIM

        if donor_meta.get("obs_dim") is not None and int(donor_meta["obs_dim"]) != OBS_DIM:
            errors.append(f"obs_dim mismatch: donor {donor_meta['obs_dim']} vs {OBS_DIM}")
        if donor_meta.get("n_actions") is not None and int(donor_meta["n_actions"]) != N_ACTIONS:
            errors.append(f"n_actions mismatch: donor {donor_meta['n_actions']} vs {N_ACTIONS}")

    from app.services.bots.ml_model_artifacts import normalize_model_timeframe

    donor_tf = normalize_model_timeframe(donor_meta.get("timeframe"))
    target_tf = normalize_model_timeframe(timeframe)
    if donor_tf != target_tf:
        errors.append(f"timeframe mismatch: donor {donor_tf}, target {target_tf}")

    if TRANSFER_CHECKPOINT_FILES.get(strat) and not donor_meta.get("metrics"):
        # A donor without metrics was likely a failed/partial train.
        errors.append("donor has no training metrics (partial or failed run)")
    return errors


def build_lineage(
    donor: dict[str, Any],
    *,
    method: str,
    scaler_strategy: str | None = None,
    finetune_budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Lineage block written into the target's ``metadata.json``."""
    meta = donor.get("metadata") or {}
    out: dict[str, Any] = {
        "donor_symbol": str(donor.get("symbol") or "").upper(),
        "donor_version_id": donor.get("version_id"),
        "donor_trained_at": meta.get("trained_at"),
        "method": method,
    }
    if scaler_strategy:
        out["scaler_strategy"] = scaler_strategy
    if finetune_budget:
        out["finetune_budget"] = dict(finetune_budget)
    return out


def load_donor_weights(
    model: Any,
    strategy: str,
    donor_cfg: dict[str, Any] | None,
    timeframe: str | None,
    *,
    device: Any = "cpu",
    head_prefixes: tuple[str, ...] = ("fc", "head"),
) -> dict[str, Any] | None:
    """Resolve, compatibility-check, and load donor weights into ``model``.

    Returns ``{lineage, freeze_trunk, donor}`` on success, None otherwise.
    Never raises — a failed donor must not break a from-scratch train. When
    ``freeze_trunk`` applies, every parameter outside ``head_prefixes`` is
    frozen so only the head adapts to the target asset.
    """
    if not isinstance(donor_cfg, dict) or not donor_cfg.get("symbol"):
        return None
    if not transfer_enabled():
        return None
    strat = str(strategy or "").upper()
    donor = resolve_donor(
        strat, str(donor_cfg["symbol"]), timeframe, donor_cfg.get("version_id"),
    )
    compat = (
        check_compatibility(donor["metadata"], strat, timeframe)
        if donor else ["donor not found"]
    )
    ckpt = donor_checkpoint_path(donor, strat) if donor else None
    if not donor or compat or not ckpt:
        logger.warning(
            "%s donor %s unusable (compat=%s, checkpoint=%s) — training from scratch",
            strat, donor_cfg.get("symbol"), compat or ["no checkpoint"], bool(ckpt),
        )
        return None
    try:
        import torch

        state = torch.load(ckpt, map_location=device)
        model.load_state_dict(state)
    except Exception as exc:
        logger.warning(
            "%s donor weight load failed: %s — training from scratch", strat, exc,
        )
        return None
    try:
        from app.config import TRANSFER_FREEZE_TRUNK_DEFAULT

        freeze = bool(donor_cfg.get("freeze_trunk", TRANSFER_FREEZE_TRUNK_DEFAULT))
    except Exception:
        freeze = bool(donor_cfg.get("freeze_trunk", False))
    if freeze:
        for name, p in model.named_parameters():
            if not any(name.startswith(h) for h in head_prefixes):
                p.requires_grad = False
    lineage = build_lineage(
        donor,
        method=METHOD_WEIGHT_WARM_START,
        finetune_budget={"freeze_trunk": freeze},
    )
    logger.info(
        "%s donor warm-start from %s (freeze_trunk=%s)",
        strat, donor.get("symbol"), freeze,
    )
    return {"lineage": lineage, "freeze_trunk": freeze, "donor": donor}


def list_donors(
    strategy: str,
    symbol: str,
    timeframe: str | None,
) -> list[dict[str, Any]]:
    """Compatible donor candidates on *other* symbols (for the UI picker)."""
    from app.services.bots.ml_model_artifacts import (
        normalize_model_timeframe,
        safe_symbol_key,
    )

    strat = str(strategy or "").upper()
    if strat not in TRANSFER_CHECKPOINT_FILES:
        return []
    target_key = safe_symbol_key(symbol)
    target_tf = normalize_model_timeframe(timeframe)

    from app.config import DATA_DIR
    from app.services.bots.ml_registry import MODEL_SUBDIRS

    sub = MODEL_SUBDIRS.get(strat)
    base = os.path.join(DATA_DIR, sub) if sub else None
    if not base or not os.path.isdir(base):
        return []

    donors: list[dict[str, Any]] = []
    for entry_name in sorted(os.listdir(base)):
        entry_dir = os.path.join(base, entry_name)
        if not os.path.isdir(entry_dir) or entry_name == "versions":
            continue
        # Folder names are model_storage_key: SYMBOL or SYMBOL__TF.
        sym_key, _, tf_key = entry_name.partition("__")
        if sym_key == target_key:
            continue
        folder_tf = normalize_model_timeframe(tf_key.lower() if tf_key else None)
        if folder_tf != target_tf:
            continue
        meta = _read_metadata(entry_dir)
        if meta is None:
            continue
        if check_compatibility(meta, strat, timeframe):
            continue
        needs_weights = TRANSFER_CHECKPOINT_FILES.get(strat) is not None
        ckpt = TRANSFER_CHECKPOINT_FILES.get(strat)
        has_ckpt = bool(ckpt) and os.path.isfile(os.path.join(entry_dir, ckpt))
        if needs_weights and not has_ckpt:
            continue
        metrics = meta.get("metrics") or {}
        donors.append({
            "symbol": str(meta.get("symbol") or sym_key).upper(),
            "version_id": str(meta.get("version_id") or "") or None,
            "trained_at": meta.get("trained_at"),
            "timeframe": meta.get("timeframe"),
            "has_checkpoint": has_ckpt,
            "mean_return_pct": metrics.get("mean_return_pct"),
            "accuracy": metrics.get("accuracy") or metrics.get("val_accuracy"),
            "is_current": True,
        })
    donors.sort(key=lambda d: str(d.get("trained_at") or ""), reverse=True)
    return donors
