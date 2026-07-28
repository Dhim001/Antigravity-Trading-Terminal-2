"""Probability calibration + fractional-Kelly position sizing.

Phase 1.1 of the Signal Enhancement Plan.

Two concerns, kept separate on purpose:

1. **Temperature scaling** — fit a single scalar ``T`` on validation logits
   so that ``softmax(logit / T)`` matches empirical win rates. Cheap, robust,
   the standard post-hoc calibrator for binary classifiers (Platt / Guo 2017).

2. **Fractional-Kelly sizing** — given a *calibrated* win probability ``p`` and
   the win/loss payoff ratio ``b``, the full-Kelly fraction is
   ``f* = (b*p - (1-p)) / b``. We never bet full Kelly in production — a
   quarter-Kelly cap is the default and is overridable per-bot.

Both helpers are pure and side-effect free; the fitter persists ``T`` to a
small JSON next to the model artifact so the live path can load it without
re-fitting.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_KELLY_FRACTION = 0.25  # quarter-Kelly — standard defensive default
MIN_KELLY_FRACTION = 0.0
MAX_KELLY_FRACTION = 1.0
MIN_SIZE_SCALE = 0.25  # never scale below 25% of risk-based size
MAX_SIZE_SCALE = 2.00  # never scale above 200% of risk-based size
MIN_TEMPERATURE = 0.05
MAX_TEMPERATURE = 10.0
DEFAULT_TEMPERATURE = 1.0


# ---------------------------------------------------------------------------
# Temperature scaling
# ---------------------------------------------------------------------------


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def calibrate_probability(prob: float, temperature: float) -> float:
    """Apply temperature scaling to a probability.

    ``T > 1`` flattens (less confident); ``T < 1`` sharpens (more confident).
    We round-trip through logit space so the transform is symmetric around 0.5.
    """
    p = float(prob)
    if not math.isfinite(p):
        return 0.5
    p = max(1e-6, min(1.0 - 1e-6, p))
    T = float(temperature)
    if not math.isfinite(T) or T <= 0.0:
        return p
    logit = math.log(p / (1.0 - p))
    return _sigmoid(logit / T)


def fit_temperature(
    probs: Sequence[float],
    labels: Sequence[int],
    *,
    n_steps: int = 200,
    lr: float = 0.1,
) -> float:
    """Gradient-descent fit of ``T`` minimising NLL on validation pairs.

    Falls back to a coarse grid search if numpy is unavailable. Returns a
    temperature clamped to ``[MIN_TEMPERATURE, MAX_TEMPERATURE]``.
    """
    if not probs or not labels or len(probs) != len(labels):
        return DEFAULT_TEMPERATURE
    n = len(probs)
    try:
        import numpy as np  # type: ignore

        p = np.clip(np.asarray(probs, dtype=float), 1e-6, 1 - 1e-6)
        y = np.asarray(labels, dtype=float)
        logits = np.log(p / (1.0 - p))
        T = 1.0
        for _ in range(max(1, int(n_steps))):
            z = logits / T
            # stable sigmoid
            q = np.where(z >= 0, 1.0 / (1.0 + np.exp(-z)), np.exp(z) / (1.0 + np.exp(z)))
            q = np.clip(q, 1e-9, 1 - 1e-9)
            # dNLL/dT = sum((q - y) * (-logit / T^2)) ... but we just need the sign
            grad = float(np.sum((q - y) * (-logits)) / (T * T * n))
            T_new = T - lr * grad
            T_new = max(MIN_TEMPERATURE, min(MAX_TEMPERATURE, T_new))
            if abs(T_new - T) < 1e-4:
                T = T_new
                break
            T = T_new
        return float(T)
    except Exception:  # pragma: no cover — numpy missing
        # Grid search fallback
        best_T, best_nll = DEFAULT_TEMPERATURE, float("inf")
        for k in range(1, 100):
            T = 0.1 + k * 0.1
            nll = 0.0
            for pi, yi in zip(probs, labels):
                cp = calibrate_probability(float(pi), T)
                cp = max(1e-9, min(1 - 1e-9, cp))
                nll -= yi * math.log(cp) + (1 - yi) * math.log(1 - cp)
            if nll < best_nll:
                best_nll, best_T = nll, T
        return best_T


# ---------------------------------------------------------------------------
# Fractional-Kelly sizing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KellyInputs:
    p: float           # calibrated win probability
    b: float           # win/loss payoff ratio (avg_win / |avg_loss|)
    fraction: float = DEFAULT_KELLY_FRACTION
    min_p: float = 0.50  # below this, no edge → flat (scale 1.0, i.e. risk-based)


def kelly_fraction(p: float, b: float, *, fraction: float = DEFAULT_KELLY_FRACTION) -> float:
    """Full-Kelly fraction, scaled by ``fraction`` (quarter-Kelly by default).

    Returns the fraction of capital to risk. Negative results (no edge) are
    floored at 0 — we never short-size into a long signal.
    """
    p = float(p)
    b = float(b)
    if not math.isfinite(p) or not math.isfinite(b) or b <= 0.0:
        return 0.0
    p = max(0.0, min(1.0, p))
    f = (b * p - (1.0 - p)) / b
    f *= max(MIN_KELLY_FRACTION, min(MAX_KELLY_FRACTION, float(fraction)))
    return max(0.0, f)


def kelly_size_scale(
    p: float,
    b: float,
    *,
    fraction: float = DEFAULT_KELLY_FRACTION,
    min_p: float = 0.50,
) -> float:
    """Map a Kelly fraction to a *multiplier* on the risk-based quantity.

    The risk-based quantity already encodes ``risk_amount / stop_distance``.
    Kelly tells us what *fraction of capital* to risk; we translate that to a
    scale on the existing size by comparing ``f`` to the bot's base risk
    fraction (``RISK_PCT``). The base size corresponds to risking ``RISK_PCT``
    of equity; if Kelly says risk ``f`` of equity, the scale is ``f / RISK_PCT``.

    We clamp to ``[MIN_SIZE_SCALE, MAX_SIZE_SCALE]`` so a single bad
    calibration can't blow up or zero out a position. Below ``min_p`` we
    return 1.0 (use the risk-based size unchanged) — Kelly has no edge to add.
    """
    p = float(p)
    if not math.isfinite(p) or p < min_p:
        return 1.0
    f = kelly_fraction(p, b, fraction=fraction)
    if f <= 0.0:
        return 1.0
    # RISK_PCT lives in manager.py (0.01 = 1%). Import lazily to avoid cycles.
    try:
        from app.services.bots.manager import RISK_PCT
        base = float(RISK_PCT) or 0.01
    except Exception:
        base = 0.01
    scale = f / base
    return max(MIN_SIZE_SCALE, min(MAX_SIZE_SCALE, scale))


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@dataclass
class CalibrationBlob:
    temperature: float
    kelly_fraction: float
    fitted_samples: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0

    def to_dict(self) -> dict:
        return {
            "temperature": round(self.temperature, 6),
            "kelly_fraction": round(self.kelly_fraction, 4),
            "fitted_samples": int(self.fitted_samples),
            "avg_win": round(self.avg_win, 6),
            "avg_loss": round(self.avg_loss, 6),
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "CalibrationBlob":
        d = d or {}
        return cls(
            temperature=float(d.get("temperature") or DEFAULT_TEMPERATURE),
            kelly_fraction=float(d.get("kelly_fraction") or DEFAULT_KELLY_FRACTION),
            fitted_samples=int(d.get("fitted_samples") or 0),
            avg_win=float(d.get("avg_win") or 0.0),
            avg_loss=float(d.get("avg_loss") or 0.0),
        )


def save_calibration(path: str, blob: CalibrationBlob) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(blob.to_dict(), fh, indent=2)
    os.replace(tmp, path)


def load_calibration(path: str) -> CalibrationBlob | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return CalibrationBlob.from_dict(json.load(fh))
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Failed to load calibration %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Live-path helper
# ---------------------------------------------------------------------------

# Per-bot in-memory cache of the last loaded blob — avoids disk reads on every
# signal. Invalidate via ``invalidate_bot_cache`` after a retrain.
_bot_cache: dict[str, CalibrationBlob] = {}


def get_bot_calibration(bot_id: str, *, path: str | None = None) -> CalibrationBlob:
    if path and path in _bot_cache:
        return _bot_cache[path]
    if bot_id in _bot_cache and not path:
        return _bot_cache[bot_id]
    blob = load_calibration(path or _default_path(bot_id)) or CalibrationBlob(
        temperature=DEFAULT_TEMPERATURE, kelly_fraction=DEFAULT_KELLY_FRACTION
    )
    _bot_cache[path or bot_id] = blob
    return blob


def invalidate_bot_cache(bot_id: str | None = None) -> None:
    if bot_id is None:
        _bot_cache.clear()
    else:
        _bot_cache.pop(bot_id, None)


def _default_path(bot_id: str) -> str:
    from app.config import DATA_DIR

    return os.path.join(DATA_DIR, "calibration", f"{bot_id}.json")
