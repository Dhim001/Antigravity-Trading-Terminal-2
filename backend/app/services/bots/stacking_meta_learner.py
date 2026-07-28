"""Stacking meta-learner — learned gating over base learners.

Phase 2.6 of the Signal Enhancement Plan.

The HYBRID_ENSEMBLE currently combines TA/ML/RL base learners via fixed or
ad-hoc adaptive weights. A stacking meta-learner *learns* how to combine them
from their out-of-sample predictions:

1. **Inverse-MSE weighting** (simple, robust): weight_i ∝ 1 / MSE_i, where MSE_i
   is base learner i's OOS mean-squared error against the realised label. This
   is the classic Bates & Granger (1969) combination and needs no extra model.

2. **Gating network** (capacity): a small logistic regression on the base
   learners' predicted probabilities (plus optional features) that learns
   which learner to trust in which regime. Trained on the OOS predictions +
   realised labels.

The gating network is the strictly more powerful combiner; inverse-MSE is the
fallback when there isn't enough OOS data to fit a gating model without
overfitting. Both are opt-in via ``ensemble_combination: "stacking"``.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from typing import Sequence

import numpy as np

logger = logging.getLogger(__name__)

MIN_OOS_SAMPLES = 50
DEFAULT_REG_C = 1.0


@dataclass
class StackingModel:
    """Fitted stacking meta-learner."""

    mode: str                       # "inverse_mse" | "gating"
    weights: tuple[float, ...]     # per base learner (inverse-mse) or gating coeffs
    base_names: tuple[str, ...]
    n_oos: int = 0
    # Gating-only: logistic regression coefficients (n_bases + 1 for bias)
    gating_coeffs: tuple[float, ...] | None = None

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "weights": list(self.weights),
            "base_names": list(self.base_names),
            "n_oos": int(self.n_oos),
            "gating_coeffs": list(self.gating_coeffs) if self.gating_coeffs else None,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "StackingModel | None":
        if not d or not isinstance(d, dict):
            return None
        try:
            return cls(
                mode=str(d.get("mode") or "inverse_mse"),
                weights=tuple(float(w) for w in d.get("weights", [])),
                base_names=tuple(str(n) for n in d.get("base_names", [])),
                n_oos=int(d.get("n_oos") or 0),
                gating_coeffs=(
                    tuple(float(c) for c in d.get("gating_coeffs"))
                    if d.get("gating_coeffs") else None
                ),
            )
        except (TypeError, ValueError):
            return None


# ── Inverse-MSE weighting ─────────────────────────────────────────────────


def fit_inverse_mse(
    base_predictions: np.ndarray,   # (n_samples, n_bases) predicted P(up)
    labels: np.ndarray,             # (n_samples,) realised 0/1
    base_names: Sequence[str],
) -> StackingModel:
    """Bates-Granger inverse-MSE combination weights."""
    if base_predictions.size == 0 or len(labels) == 0:
        # uniform fallback
        n = len(base_names) or 1
        w = tuple(1.0 / n for _ in range(n))
        return StackingModel(mode="inverse_mse", weights=w,
                              base_names=tuple(base_names), n_oos=0)
    sq_err = (base_predictions - labels.reshape(-1, 1)) ** 2  # (n, bases)
    mse = sq_err.mean(axis=0)                                   # (bases,)
    inv = 1.0 / np.maximum(mse, 1e-9)
    w = inv / inv.sum()
    return StackingModel(
        mode="inverse_mse",
        weights=tuple(float(x) for x in w),
        base_names=tuple(base_names),
        n_oos=int(len(labels)),
    )


# ── Gating network (logistic regression) ──────────────────────────────────


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return np.where(x >= 0, 1.0 / (1.0 + np.exp(-x)), np.exp(x) / (1.0 + np.exp(x)))


def fit_gating(
    base_predictions: np.ndarray,
    labels: np.ndarray,
    base_names: Sequence[str],
    *,
    n_steps: int = 500,
    lr: float = 0.1,
    reg_c: float = DEFAULT_REG_C,
) -> StackingModel:
    """Logistic-regression gating on base predictions.

    Input features = base predictions (n_bases) + bias. Output = P(win).
    Trained via gradient descent on cross-entropy with L2 regularisation.
    """
    n, n_bases = base_predictions.shape if base_predictions.size else (0, len(base_names))
    if n < MIN_OOS_SAMPLES:
        return fit_inverse_mse(base_predictions, labels, base_names)

    X = np.column_stack([base_predictions, np.ones(n)])  # (n, n_bases+1)
    y = labels.astype(float)
    beta = np.zeros(n_bases + 1)
    lam = 1.0 / max(reg_c, 1e-9)
    for _ in range(n_steps):
        p = _sigmoid(X @ beta)
        grad = X.T @ (p - y) / n + lam * beta
        beta -= lr * grad
    # Derive inverse-MSE-style weights from the gating coeffs for the
    # `weights` field (diagnostic only — the live path uses gating_coeffs).
    sq_err = (base_predictions - labels.reshape(-1, 1)) ** 2
    mse = sq_err.mean(axis=0)
    inv = 1.0 / np.maximum(mse, 1e-9)
    w = inv / inv.sum()
    return StackingModel(
        mode="gating",
        weights=tuple(float(x) for x in w),
        base_names=tuple(base_names),
        n_oos=int(n),
        gating_coeffs=tuple(float(c) for c in beta),
    )


def fit_stacking(
    base_predictions: np.ndarray,
    labels: np.ndarray,
    base_names: Sequence[str],
    *,
    prefer_gating: bool = True,
) -> StackingModel:
    """Dispatch to gating (if enough data) else inverse-MSE."""
    n = len(labels) if labels is not None else 0
    if prefer_gating and n >= MIN_OOS_SAMPLES and base_predictions.size:
        try:
            return fit_gating(base_predictions, labels, base_names)
        except Exception as exc:
            logger.debug("Gating fit failed, falling back to inverse-MSE: %s", exc)
    return fit_inverse_mse(base_predictions, labels, base_names)


# ── Prediction ────────────────────────────────────────────────────────────


def predict_stacked(
    base_probs: np.ndarray,   # (n_bases,) predicted P(up) from each base learner
    model: StackingModel,
) -> float:
    """Combine base learner probabilities into a single P(up)."""
    if model is None or len(model.weights) == 0:
        return 0.5
    p = np.asarray(base_probs, dtype=float).reshape(-1)
    if len(p) != len(model.weights):
        return 0.5
    if model.mode == "gating" and model.gating_coeffs:
        coeffs = np.array(model.gating_coeffs)
        feats = np.append(p, 1.0)  # bias
        if len(feats) == len(coeffs):
            return float(_sigmoid(feats @ coeffs)[()])
    # inverse-mse: weighted average
    w = np.array(model.weights)
    return float((p * w).sum())


def stacked_signal(
    base_probs: np.ndarray,
    model: StackingModel,
    *,
    threshold: float = 0.5,
    min_margin: float = 0.0,
) -> tuple[str, float]:
    """Return (signal, combined_prob) from stacked prediction."""
    p = predict_stacked(base_probs, model)
    if p >= threshold + min_margin and p > 0.5:
        return "BUY", p
    if p <= 1.0 - threshold - min_margin and p < 0.5:
        return "SELL", 1.0 - p
    return "NONE", p


# ── Persistence + cache ───────────────────────────────────────────────────

_bot_models: dict[str, StackingModel] = {}


def _default_path(bot_id: str) -> str:
    from app.config import DATA_DIR
    return os.path.join(DATA_DIR, "stacking", f"{bot_id}.json")


def save_stacking_model(bot_id: str, model: StackingModel, *, path: str | None = None) -> None:
    target = path or _default_path(bot_id)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(model.to_dict(), fh, indent=2)
    os.replace(tmp, target)
    _bot_models[bot_id] = model


def load_stacking_model(bot_id: str, *, path: str | None = None) -> StackingModel | None:
    if bot_id in _bot_models:
        return _bot_models[bot_id]
    target = path or _default_path(bot_id)
    try:
        with open(target, "r", encoding="utf-8") as fh:
            model = StackingModel.from_dict(json.load(fh))
        if model:
            _bot_models[bot_id] = model
        return model
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Failed to load stacking model %s: %s", target, exc)
        return None


def invalidate_stacking_cache(bot_id: str | None = None) -> None:
    if bot_id is None:
        _bot_models.clear()
    else:
        _bot_models.pop(bot_id, None)
