"""Walk-forward feature/label cache — precompute once, slice/gather per fold.

Avoids rebuilding overlapping IS prefixes on every WF fold (Optimizer Opt #4).
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from app.services.bots.ml_triple_barrier import label_triple_barrier


class WfFeatureCache:
    """Precompute feature matrix + triple-barrier labels once for a candle series."""

    def __init__(self, candles: list[dict], config: dict | None = None) -> None:
        from app.services.bots.ml_feature_engineering import (
            EVAL_FEATURE_LOOKBACK,
            precompute_signal_feature_matrix,
        )

        cfg = config if isinstance(config, dict) else {}
        atr_mult = float(cfg.get("triple_barrier_atr_mult", 2.0))
        max_bars = int(cfg.get("triple_barrier_max_bars", 30))
        self.feature_lookback = EVAL_FEATURE_LOOKBACK
        self.n = len(candles or [])
        self.feature_matrix = precompute_signal_feature_matrix(
            candles or [],
            feature_lookback=self.feature_lookback,
        )
        self.labels = label_triple_barrier(
            candles or [],
            atr_mult_upper=atr_mult,
            atr_mult_lower=atr_mult,
            max_holding_bars=max_bars,
        )
        from app.services.bots.ml_event_sampling import annotate_event_labels
        from app.services.bots.ml_feature_engineering import (
            apply_exclude_features,
            resolve_exclude_features,
        )

        self.labels = annotate_event_labels(self.labels, candles or [], cfg)
        exclude = resolve_exclude_features(cfg)
        if exclude:
            self.feature_matrix = apply_exclude_features(self.feature_matrix, exclude)

    def gather(self, indices: Sequence[int]) -> dict[str, Any]:
        """Return features/labels aligned to ``indices`` (may be non-contiguous)."""
        if not indices:
            return {
                "features": np.empty((0, self.feature_matrix.shape[1]), dtype=np.float32)
                if self.feature_matrix.ndim == 2
                else np.array([]),
                "labels": [],
            }
        idx = np.asarray(list(indices), dtype=np.int64)
        return {
            "features": np.asarray(self.feature_matrix[idx], dtype=np.float32),
            "labels": [self.labels[int(i)] for i in idx],
        }

    def attach_config(self, cfg: dict | None, indices: Sequence[int]) -> dict[str, Any]:
        """Copy ``cfg`` and attach gathered ``_precomputed_features`` / labels."""
        out = dict(cfg or {})
        gathered = self.gather(indices)
        out["_precomputed_features"] = gathered["features"]
        out["_precomputed_labels"] = gathered["labels"]
        return out


def resolve_precomputed_features(
    candles: list[dict],
    config: dict | None,
) -> np.ndarray | None:
    """Return cached feature matrix when it matches ``candles`` length."""
    cfg = config if isinstance(config, dict) else {}
    pre = cfg.get("_precomputed_features")
    if pre is None:
        return None
    arr = np.asarray(pre)
    if arr.ndim != 2 or arr.shape[0] != len(candles or []):
        return None
    return arr.astype(np.float32, copy=False)


def resolve_precomputed_labels(
    candles: list[dict],
    config: dict | None,
) -> list[dict] | None:
    """Return cached labels when length matches ``candles``."""
    cfg = config if isinstance(config, dict) else {}
    pre = cfg.get("_precomputed_labels")
    if not isinstance(pre, list) or len(pre) != len(candles or []):
        return None
    return pre
