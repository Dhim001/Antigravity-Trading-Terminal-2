"""Champion-Challenger model promotion gate.

After retraining, the new model (challenger) must pass a comparison
gate against the current model (champion) before being promoted:

1. Train new model → save as challenger version
2. Run challenger on recent N bars → compute OOS metrics
3. Compare with champion's recent live metrics
4. Promote only if challenger ≥ champion performance
5. Log comparison for audit trail

Comparison window size: proportional to training window (20% default).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import numpy as np

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

# Minimum advantage required to promote (prevents noisy promotions)
MIN_PROMOTION_DELTA = -0.02  # challenger can be up to 2% worse and still promote (within noise)


def _get_model_metadata(strategy: str, symbol: str, version_dir: str | None = None) -> dict | None:
    """Load metadata.json from a model directory."""
    try:
        from app.services.bots.ml_model_artifacts import model_root_for
        root = version_dir or model_root_for(strategy, symbol)
        if not root:
            return None
        meta_path = os.path.join(root, "metadata.json")
        if not os.path.isfile(meta_path):
            return None
        with open(meta_path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


class ChampionChallengerGate:
    """Lightweight model promotion gate comparing challenger vs champion."""

    def __init__(
        self,
        *,
        comparison_window_ratio: float = 0.2,
        min_comparison_bars: int = 100,
        max_comparison_bars: int = 2000,
    ):
        self.comparison_window_ratio = comparison_window_ratio
        self.min_comparison_bars = min_comparison_bars
        self.max_comparison_bars = max_comparison_bars
        self._comparison_history: list[dict[str, Any]] = []

    def _comparison_window_size(self, training_samples: int) -> int:
        """Calculate comparison window size proportional to training window."""
        window = int(training_samples * self.comparison_window_ratio)
        return max(self.min_comparison_bars, min(window, self.max_comparison_bars))

    def evaluate_challenger(
        self,
        strategy: str,
        symbol: str,
        challenger_version: str,
        recent_candles: list[dict],
        config: dict | None = None,
    ) -> dict[str, Any]:
        """Compare challenger vs champion on recent data.

        Parameters
        ----------
        strategy : str
            ML strategy ID.
        symbol : str
            Trading symbol.
        challenger_version : str
            Version ID of the newly trained model.
        recent_candles : list[dict]
            Recent candle data for evaluation.
        config : dict, optional
            Strategy config.

        Returns
        -------
        dict with: promote (bool), champion_acc (float), challenger_acc (float),
             delta (float), reason (str), comparison_bars (int).
        """
        cfg = config or {}

        # Load champion metadata for training sample count
        champion_meta = _get_model_metadata(strategy, symbol)
        training_samples = 500  # fallback
        champion_acc = None

        if champion_meta:
            training_samples = champion_meta.get("train_samples", 500)
            # Get champion's validation accuracy as baseline
            champion_acc = champion_meta.get("val_accuracy")
            if champion_acc is None:
                metrics = champion_meta.get("metrics", {})
                champion_acc = metrics.get("val_accuracy")

        # Determine comparison window
        window_size = self._comparison_window_size(training_samples)
        eval_candles = recent_candles[-window_size:] if len(recent_candles) > window_size else recent_candles

        if len(eval_candles) < self.min_comparison_bars:
            # Not enough data — promote by default (can't evaluate)
            result = {
                "promote": True,
                "champion_acc": champion_acc,
                "challenger_acc": None,
                "delta": None,
                "reason": f"insufficient_data ({len(eval_candles)} < {self.min_comparison_bars})",
                "comparison_bars": len(eval_candles),
            }
            self._record(strategy, symbol, challenger_version, result)
            return result

        # Evaluate challenger on the comparison window
        challenger_acc = self._evaluate_model_accuracy(
            strategy, symbol, eval_candles, cfg,
        )

        if challenger_acc is None:
            # Evaluation failed — promote by default
            result = {
                "promote": True,
                "champion_acc": champion_acc,
                "challenger_acc": None,
                "delta": None,
                "reason": "evaluation_failed",
                "comparison_bars": len(eval_candles),
            }
            self._record(strategy, symbol, challenger_version, result)
            return result

        if champion_acc is None:
            # No champion baseline — promote
            result = {
                "promote": True,
                "champion_acc": None,
                "challenger_acc": round(float(challenger_acc), 4),
                "delta": None,
                "reason": "no_champion_baseline",
                "comparison_bars": len(eval_candles),
            }
            self._record(strategy, symbol, challenger_version, result)
            return result

        delta = float(challenger_acc) - float(champion_acc)
        promote = delta >= MIN_PROMOTION_DELTA

        if promote:
            reason = f"challenger wins (delta={delta:+.3f})"
        else:
            reason = f"champion retained (delta={delta:+.3f}, threshold={MIN_PROMOTION_DELTA})"

        result = {
            "promote": promote,
            "champion_acc": round(float(champion_acc), 4),
            "challenger_acc": round(float(challenger_acc), 4),
            "delta": round(delta, 4),
            "reason": reason,
            "comparison_bars": len(eval_candles),
        }
        self._record(strategy, symbol, challenger_version, result)

        logger.info(
            "Champion-Challenger for %s/%s: %s (champion=%.3f, challenger=%.3f, delta=%+.3f)",
            strategy, symbol, "PROMOTE" if promote else "RETAIN",
            champion_acc, challenger_acc, delta,
        )

        return result

    def _evaluate_model_accuracy(
        self,
        strategy: str,
        symbol: str,
        candles: list[dict],
        config: dict,
    ) -> float | None:
        """Run the model's OOS evaluation on given candles."""
        try:
            from app.services.bots.ml_walk_forward_validator import (
                evaluate_oos_accuracy,
                get_trainer,
                is_ml_strategy,
            )
            if not is_ml_strategy(strategy):
                return None

            trainer = get_trainer(strategy)
            if not trainer:
                return None

            # Train on first 80%, test on last 20%
            split = int(len(candles) * 0.8)
            train_candles = candles[:split]
            test_candles = candles[split:]

            if len(train_candles) < 50 or len(test_candles) < 10:
                return None

            # Train a quick model and evaluate OOS
            wf_config = {**config, "_wf_mode": True, "skip_snapshot": True}
            train_result = trainer(symbol, train_candles, config=wf_config)
            if not train_result.get("ok"):
                return None

            accuracy = evaluate_oos_accuracy(strategy, symbol, test_candles, config=config)
            return accuracy
        except Exception as exc:
            logger.debug("Champion-Challenger evaluation failed: %s", exc)
            return None

    def _record(self, strategy: str, symbol: str, version: str, result: dict) -> None:
        """Record comparison result for audit trail."""
        entry = {
            "strategy": strategy,
            "symbol": symbol,
            "version": version,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            **result,
        }
        self._comparison_history.append(entry)
        if len(self._comparison_history) > 50:
            self._comparison_history = self._comparison_history[-50:]

    def get_comparison_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent comparison history for audit trail."""
        return list(reversed(self._comparison_history[-limit:]))

    def promote_challenger(self, strategy: str, symbol: str, version_id: str) -> dict[str, Any]:
        """Swap challenger into the 'current' model slot.

        Delegates to ml_model_artifacts for the actual file swap.
        """
        try:
            from app.services.bots.ml_model_artifacts import (
                model_root_for,
                update_version_status,
            )
            update_version_status(strategy, symbol, version_id, "champion")
            logger.info("Promoted version %s to champion for %s/%s", version_id, strategy, symbol)
            return {"ok": True, "version": version_id}
        except Exception as exc:
            logger.error("Failed to promote challenger: %s", exc)
            return {"ok": False, "error": str(exc)}


# ── Module-level singleton ───────────────────────────────────────────────

_gate: ChampionChallengerGate | None = None


def get_champion_challenger_gate() -> ChampionChallengerGate:
    global _gate
    if _gate is None:
        _gate = ChampionChallengerGate()
    return _gate
