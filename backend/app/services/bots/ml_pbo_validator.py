"""ML PBO (Probability of Backtest Overfitting) Validator.

Extends the existing backtest_pbo.py CPCV framework to compute PBO
specifically for ML strategies.  Partitions data into segments, trains
on each IS combination, evaluates on OOS, and computes the probability
that an in-sample winner underperforms out-of-sample.

A PBO > 0.5 indicates overfitting is likely.
A PBO > 0.75 strongly suggests the strategy won't generalize.
"""

from __future__ import annotations

import logging
import statistics
from itertools import combinations
from typing import Any

from app.services.bots.backtest_purged_cv import partition_candles
from app.services.bots.ml_walk_forward_validator import (
    evaluate_oos_accuracy,
    get_trainer,
    is_ml_strategy,
)

logger = logging.getLogger(__name__)


def compute_ml_pbo(
    strategy: str,
    symbol: str,
    candles: list[dict],
    *,
    config: dict | None = None,
    n_segments: int = 8,
    max_combos: int = 35,
) -> dict[str, Any]:
    """Compute Probability of Backtest Overfitting for an ML strategy.

    Uses Combinatorial Symmetric Cross-Validation (CSCV):
    1. Partition candles into n_segments contiguous groups.
    2. For each symmetric split (half IS, half OOS), train on IS groups,
       evaluate accuracy on OOS groups.
    3. PBO = fraction of splits where IS accuracy ranks higher than OOS accuracy.

    Parameters
    ----------
    strategy : str
        ML strategy ID.
    symbol : str
        Trading symbol.
    candles : list[dict]
        Full historical candles.
    config : dict, optional
        Strategy config overrides.
    n_segments : int
        Number of segments to partition data into (default 8).
    max_combos : int
        Maximum number of IS/OOS combinations to evaluate (cap for speed).

    Returns
    -------
    dict with: pbo, n_combos, is_accuracies, oos_accuracies, recommendation.
    """
    cfg = dict(config or {})
    cfg.setdefault("symbol", symbol)
    cfg.setdefault("model_symbol", symbol)
    cfg.setdefault("_wf_mode", True)
    trainer = get_trainer(strategy)
    if trainer is None:
        return {"ok": False, "pbo": None, "error": f"No trainer for {strategy}"}

    # Partition into segments
    segments = partition_candles(candles, n_segments)
    actual_segments = len(segments)
    if actual_segments < 4:
        return {
            "ok": False,
            "pbo": None,
            "error": f"Need >= 4 segments, got {actual_segments}",
        }

    half = actual_segments // 2
    all_combos = list(combinations(range(actual_segments), half))
    if len(all_combos) > max_combos:
        # Sample uniformly
        import random
        rng = random.Random(42)
        all_combos = rng.sample(all_combos, max_combos)

    overfit_count = 0
    total_count = 0
    is_accuracies = []
    oos_accuracies = []

    for combo in all_combos:
        is_indices = set(combo)
        oos_indices = set(range(actual_segments)) - is_indices

        # Concatenate segments
        is_candles = []
        for idx in sorted(is_indices):
            is_candles.extend(segments[idx])

        oos_candles = []
        for idx in sorted(oos_indices):
            oos_candles.extend(segments[idx])

        if len(is_candles) < 100 or len(oos_candles) < 50:
            continue

        # Train on IS
        try:
            result = trainer(symbol, is_candles, config=cfg)
            if not result.get("ok", False):
                continue
        except Exception:
            continue

        # Evaluate IS accuracy (from training metrics if available)
        is_acc = result.get("metrics", {}).get("val_accuracy", 0.0)
        if is_acc == 0.0:
            is_acc = result.get("metrics", {}).get("accuracy", 0.5)

        # Evaluate OOS accuracy
        try:
            oos_result = evaluate_oos_accuracy(strategy, oos_candles, cfg)
            oos_acc = oos_result.get("accuracy", 0.0)
        except Exception:
            continue

        is_accuracies.append(is_acc)
        oos_accuracies.append(oos_acc)
        total_count += 1

        # Overfit if IS accuracy significantly exceeds OOS
        if is_acc > oos_acc:
            overfit_count += 1

    if total_count == 0:
        return {"ok": False, "pbo": None, "error": "No valid combinations evaluated"}

    pbo = overfit_count / total_count

    # Compute degradation (mean IS→OOS accuracy drop)
    mean_is = statistics.mean(is_accuracies) if is_accuracies else 0
    mean_oos = statistics.mean(oos_accuracies) if oos_accuracies else 0
    degradation = mean_is - mean_oos

    recommendation = _pbo_recommendation(pbo, degradation)

    return {
        "ok": True,
        "pbo": round(pbo, 4),
        "n_combos": total_count,
        "n_segments": actual_segments,
        "overfit_count": overfit_count,
        "mean_is_accuracy": round(mean_is, 4),
        "mean_oos_accuracy": round(mean_oos, 4),
        "degradation": round(degradation, 4),
        "recommendation": recommendation,
    }


def _pbo_recommendation(pbo: float, degradation: float) -> str:
    """Generate recommendation based on PBO analysis."""
    if pbo > 0.75:
        return "REJECT — High probability of overfitting (PBO > 0.75). Model unlikely to generalize."
    if pbo > 0.5:
        return "REVIEW — Moderate overfitting risk (PBO > 0.5). Consider simplifying model or adding regularization."
    if degradation > 0.15:
        return "REVIEW — Significant IS→OOS accuracy degradation. Model may be memorizing training patterns."
    if pbo < 0.3:
        return "DEPLOY — Low overfitting risk. Model generalizes well across data partitions."
    return "DEPLOY_WITH_CAUTION — Acceptable PBO but monitor for degradation in live trading."


def pbo_gate_check(
    pbo_result: dict,
    *,
    max_pbo: float = 0.5,
    max_degradation: float = 0.2,
) -> tuple[bool, str]:
    """Binary deploy gate check based on PBO results.

    Returns (passed, reason).
    """
    if not pbo_result.get("ok"):
        return True, "PBO not computed — skipping gate"

    pbo = pbo_result.get("pbo", 1.0)
    degradation = pbo_result.get("degradation", 0.0)

    if pbo > max_pbo:
        return False, f"PBO = {pbo:.2f} exceeds threshold {max_pbo:.2f}"
    if degradation > max_degradation:
        return False, f"IS→OOS degradation = {degradation:.2f} exceeds {max_degradation:.2f}"
    return True, "PBO check passed"
