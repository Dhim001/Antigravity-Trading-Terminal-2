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
import time
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

# MEMORY_CENTRIC_REVIEW #34 — symbol×strategy buffer keys grew unbounded.
# Buffers persist to disk, so evicted keys lazily reload on next access.
_MAX_BUFFER_KEYS = int(os.environ.get("FEATURE_DRIFT_MAX_BUFFER_KEYS", "64"))
_IDLE_EVICT_SEC = float(os.environ.get("FEATURE_DRIFT_IDLE_EVICT_SEC", str(6 * 3600)))


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
        self._last_access: dict[str, float] = {}  # key → monotonic timestamp (#34)
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
            self._last_access[key] = time.monotonic()
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

        self._last_access[key] = time.monotonic()
        return self._buffers[key]

    def _maybe_evict_idle_keys(self) -> None:
        """MEMORY #34 — bound symbol×strategy buffer keys (idle + LRU eviction).

        Buffers persist to disk, so eviction loses nothing long-term: a later
        access lazily reloads the snapshot. Save-before-drop keeps the tail
        that has not hit the periodic-persist threshold yet.
        """
        if len(self._buffers) <= _MAX_BUFFER_KEYS:
            return
        now = time.monotonic()
        victims = [
            k for k in self._buffers
            if now - self._last_access.get(k, 0.0) > _IDLE_EVICT_SEC
        ]
        excess = len(self._buffers) - len(victims) - _MAX_BUFFER_KEYS
        if excess > 0:
            remaining = [k for k in self._buffers if k not in victims]
            remaining.sort(key=lambda k: self._last_access.get(k, 0.0))
            victims.extend(remaining[:excess])
        for key in victims:
            parts = key.split(":", 1)
            if len(parts) == 2 and self._buffers.get(key):
                self._save_buffer(parts[0], parts[1])
            self._buffers.pop(key, None)
            self._last_access.pop(key, None)

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

    def _expected_feature_dim(self) -> int:
        try:
            from app.services.bots.ml_feature_engineering import SIGNAL_FEATURE_NAMES

            return len(SIGNAL_FEATURE_NAMES)
        except Exception:
            return 0

    def _homogeneous_vectors(self, buf: list[list[float]], *, dim: int | None = None) -> list[list[float]]:
        """Keep only vectors matching the current schema width (drop pre-bump rows)."""
        if not buf:
            return []
        target = dim if dim and dim > 0 else self._expected_feature_dim()
        if target <= 0:
            # Fall back to majority length in the buffer.
            lengths = [len(v) for v in buf if isinstance(v, (list, tuple))]
            if not lengths:
                return []
            target = max(set(lengths), key=lengths.count)
        return [list(v) for v in buf if isinstance(v, (list, tuple)) and len(v) == target]

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
            vec = [float(x) for x in features]

        with self._lock:
            # Schema bumps (e.g. v3→v4) must not mix widths — numpy PSI would crash.
            # Validate BEFORE _load_buffer so rejected vectors never create
            # empty buffer entries that eviction would have to chase.
            expected = self._expected_feature_dim()
            if expected > 0 and len(vec) != expected:
                return
            buf = self._load_buffer(symbol, strategy)
            if buf and any(len(v) != len(vec) for v in buf if isinstance(v, (list, tuple))):
                buf[:] = self._homogeneous_vectors(buf, dim=len(vec))
            buf.append(vec)
            # Trim to window size
            if len(buf) > self._window_size:
                self._buffers[self._key(symbol, strategy)] = buf[-self._window_size:]
            # Persist every 50 new entries to avoid excessive I/O
            if len(buf) % 50 == 0:
                self._save_buffer(symbol, strategy)
            self._maybe_evict_idle_keys()

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
            homogeneous = self._homogeneous_vectors(buf)
            if len(homogeneous) < 30:
                return None

            live_arr = np.array(homogeneous[-self._window_size:], dtype=np.float32)

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
        """Load the real training feature baseline; fall back to Gaussian approx."""
        try:
            from app.services.bots.ml_model_artifacts import model_root_for
            root = model_root_for(strategy, symbol)
            if not root:
                return None

            # Prefer the real persisted training sample (no synthetic drift noise).
            baseline_path = os.path.join(root, "feature_baseline.json")
            if os.path.isfile(baseline_path):
                with open(baseline_path, encoding="utf-8") as fh:
                    payload = json.load(fh)
                feats = np.array(payload.get("features", []), dtype=np.float32)
                if feats.ndim == 2 and feats.shape[0] > 10 and feats.shape[1] > 0:
                    return feats

            # Legacy fallback: synthesize from scaler mean/std (approximate —
            # kept for models trained before feature_baseline.json existed).
            scaler_path = os.path.join(root, "scaler.json")
            if os.path.isfile(scaler_path):
                with open(scaler_path, encoding="utf-8") as fh:
                    scaler = json.load(fh)
                mean = np.array(scaler.get("mean", []), dtype=np.float32)
                std = np.array(scaler.get("std", []), dtype=np.float32)
                if len(mean) > 0:
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


def record_ml_inference_features(
    symbol: str | None,
    strategy: str | None,
    features: dict | list | Any,
) -> None:
    """Best-effort: record live inference features for PSI / alpha-decay Metric 8.

    Safe to call from hot evaluate() paths — never raises into the strategy.
    """
    sym = (symbol or "").strip().upper()
    strat = (strategy or "").strip().upper()
    if not sym or not strat or features is None:
        return
    try:
        if isinstance(features, np.ndarray):
            features = features.reshape(-1).tolist()
        elif isinstance(features, dict):
            from app.services.bots.ml_feature_engineering import SIGNAL_FEATURE_NAMES

            # Stable column order matching training / scaler baselines.
            features = [float(features.get(n, 0.0)) for n in SIGNAL_FEATURE_NAMES]
        get_feature_drift_monitor().record_inference(sym, strat, features)
    except Exception as exc:
        logger.debug("record_ml_inference_features failed for %s/%s: %s", strat, sym, exc)


def get_drift_summary_for_ui(symbol: str, strategy: str) -> dict[str, Any]:
    """Return compact drift summary blob for ML Training Dashboard UI."""
    try:
        mon = get_feature_drift_monitor()
        res = mon.check_drift(symbol, strategy)
        if not res:
            return {"available": False, "assessment": "unknown"}
        drifted_count = sum(1 for f in res.get("per_feature", []) if f.get("psi", 0) > PSI_MODERATE)
        return {
            "available": True,
            "overall_psi": res.get("overall_psi", 0.0),
            "assessment": res.get("assessment", "stable"),
            "drifted_features_count": drifted_count,
            "n_live": res.get("n_live", 0),
            "n_training": res.get("n_training", 0),
        }
    except Exception:
        return {"available": False, "assessment": "unknown"}


def should_recommend_retrain(symbol: str, strategy: str) -> bool:
    """True when drift monitor detects significant drift requiring retrain."""
    summary = get_drift_summary_for_ui(symbol, strategy)
    if not summary.get("available"):
        return False
    return summary.get("assessment") == "significant_drift" or summary.get("drifted_features_count", 0) >= 3
