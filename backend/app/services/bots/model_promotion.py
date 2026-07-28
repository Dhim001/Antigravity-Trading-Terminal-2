"""Champion-challenger model promotion gate.

Phase 3.8 of the Signal Enhancement Plan.

Today, ``activate_model_version`` promotes any version to the live root
unconditionally. This module adds the missing gate: a freshly-trained
**challenger** is only promoted to **champion** if it beats the current
champion on out-of-sample validation metrics by a configurable margin.

Promotion policy (defaults, all overridable per-strategy via config):

- ``min_oos_improvement_pct`` — challenger must beat champion's primary OOS
  metric (e.g. OOS Sharpe, val AUC, PBO) by at least this %. Default 5%.
- ``min_sample_size`` — challenger must have been validated on at least this
  many OOS bars. Default 200.
- ``require_champion_validation`` — if True and the champion has no
  validation metrics, refuse to promote (forces a cold champion to be
  validated before it can be dethroned). Default False (allow first-challenger
  promotion when champion is unvalidated).

Drift-triggered retraining is already handled by the retrain scheduler's
``feature_drift`` reason; this module gates the *outcome* of that retrain.
When drift triggers a retrain, the challenger must still clear the
champion-challenger bar — drift is a reason to retrain, not a reason to
promote blindly.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PromotionPolicy:
    min_oos_improvement_pct: float = 5.0
    min_sample_size: int = 200
    require_champion_validation: bool = False
    primary_metric: str = "oos_sharpe"  # fallback chain: oos_sharpe → val_auc → pbo

    def to_dict(self) -> dict:
        return {
            "min_oos_improvement_pct": self.min_oos_improvement_pct,
            "min_sample_size": self.min_sample_size,
            "require_champion_validation": self.require_champion_validation,
            "primary_metric": self.primary_metric,
        }

    @classmethod
    def from_config(cls, config: dict | None) -> "PromotionPolicy":
        cfg = config if isinstance(config, dict) else {}
        return cls(
            min_oos_improvement_pct=float(cfg.get("cc_min_oos_improvement_pct", 5.0)),
            min_sample_size=int(cfg.get("cc_min_sample_size", 200)),
            require_champion_validation=bool(cfg.get("cc_require_champion_validation", False)),
            primary_metric=str(cfg.get("cc_primary_metric", "oos_sharpe")),
        )


# ── Metric extraction ────────────────────────────────────────────────────


_METRIC_FALLBACK = ("oos_sharpe", "val_auc", "pbo", "oos_accuracy", "val_log_loss")


def _extract_metric(metrics: dict | None, primary: str) -> tuple[str, float | None]:
    """Return (metric_name, value) — primary metric, else first available fallback."""
    if not isinstance(metrics, dict):
        return primary, None
    if primary in metrics and metrics[primary] is not None:
        try:
            return primary, float(metrics[primary])
        except (TypeError, ValueError):
            pass
    for name in _METRIC_FALLBACK:
        if name in metrics and metrics[name] is not None:
            try:
                return name, float(metrics[name])
            except (TypeError, ValueError):
                continue
    return primary, None


def _extract_sample_size(metrics: dict | None) -> int:
    if not isinstance(metrics, dict):
        return 0
    for key in ("oos_samples", "val_samples", "n_oos", "sample_size"):
        try:
            val = int(metrics.get(key) or 0)
            if val > 0:
                return val
        except (TypeError, ValueError):
            continue
    return 0


# ── Verdict ──────────────────────────────────────────────────────────────


@dataclass
class PromotionVerdict:
    decision: str            # "promote" | "keep_champion" | "shadow"
    reason: str
    metric_name: str
    champion_value: float | None
    challenger_value: float | None
    improvement_pct: float
    challenger_samples: int


def evaluate_challenger(
    champion_metrics: dict | None,
    challenger_metrics: dict | None,
    policy: PromotionPolicy,
) -> PromotionVerdict:
    """Decide whether a challenger dethrones the champion."""
    metric_name, champ_val = _extract_metric(champion_metrics, policy.primary_metric)
    _, chall_val = _extract_metric(challenger_metrics, policy.primary_metric)
    chall_samples = _extract_sample_size(challenger_metrics)

    # Sample-size floor — a challenger validated on too few bars is unreliable.
    if chall_samples < policy.min_sample_size:
        return PromotionVerdict(
            decision="shadow",
            reason=(
                f"challenger OOS samples {chall_samples} < min {policy.min_sample_size}"
            ),
            metric_name=metric_name,
            champion_value=champ_val,
            challenger_value=chall_val,
            improvement_pct=0.0,
            challenger_samples=chall_samples,
        )

    # Champion has no validation metrics.
    if champ_val is None:
        if policy.require_champion_validation:
            return PromotionVerdict(
                decision="keep_champion",
                reason="champion has no validation metrics and require_champion_validation=True",
                metric_name=metric_name,
                champion_value=None,
                challenger_value=chall_val,
                improvement_pct=0.0,
                challenger_samples=chall_samples,
            )
        # No champion baseline → promote the first challenger that clears
        # the sample floor (cold-start path).
        if chall_val is not None:
            return PromotionVerdict(
                decision="promote",
                reason="champion unvalidated — cold-start promotion",
                metric_name=metric_name,
                champion_value=None,
                challenger_value=chall_val,
                improvement_pct=float("inf"),
                challenger_samples=chall_samples,
            )
        return PromotionVerdict(
            decision="shadow",
            reason="neither champion nor challenger has validation metrics",
            metric_name=metric_name,
            champion_value=None,
            challenger_value=None,
            improvement_pct=0.0,
            challenger_samples=chall_samples,
        )

    # Both have metrics — compare.
    if chall_val is None:
        return PromotionVerdict(
            decision="keep_champion",
            reason="challenger has no validation metrics",
            metric_name=metric_name,
            champion_value=champ_val,
            challenger_value=None,
            improvement_pct=0.0,
            challenger_samples=chall_samples,
        )

    # For log_loss, lower is better. For all others, higher is better.
    lower_is_better = metric_name == "val_log_loss"
    if lower_is_better:
        improvement_pct = ((champ_val - chall_val) / max(abs(champ_val), 1e-9)) * 100.0
        challenger_wins = chall_val < champ_val
    else:
        improvement_pct = ((chall_val - champ_val) / max(abs(champ_val), 1e-9)) * 100.0
        challenger_wins = chall_val > champ_val

    if challenger_wins and improvement_pct >= policy.min_oos_improvement_pct:
        return PromotionVerdict(
            decision="promote",
            reason=(
                f"challenger {metric_name}={chall_val:.4f} beats champion "
                f"{champ_val:.4f} by {improvement_pct:.1f}% "
                f"(≥ {policy.min_oos_improvement_pct}%)"
            ),
            metric_name=metric_name,
            champion_value=champ_val,
            challenger_value=chall_val,
            improvement_pct=improvement_pct,
            challenger_samples=chall_samples,
        )

    return PromotionVerdict(
        decision="keep_champion" if challenger_wins else "keep_champion",
        reason=(
            f"challenger {metric_name}={chall_val:.4f} does not beat champion "
            f"{champ_val:.4f} by ≥ {policy.min_oos_improvement_pct}% "
            f"(actual: {improvement_pct:+.1f}%)"
        ),
        metric_name=metric_name,
        champion_value=champ_val,
        challenger_value=chall_val,
        improvement_pct=improvement_pct,
        challenger_samples=chall_samples,
    )


# ── Full promotion flow ──────────────────────────────────────────────────


def promote_challenger_if_better(
    strategy: str,
    symbol: str,
    challenger_version: str,
    *,
    champion_version: str | None = None,
    policy: PromotionPolicy | None = None,
    timeframe: str | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    """Gate a challenger through champion-challenger before activation.

    Reads both versions' validation metrics from the model index, evaluates,
    and only calls ``activate_model_version`` when the challenger wins. The
    challenger is tagged ``challenger`` in the index regardless; on promotion
    it becomes ``champion`` and the old champion is retired.

    Returns a dict with ``ok``, ``decision``, ``reason``, and the verdict.
    """
    from app.services.bots.ml_model_artifacts import (
        activate_model_version,
        find_version_entry,
        list_model_versions,
        model_root_for,
        update_version_status,
    )

    policy = policy or PromotionPolicy.from_config(config)
    strat = str(strategy or "").upper()
    sym = str(symbol or "").upper()
    root = model_root_for(strat, sym, timeframe)
    if not root:
        return {"ok": False, "error": f"No model directory for {strat}/{sym}"}

    versions = list_model_versions(root) or []
    if not versions:
        return {"ok": False, "error": "No model versions found"}

    # Find challenger + champion entries.
    challenger_entry = None
    champion_entry = None
    for v in versions:
        vid = str(v.get("version_id") or "")
        if vid == str(challenger_version):
            challenger_entry = v
        if champion_version and vid == str(champion_version):
            champion_entry = v
        if not champion_version and v.get("status") == "champion":
            champion_entry = v

    if not challenger_entry:
        return {"ok": False, "error": f"Challenger version not found: {challenger_version}"}

    challenger_metrics = challenger_entry.get("validation") or challenger_entry.get("metrics") or {}
    champion_metrics = (champion_entry or {}).get("validation") or (champion_entry or {}).get("metrics") or {}

    verdict = evaluate_challenger(champion_metrics, challenger_metrics, policy)

    # Tag the challenger regardless of outcome.
    try:
        update_version_status(strat, sym, str(challenger_version), "challenger", timeframe=timeframe)
    except Exception:
        logger.debug("Failed to tag challenger status", exc_info=True)

    if verdict.decision != "promote":
        return {
            "ok": True,
            "decision": verdict.decision,
            "reason": verdict.reason,
            "verdict": verdict.__dict__,
            "challenger_version": str(challenger_version),
            "champion_version": str(champion_version) if champion_version else None,
        }

    # Promote: activate the challenger and tag it champion.
    activation = activate_model_version(strat, sym, str(challenger_version), timeframe=timeframe)
    if not activation.get("ok"):
        return {
            "ok": False,
            "error": activation.get("error", "activation failed"),
            "decision": "keep_champion",
            "verdict": verdict.__dict__,
        }
    try:
        update_version_status(strat, sym, str(challenger_version), "champion", timeframe=timeframe)
    except Exception:
        logger.debug("Failed to tag new champion status", exc_info=True)

    logger.info(
        "Champion-challenger promotion: %s/%s %s → champion (%s)",
        strat, sym, challenger_version, verdict.reason,
    )
    return {
        "ok": True,
        "decision": "promote",
        "reason": verdict.reason,
        "verdict": verdict.__dict__,
        "challenger_version": str(challenger_version),
        "champion_version": str(champion_version) if champion_version else None,
        "activation": activation,
    }
