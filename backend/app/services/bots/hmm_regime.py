"""HMM regime gate — soft posterior-weighted signal scaling.

Phase 2.5 of the Signal Enhancement Plan.

Identifies market regimes via a Gaussian mixture on (log-return, rolling-vol)
features — the emission step of a Hidden Markov Model. We use the mixture
(rather than a full HMM with transition dynamics) because:

1. The gate only needs the *current-bar* regime posterior, which the emission
   model alone provides. Transition dynamics matter for forecasting future
   states, not for labelling the current one.
2. A mixture is far more robust to fit on limited data than a full HMM, and
   doesn't require ``hmmlearn`` (not in requirements.txt).
3. The soft posteriors are exactly what we need for *soft gating*: scale
   signal confidence by regime suitability instead of hard-blocking.

States are mapped to regimes post-hoc by centroid characteristics:
- high vol + positive mean  → bull-volatile
- high vol + negative mean  → bear-volatile
- low vol + positive mean  → bull-quiet
- low vol + negative mean  → bear-quiet

The gate then scales BUY confidence up in bull regimes and down in bear
regimes, and dampens everything in high-vol regimes. Opt-in via
``hmm_regime_gate_enabled``.
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

DEFAULT_N_STATES = 4
DEFAULT_VOL_LOOKBACK = 20
MIN_FIT_SAMPLES = 100
# Scale clamps — regime gate is a soft multiplier, never zero or huge.
MIN_REGIME_SCALE = 0.50
MAX_REGIME_SCALE = 1.50


@dataclass
class RegimeModel:
    """Fitted Gaussian mixture + state→regime label mapping."""

    means: tuple[tuple[float, float], ...]      # (mean_ret, mean_vol) per state
    covariances: tuple[np.ndarray, ...]
    weights: tuple[float, ...]
    state_labels: tuple[str, ...]              # bull_quiet | bull_volatile | bear_quiet | bear_volatile
    vol_threshold: float                       # median vol across states
    n_states: int = DEFAULT_N_STATES

    def to_dict(self) -> dict:
        return {
            "means": [list(m) for m in self.means],
            "covariances": [c.tolist() for c in self.covariances],
            "weights": list(self.weights),
            "state_labels": list(self.state_labels),
            "vol_threshold": self.vol_threshold,
            "n_states": self.n_states,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "RegimeModel | None":
        if not d or not isinstance(d, dict):
            return None
        try:
            return cls(
                means=tuple(tuple(m) for m in d.get("means", [])),
                covariances=tuple(np.array(c) for c in d.get("covariances", [])),
                weights=tuple(float(w) for w in d.get("weights", [])),
                state_labels=tuple(str(s) for s in d.get("state_labels", [])),
                vol_threshold=float(d.get("vol_threshold", 0.0)),
                n_states=int(d.get("n_states", DEFAULT_N_STATES)),
            )
        except (TypeError, ValueError):
            return None


def _features_from_candles(
    candles: Sequence[dict],
    *,
    vol_lookback: int = DEFAULT_VOL_LOOKBACK,
) -> np.ndarray:
    """Build (log_return, rolling_vol) feature matrix from candles."""
    closes = np.array([float(c.get("close", 0)) for c in candles], dtype=np.float64)
    if len(closes) < 2:
        return np.zeros((0, 2))
    log_rets = np.diff(np.log(np.clip(closes, 1e-9, None)))
    # Rolling std of log returns as vol proxy
    vol = np.zeros_like(log_rets)
    for i in range(len(log_rets)):
        start = max(0, i - vol_lookback + 1)
        vol[i] = float(np.std(log_rets[start:i + 1])) if i > 0 else 0.0
    return np.column_stack([log_rets, vol])


def recent_regime_features(
    candles: Sequence[dict],
    *,
    vol_lookback: int = DEFAULT_VOL_LOOKBACK,
) -> np.ndarray | None:
    """Return the latest (log_return, rolling_vol) row for the live/BT gate.

    Soft-fails to ``None`` when fewer than 2 closes are available.
    """
    feats = _features_from_candles(candles, vol_lookback=vol_lookback)
    if len(feats) == 0:
        return None
    return feats[-1:]


def _label_state(mean_ret: float, mean_vol: float, vol_threshold: float) -> str:
    vol_side = "volatile" if mean_vol >= vol_threshold else "quiet"
    ret_side = "bull" if mean_ret >= 0 else "bear"
    return f"{ret_side}_{vol_side}"


def fit_regime_model(
    candles: Sequence[dict],
    *,
    n_states: int = DEFAULT_N_STATES,
    vol_lookback: int = DEFAULT_VOL_LOOKBACK,
) -> RegimeModel | None:
    """Fit a Gaussian mixture on (log_return, rolling_vol) → RegimeModel."""
    feats = _features_from_candles(candles, vol_lookback=vol_lookback)
    if len(feats) < MIN_FIT_SAMPLES:
        return None
    try:
        from sklearn.mixture import GaussianMixture
    except ImportError:
        logger.warning("sklearn unavailable — cannot fit regime model")
        return None

    n = min(n_states, max(2, len(feats) // 50))
    gm = GaussianMixture(n_components=n, covariance_type="full",
                          random_state=42, max_iter=100, n_init=3)
    try:
        gm.fit(feats)
    except Exception as exc:
        logger.warning("GaussianMixture fit failed: %s", exc)
        return None

    means = gm.means_  # (n, 2): [mean_ret, mean_vol]
    vols = means[:, 1]
    vol_threshold = float(np.median(vols)) if len(vols) else 0.0
    state_labels = tuple(
        _label_state(float(means[i, 0]), float(means[i, 1]), vol_threshold)
        for i in range(n)
    )
    return RegimeModel(
        means=tuple(tuple(float(x) for x in means[i]) for i in range(n)),
        covariances=tuple(gm.covariances_[i] for i in range(n)),
        weights=tuple(float(w) for w in gm.weights_),
        state_labels=state_labels,
        vol_threshold=vol_threshold,
        n_states=n,
    )


def predict_regime_posteriors(model: RegimeModel, features: np.ndarray) -> np.ndarray:
    """Soft posterior P(state | features) for a single feature row (1, 2)."""
    if model is None or len(model.weights) == 0:
        return np.zeros(0)
    if features.ndim == 1:
        features = features.reshape(1, -1)
    n = len(model.weights)
    log_probs = np.zeros(n)
    for i in range(n):
        mean = np.array(model.means[i])
        cov = model.covariances[i]
        try:
            inv = np.linalg.inv(cov)
            diff = features[0] - mean
            log_det = math.log(max(np.linalg.det(cov), 1e-12))
            log_probs[i] = math.log(model.weights[i]) - 0.5 * (
                diff @ inv @ diff + log_det + 2 * math.log(2 * math.pi)
            )
        except (np.linalg.LinAlgError, ValueError):
            log_probs[i] = math.log(max(model.weights[i], 1e-12))
    # softmax
    log_probs -= log_probs.max()
    probs = np.exp(log_probs)
    s = probs.sum()
    return probs / s if s > 0 else np.ones(n) / n


def regime_signal_scale(
    posteriors: np.ndarray,
    model: RegimeModel,
    signal_side: str,
) -> float:
    """Map regime posteriors to a confidence multiplier for the given side.

    Bull regimes boost BUY, dampen SELL. Bear regimes boost SELL, dampen BUY.
    Volatile regimes dampen both (uncertainty). The result is clamped to
    ``[MIN_REGIME_SCALE, MAX_REGIME_SCALE]`` so the gate never zeros or
    doubles a signal.
    """
    if model is None or len(posteriors) == 0 or len(model.state_labels) != len(posteriors):
        return 1.0
    side = str(signal_side or "").upper()
    score = 0.0
    for p, label in zip(posteriors, model.state_labels):
        is_bull = label.startswith("bull")
        is_bear = label.startswith("bear")
        is_volatile = label.endswith("volatile")
        if side == "BUY":
            directional = 1.0 if is_bull else (-1.0 if is_bear else 0.0)
        elif side == "SELL":
            directional = 1.0 if is_bear else (-1.0 if is_bull else 0.0)
        else:
            directional = 0.0
        vol_damp = 0.5 if is_volatile else 1.0
        score += p * directional * vol_damp
    # score ∈ [-1, 1]; map to [MIN_REGIME_SCALE, MAX_REGIME_SCALE] centred at 1.0
    scale = 1.0 + 0.5 * score
    return max(MIN_REGIME_SCALE, min(MAX_REGIME_SCALE, scale))


# ── Persistence + per-bot cache ────────────────────────────────────────────

_bot_models: dict[str, RegimeModel] = {}
_cache_lru = None


def _get_cache_lru():
    """MEMORY_CENTRIC_REVIEW #28 — bound LRU+TTL so a growing bot fleet cannot
    retain every fitted model for the life of the process. Disk stays the
    source of truth; eviction only drops the hot copy."""
    global _cache_lru
    if _cache_lru is None:
        from app.config import ML_MODEL_CACHE_MAX, ML_MODEL_CACHE_TTL_SEC
        from app.services.bots.model_store_lru import bind_dict_cache

        _cache_lru = bind_dict_cache(
            _bot_models,
            max_entries=ML_MODEL_CACHE_MAX,
            ttl_sec=ML_MODEL_CACHE_TTL_SEC,
        )
    return _cache_lru


def _default_path(bot_id: str) -> str:
    from app.config import DATA_DIR
    return os.path.join(DATA_DIR, "hmm_regime", f"{bot_id}.json")


def save_regime_model(bot_id: str, model: RegimeModel, *, path: str | None = None) -> None:
    target = path or _default_path(bot_id)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(model.to_dict(), fh, indent=2)
    os.replace(tmp, target)
    _bot_models[bot_id] = model
    _get_cache_lru().touch(bot_id)


def load_regime_model(bot_id: str, *, path: str | None = None) -> RegimeModel | None:
    if bot_id in _bot_models:
        _get_cache_lru().touch(bot_id)
        return _bot_models[bot_id]
    target = path or _default_path(bot_id)
    try:
        with open(target, "r", encoding="utf-8") as fh:
            model = RegimeModel.from_dict(json.load(fh))
        if model:
            _bot_models[bot_id] = model
            _get_cache_lru().touch(bot_id)
        return model
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Failed to load regime model %s: %s", target, exc)
        return None


def invalidate_regime_cache(bot_id: str | None = None) -> None:
    if bot_id is None:
        _get_cache_lru().clear()
        _bot_models.clear()
    else:
        _bot_models.pop(bot_id, None)
        _get_cache_lru().discard(bot_id)


# ── Live gate ──────────────────────────────────────────────────────────────


def apply_hmm_regime_gate(
    result: dict | None,
    config: dict | None,
    *,
    recent_features: np.ndarray | None = None,
) -> dict:
    """Soft-gate ML/TA signals via regime posteriors.

    Opt-in via ``hmm_regime_gate_enabled``. Scales ``confidence`` by the
    regime suitability multiplier. Falls back to identity when no model is
    fitted or features are missing — never hard-blocks.
    """
    if not isinstance(result, dict):
        return {"signal": "NONE"}
    cfg = config if isinstance(config, dict) else {}
    if not cfg.get("hmm_regime_gate_enabled"):
        return result
    signal = str(result.get("signal") or "NONE").upper()
    if signal not in ("BUY", "SELL"):
        return result
    bot_id = cfg.get("_bot_id") or cfg.get("bot_id")
    if not bot_id:
        return result
    model = load_regime_model(str(bot_id))
    if model is None or recent_features is None:
        return result
    try:
        feats = np.asarray(recent_features, dtype=np.float64)
        if feats.ndim == 1:
            feats = feats.reshape(1, -1)
        posteriors = predict_regime_posteriors(model, feats)
        scale = regime_signal_scale(posteriors, model, signal)
    except Exception as exc:
        logger.debug("HMM regime gate failed for %s: %s", bot_id, exc)
        return result
    out = dict(result)
    out["confidence"] = max(0.0, min(1.0, float(result.get("confidence") or 0.5) * scale))
    out["regime_scale"] = round(scale, 4)
    return out
