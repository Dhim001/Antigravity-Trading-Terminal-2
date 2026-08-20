"""Split-conformal prediction gate for ML signal entries.

Phase 1.2 of the Signal Enhancement Plan.

The idea: a calibrated classifier produces ``p_long`` / ``p_short``. We want a
data-driven confidence threshold — not a hardcoded ``0.55`` — that admits
signals only when the conformal prediction set is a *singleton*.

Split-conformal (Vovk / Lei) for binary classification:

1. On a held-out calibration set, compute nonconformity scores
   ``s_i = 1 - p_i[y_i_true]``.
2. The conformal quantile ``q_hat`` at coverage ``1 - alpha`` is the
   ``ceil((n+1)*(1-alpha))/n`` empirical quantile of those scores.
3. For a new sample, class ``k`` is in the prediction set iff
   ``1 - p[k] <= q_hat``, i.e. ``p[k] >= 1 - q_hat``.
4. Gate accepts a signal iff exactly one class is in the set. Both in set
   → ambiguous (reject). Neither in set → too uncertain (reject).

This gives a per-model, per-symbol confidence floor learned from OOS data,
replacing the hardcoded ``min_confidence`` knob for ML strategies. It is
opt-in via ``conformal_gate_enabled`` and falls back to the legacy gate when
the calibration set is too small.

MAPIE is *optional* — if installed we delegate the quantile fit to it; the
in-tree implementation covers the common case without adding a dependency.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger(__name__)

DEFAULT_ALPHA = 0.10           # 90% coverage — standard for split-conformal
MIN_CALIB_SAMPLES = 30         # below this, refuse to gate (too noisy)
MIN_PROB_FALLBACK = 0.55       # legacy default when conformal can't gate


@dataclass
class ConformalCalibration:
    q_hat: float               # conformal quantile of nonconformity scores
    threshold: float           # 1 - q_hat — the effective confidence floor
    n: int                     # calibration sample count
    alpha: float
    scores: tuple[float, ...] = ()

    def to_dict(self) -> dict:
        return {
            "q_hat": round(self.q_hat, 6),
            "threshold": round(self.threshold, 6),
            "n": int(self.n),
            "alpha": float(self.alpha),
            "scores": list(self.scores),
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "ConformalCalibration | None":
        if not d or not isinstance(d, dict):
            return None
        try:
            scores = tuple(float(s) for s in (d.get("scores") or []))
            return cls(
                q_hat=float(d.get("q_hat") or 0.0),
                threshold=float(d.get("threshold") or 0.0),
                n=int(d.get("n") or 0),
                alpha=float(d.get("alpha") or DEFAULT_ALPHA),
                scores=scores,
            )
        except (TypeError, ValueError):
            return None


def _nonconformity_scores(
    probs: Sequence[float], labels: Sequence[int]
) -> tuple[float, ...]:
    """Score = 1 - p[true_label]. Higher = more nonconforming."""
    out: list[float] = []
    for p, y in zip(probs, labels):
        p = max(0.0, min(1.0, float(p)))
        # label convention: 1 = positive class (long), 0 = negative (short/flat)
        p_true = p if int(y) == 1 else (1.0 - p)
        out.append(1.0 - p_true)
    return tuple(out)


def _conformal_quantile(scores: Sequence[float], alpha: float) -> float:
    """Split-conformal quantile: ceil((n+1)*(1-alpha))/n empirical quantile."""
    s = sorted(scores)
    n = len(s)
    if n == 0:
        return 1.0
    rank = math.ceil((n + 1) * (1.0 - alpha))
    rank = max(1, min(n, rank))
    return float(s[rank - 1])


def fit_conformal(
    probs: Sequence[float],
    labels: Sequence[int],
    *,
    alpha: float = DEFAULT_ALPHA,
    min_samples: int = MIN_CALIB_SAMPLES,
) -> ConformalCalibration | None:
    """Fit split-conformal calibration from validation (probs, labels).

    Returns ``None`` when there isn't enough data — caller should fall back
    to the legacy fixed-threshold gate.
    """
    if probs is None or labels is None or len(probs) != len(labels):
        return None
    if len(probs) < max(1, int(min_samples)):
        return None
    scores = _nonconformity_scores(probs, labels)
    q_hat = _conformal_quantile(scores, alpha)
    threshold = max(0.0, min(1.0, 1.0 - q_hat))
    return ConformalCalibration(
        q_hat=q_hat,
        threshold=threshold,
        n=len(scores),
        alpha=float(alpha),
        scores=scores,
    )


@dataclass
class ConformalVerdict:
    accept: bool
    side: str | None           # "LONG" | "SHORT" | None
    reason: str                 # "singleton_long" | "ambiguous" | "neither" | "no_calibration"
    threshold: float
    p_long: float
    p_short: float


def conformal_verdict(
    p_long: float,
    p_short: float,
    cal: ConformalCalibration | None,
) -> ConformalVerdict:
    """Decide whether a conformal prediction set admits exactly one side.

    For binary directional signals we expect ``p_long + p_short ≈ 1``; we
    still treat them independently so a "flat" model (both low) is rejected
    as ``neither`` rather than silently coerced to a side.
    """
    pl = max(0.0, min(1.0, float(p_long)))
    ps = max(0.0, min(1.0, float(p_short)))
    if cal is None or cal.n == 0:
        return ConformalVerdict(
            accept=False, side=None, reason="no_calibration",
            threshold=0.0, p_long=pl, p_short=ps,
        )
    thr = cal.threshold
    long_in = pl >= thr
    short_in = ps >= thr
    if long_in and short_in:
        return ConformalVerdict(
            accept=False, side=None, reason="ambiguous",
            threshold=thr, p_long=pl, p_short=ps,
        )
    if not long_in and not short_in:
        return ConformalVerdict(
            accept=False, side=None, reason="neither",
            threshold=thr, p_long=pl, p_short=ps,
        )
    side = "LONG" if long_in else "SHORT"
    return ConformalVerdict(
        accept=True, side=side, reason=f"singleton_{side.lower()}",
        threshold=thr, p_long=pl, p_short=ps,
    )


# ---------------------------------------------------------------------------
# Persistence + per-bot cache
# ---------------------------------------------------------------------------

_bot_cal: dict[str, ConformalCalibration] = {}
_cache_lru = None


def _get_cache_lru():
    """MEMORY_CENTRIC_REVIEW #28 — bound LRU+TTL so a growing bot fleet cannot
    retain every calibration for the life of the process. Disk stays the
    source of truth; eviction only drops the hot copy."""
    global _cache_lru
    if _cache_lru is None:
        from app.config import ML_MODEL_CACHE_MAX, ML_MODEL_CACHE_TTL_SEC
        from app.services.bots.model_store_lru import bind_dict_cache

        _cache_lru = bind_dict_cache(
            _bot_cal,
            max_entries=ML_MODEL_CACHE_MAX,
            ttl_sec=ML_MODEL_CACHE_TTL_SEC,
        )
    return _cache_lru


def _default_path(bot_id: str) -> str:
    from app.config import DATA_DIR

    return os.path.join(DATA_DIR, "conformal", f"{bot_id}.json")


def save_conformal(bot_id: str, cal: ConformalCalibration, *, path: str | None = None) -> None:
    target = path or _default_path(bot_id)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cal.to_dict(), fh, indent=2)
    os.replace(tmp, target)
    _bot_cal[bot_id] = cal
    _get_cache_lru().touch(bot_id)


def load_conformal(bot_id: str, *, path: str | None = None) -> ConformalCalibration | None:
    if bot_id in _bot_cal:
        _get_cache_lru().touch(bot_id)
        return _bot_cal[bot_id]
    target = path or _default_path(bot_id)
    try:
        with open(target, "r", encoding="utf-8") as fh:
            cal = ConformalCalibration.from_dict(json.load(fh))
        if cal:
            _bot_cal[bot_id] = cal
            _get_cache_lru().touch(bot_id)
        return cal
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Failed to load conformal calibration %s: %s", target, exc)
        return None


def invalidate_conformal_cache(bot_id: str | None = None) -> None:
    if bot_id is None:
        _get_cache_lru().clear()
        _bot_cal.clear()
    else:
        _bot_cal.pop(bot_id, None)
        _get_cache_lru().discard(bot_id)


# ---------------------------------------------------------------------------
# Adaptive recalibration (AI-FT-PTL-001 §4.5, P1 #7)
# ---------------------------------------------------------------------------


def recalibrate_conformal_gate(bot_id: str) -> dict:
    """Recompute ``q_hat`` from recent live predictions vs outcomes (EMA).

    Reads the rolling (predicted_prob, actual_win) tracker, fits a fresh
    conformal quantile over the last ``CONFORMAL_RECALIB_WINDOW`` pairs, then
    blends it into the persisted calibration via exponential smoothing:
    ``q_hat_new = (1-α)·q_hat_old + α·q_hat_recent``. Persists the updated
    calibration alongside the model artifacts. Never raises.

    Returns a small status dict (``updated`` False when data is insufficient).
    """
    try:
        from app.config import (
            CONFORMAL_RECALIB_ENABLED,
            CONFORMAL_RECALIB_EMA_ALPHA,
            CONFORMAL_RECALIB_WINDOW,
        )
        from app.services.bots.meta_label_operational import _rolling_predictions

        if not CONFORMAL_RECALIB_ENABLED:
            return {"updated": False, "reason": "disabled"}

        preds = list(_rolling_predictions.get(bot_id) or [])[-int(CONFORMAL_RECALIB_WINDOW):]
        if len(preds) < MIN_CALIB_SAMPLES:
            return {
                "updated": False,
                "reason": f"insufficient outcomes ({len(preds)} < {MIN_CALIB_SAMPLES})",
                "n": len(preds),
            }

        probs = [p["predicted"] for p in preds]
        labels = [1 if p["actual"] else 0 for p in preds]
        recent = fit_conformal(probs, labels, alpha=DEFAULT_ALPHA, min_samples=MIN_CALIB_SAMPLES)
        if recent is None:
            return {"updated": False, "reason": "fit_failed", "n": len(preds)}

        existing = load_conformal(bot_id)
        alpha = max(0.0, min(1.0, float(CONFORMAL_RECALIB_EMA_ALPHA)))
        if existing is not None and existing.n > 0:
            q_hat = (1.0 - alpha) * existing.q_hat + alpha * recent.q_hat
        else:
            q_hat = recent.q_hat
        q_hat = max(0.0, min(1.0, q_hat))

        updated = ConformalCalibration(
            q_hat=q_hat,
            threshold=max(0.0, min(1.0, 1.0 - q_hat)),
            n=recent.n,
            alpha=recent.alpha,
            scores=recent.scores,
        )
        save_conformal(bot_id, updated)
        logger.info(
            "Conformal gate recalibrated for %s: q_hat %.4f → %.4f (n=%d)",
            bot_id,
            existing.q_hat if existing else float("nan"),
            updated.q_hat,
            updated.n,
        )
        return {
            "updated": True,
            "q_hat": round(updated.q_hat, 6),
            "threshold": round(updated.threshold, 6),
            "n": updated.n,
        }
    except Exception as exc:
        logger.debug("recalibrate_conformal_gate failed for %s: %s", bot_id, exc)
        return {"updated": False, "reason": "error"}


# ---------------------------------------------------------------------------
# Live gate — drop-in companion to apply_ml_meta_label_gate
# ---------------------------------------------------------------------------


def apply_conformal_gate(
    result: dict | None,
    config: dict | None,
) -> dict:
    """Gate ML BUY/SELL signals via the conformal prediction set.

    Opt-in via ``conformal_gate_enabled``. Falls back to the legacy
    ``min_confidence`` floor when no calibration is available, so enabling this
    never silently disables the existing gate.
    """
    if not isinstance(result, dict):
        return {"signal": "NONE"}
    cfg = config if isinstance(config, dict) else {}
    signal = str(result.get("signal") or "NONE").upper()
    if signal not in ("BUY", "SELL"):
        return result
    if not cfg.get("conformal_gate_enabled"):
        return result
    if cfg.get("_wf_mode") or cfg.get("skip_conformal_gate"):
        return result

    bot_id = cfg.get("_bot_id") or cfg.get("bot_id")
    if not bot_id:
        return result

    cal = load_conformal(str(bot_id))
    conf = float(result.get("confidence") or 0.0)
    # ML strategies emit a single confidence; treat it as p(side they picked).
    # The opposing side gets the residual so the gate sees both probabilities.
    p_picked = max(0.0, min(1.0, conf))
    p_other = 1.0 - p_picked
    if signal == "BUY":
        p_long, p_short = p_picked, p_other
    else:
        p_long, p_short = p_other, p_picked

    verdict = conformal_verdict(p_long, p_short, cal)
    if verdict.accept:
        return result

    # No calibration → fall back to legacy min_confidence rather than blocking.
    if verdict.reason == "no_calibration":
        try:
            min_conf = float(cfg.get("min_confidence") or MIN_PROB_FALLBACK)
        except (TypeError, ValueError):
            min_conf = MIN_PROB_FALLBACK
        if conf >= min_conf:
            return result
        reject = f"conformal_fallback: confidence {conf:.2%} < min_confidence {min_conf:.2%}"
    else:
        reject = f"conformal_gate: {verdict.reason} (threshold={verdict.threshold:.2%})"

    out = dict(result)
    out["signal"] = "NONE"
    out["raw_signal"] = signal
    out["reject_reason"] = "conformal_gate"
    out["reject_detail"] = reject
    return out
