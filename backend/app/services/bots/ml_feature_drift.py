"""Feature drift detection via Population Stability Index (PSI).

Compares the distribution of live inference features against the
training-time baseline stored in model metadata / scaler artifacts.

Thresholds (industry standard):
    PSI < 0.1   → stable (no action needed)
    PSI 0.1–0.25 → moderate drift (investigate)
    PSI > 0.25  → significant drift (retrain recommended)

The monitor persists feature distribution snapshots to disk (lazy loading)
so they survive restarts.  Training baselines come from model metadata.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

import numpy as np

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

DRIFT_DATA_DIR = os.path.join(BASE_DIR, "data", "feature_drift")

# PSI thresholds
PSI_STABLE = 0.1
PSI_MODERATE = 0.25

# Sliding window: keep the last N inference feature vectors
DEFAULT_WINDOW_SIZE = 500


# ── PSI computation ──────────────────────────────────────────────────────


def compute_psi(
    expected: np.ndarray,
    actual: np.ndarray,
    n_bins: int = 10,
) -> float:
    """Compute Population Stability Index between two 1-D arrays.

    Parameters
    ----------
    expected : np.ndarray
        Reference (training) distribution.
    actual : np.ndarray
        Live (inference) distribution.
    n_bins : int
        Number of bins for the histogram comparison.

    Returns
    -------
    float — PSI score. Higher = more drift.
    """
    if len(expected) < 10 or len(actual) < 10:
        return 0.0

    # Use expected quantiles as bin edges for both distributions
    try:
        breakpoints = np.quantile(expected, np.linspace(0, 1, n_bins + 1))
        breakpoints = np.unique(breakpoints)
        if len(breakpoints) < 3:
            return 0.0
    except Exception:
        return 0.0

    expected_counts = np.histogram(expected, bins=breakpoints)[0].astype(float)
    actual_counts = np.histogram(actual, bins=breakpoints)[0].astype(float)

    # Normalize to proportions, add small epsilon to avoid log(0)
    eps = 1e-4
    expected_prop = (expected_counts / expected_counts.sum()) + eps
    actual_prop = (actual_counts / actual_counts.sum()) + eps

    psi = float(np.sum((actual_prop - expected_prop) * np.log(actual_prop / expected_prop)))
    return max(0.0, psi)


def compute_feature_drift(
    training_features: np.ndarray,
    live_features: np.ndarray,
    feature_names: list[str],
    *,
    n_bins: int = 10,
) -> dict[str, Any]:
    """Return per-feature PSI scores and overall drift assessment.

    Parameters
    ----------
    training_features : np.ndarray of shape (N_train, n_features)
        Feature matrix from training data.
    live_features : np.ndarray of shape (N_live, n_features)
        Feature matrix from recent live inference.
    feature_names : list[str]
        Names for each feature column.
    n_bins : int
        Bins for PSI computation.

    Returns
    -------
    dict with keys:
        overall_psi (float), per_feature (list[dict]), assessment (str),
        n_training (int), n_live (int).
    """
    n_features = training_features.shape[1] if training_features.ndim > 1 else 1
    per_feature: list[dict[str, Any]] = []

    for i in range(n_features):
        col_train = training_features[:, i] if training_features.ndim > 1 else training_features
        col_live = live_features[:, i] if live_features.ndim > 1 else live_features
        name = feature_names[i] if i < len(feature_names) else f"feature_{i}"
        psi = compute_psi(col_train, col_live, n_bins=n_bins)
        per_feature.append({"name": name, "psi": round(psi, 6)})

    overall_psi = float(np.mean([f["psi"] for f in per_feature])) if per_feature else 0.0

    if overall_psi > PSI_MODERATE:
        assessment = "significant_drift"
    elif overall_psi > PSI_STABLE:
        assessment = "moderate_drift"
    else:
        assessment = "stable"

    return {
        "overall_psi": round(overall_psi, 6),
        "per_feature": per_feature,
        "assessment": assessment,
        "n_training": int(training_features.shape[0]),
        "n_live": int(live_features.shape[0]),
    }


# ── Feature Drift Monitor (background tracker) ──────────────────────────


class FeatureDriftMonitor:
    """Background monitor that tracks feature distributions over a sliding window.

    Persists distribution snapshots to disk for restart resilience.
    Training baselines are loaded lazily from model metadata.
    """

    def __init__(self, *, window_size: int = DEFAULT_WINDOW_SIZE):
        self._window_size = window_size
        self._buffers: dict[str, list[list[float]]] = {}  # key → recent feature vectors
        self._lock = threading.Lock()

    def _key(self, symbol: str, strategy: str) -> str:
        return f"{symbol.upper()}:{strategy.upper()}"

    def _snapshot_path(self, symbol: str, strategy: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in self._key(symbol, strategy))
        return os.path.join(DRIFT_DATA_DIR, f"{safe}_live.json")

    def _load_buffer(self, symbol: str, strategy: str) -> list[list[float]]:
        """Lazy load persisted buffer from disk."""
        key = self._key(symbol, strategy)
        if key in self._buffers:
            return self._buffers[key]

        path = self._snapshot_path(symbol, strategy)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                self._buffers[key] = data.get("vectors", [])[-self._window_size:]
            except Exception:
                self._buffers[key] = []
        else:
            self._buffers[key] = []

        return self._buffers[key]

    def _save_buffer(self, symbol: str, strategy: str) -> None:
        """Persist buffer to disk."""
        key = self._key(symbol, strategy)
        buf = self._buffers.get(key, [])
        if not buf:
            return
        os.makedirs(DRIFT_DATA_DIR, exist_ok=True)
        path = self._snapshot_path(symbol, strategy)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"vectors": buf[-self._window_size:]}, fh)
        except Exception as exc:
            logger.debug("Failed to save drift buffer: %s", exc)

    def record_inference(self, symbol: str, strategy: str, features: dict | list) -> None:
        """Record a single inference feature vector into the sliding window.

        Parameters
        ----------
        symbol : str
        strategy : str
        features : dict or list
            Feature dict (name→value) or flat list of values.
        """
        if isinstance(features, dict):
            vec = list(features.values())
        else:
            vec = list(features)

        with self._lock:
            buf = self._load_buffer(symbol, strategy)
            buf.append(vec)
            # Trim to window size
            if len(buf) > self._window_size:
                self._buffers[self._key(symbol, strategy)] = buf[-self._window_size:]
            # Persist every 50 new entries to avoid excessive I/O
            if len(buf) % 50 == 0:
                self._save_buffer(symbol, strategy)

    def check_drift(
        self,
        symbol: str,
        strategy: str,
        *,
        training_features: np.ndarray | None = None,
        feature_names: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Check feature drift for the given symbol/strategy pair.

        If training_features is not provided, attempts to load from
        the model's scaler metadata (mean/std baseline).

        Returns None if insufficient data for comparison.
        """
        with self._lock:
            buf = self._load_buffer(symbol, strategy)
            if len(buf) < 30:
                return None

            live_arr = np.array(buf[-self._window_size:], dtype=np.float32)

        # Attempt to load training baseline if not provided
        if training_features is None:
            training_features = self._load_training_baseline(symbol, strategy)

        if training_features is None or len(training_features) < 10:
            return None

        # Auto-detect feature names
        if feature_names is None:
            feature_names = [f"f_{i}" for i in range(live_arr.shape[1])]

        # Ensure shape compatibility
        n_features = min(training_features.shape[1], live_arr.shape[1])
        return compute_feature_drift(
            training_features[:, :n_features],
            live_arr[:, :n_features],
            feature_names[:n_features],
        )

    def _load_training_baseline(self, symbol: str, strategy: str) -> np.ndarray | None:
        """Try loading training feature baseline from model scaler/metadata."""
        try:
            from app.services.bots.ml_model_artifacts import model_root_for
            root = model_root_for(strategy, symbol)
            if not root:
                return None

            # Try scaler.json (contains mean/std from training)
            scaler_path = os.path.join(root, "scaler.json")
            if os.path.isfile(scaler_path):
                with open(scaler_path, encoding="utf-8") as fh:
                    scaler = json.load(fh)
                mean = np.array(scaler.get("mean", []), dtype=np.float32)
                std = np.array(scaler.get("std", []), dtype=np.float32)
                if len(mean) > 0:
                    # Synthesize a training baseline from mean/std
                    # (approximation — Gaussian samples around training distribution)
                    rng = np.random.default_rng(42)
                    n_synth = 200
                    baseline = rng.normal(
                        loc=mean, scale=std, size=(n_synth, len(mean)),
                    ).astype(np.float32)
                    return baseline
        except Exception as exc:
            logger.debug("Could not load training baseline for %s/%s: %s", strategy, symbol, exc)
        return None


# ── Module-level singleton ───────────────────────────────────────────────

_monitor: FeatureDriftMonitor | None = None


def get_feature_drift_monitor() -> FeatureDriftMonitor:
    global _monitor
    if _monitor is None:
        _monitor = FeatureDriftMonitor()
    return _monitor
