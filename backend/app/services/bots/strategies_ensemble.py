"""HYBRID_ENSEMBLE — weighted TA + ML + RL voting (proposal §7).

Combines a configurable TA strategy, an ML classifier (default ML_SIGNAL_BOOST),
and the PPO RL agent. Emits BUY/SELL only when the weighted confidence vote
clears ``ensemble_threshold``.

Adaptive weights: if ``ensemble_adaptive_weights`` is present on config
(e.g. written by post-trade learner), those override the static weights.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.bots.indicators import merge_strategy_config
from app.services.bots.ml_signal_gates import apply_ml_meta_label_gate
from app.services.bots.strategies import BaseStrategy

logger = logging.getLogger(__name__)

_ENSEMBLE_SELF = "HYBRID_ENSEMBLE"
_DEFAULT_TA = "MACD_RSI"
_DEFAULT_ML = "ML_SIGNAL_BOOST"
_DEFAULT_RL = "RL_PPO_AGENT"

_BLOCKED_CHILD = frozenset({
    "HYBRID_ENSEMBLE",
    "CUSTOM",
    "TICK_MOMENTUM",
    "TICK_MEAN_REVERT",
    "TICK_BREAKOUT",
})


def _norm_signal(raw: Any) -> str:
    sig = str(raw or "NONE").upper()
    if sig in ("BUY", "SELL", "NONE"):
        return sig
    # RL CLOSE / other exits do not count as entry votes
    return "NONE"


def _safe_conf(result: dict | None, default: float = 0.5) -> float:
    if not isinstance(result, dict):
        return default
    try:
        c = float(result.get("confidence"))
    except (TypeError, ValueError):
        return default
    if c != c:  # NaN
        return default
    return max(0.0, min(1.0, c))


def _resolve_weights(cfg: dict) -> tuple[float, float, float]:
    adaptive = cfg.get("ensemble_adaptive_weights")
    if isinstance(adaptive, dict):
        try:
            ta = float(adaptive.get("ta", adaptive.get("ensemble_weight_ta", 0.3)))
            ml = float(adaptive.get("ml", adaptive.get("ensemble_weight_ml", 0.4)))
            rl = float(adaptive.get("rl", adaptive.get("ensemble_weight_rl", 0.3)))
            total = ta + ml + rl
            if total > 1e-9:
                return ta / total, ml / total, rl / total
        except (TypeError, ValueError):
            pass
    try:
        ta = float(cfg.get("ensemble_weight_ta", 0.3))
        ml = float(cfg.get("ensemble_weight_ml", 0.4))
        rl = float(cfg.get("ensemble_weight_rl", 0.3))
    except (TypeError, ValueError):
        return 0.3, 0.4, 0.3
    total = ta + ml + rl
    if total <= 1e-9:
        return 0.3, 0.4, 0.3
    return ta / total, ml / total, rl / total


def _child_config(parent: dict, child_id: str) -> dict:
    """Clone parent config for a child strategy; pin model_symbol if set."""
    cfg = dict(parent or {})
    # Children must not recurse into another ensemble
    cfg.pop("ta_strategy", None)
    cfg.pop("ml_strategy", None)
    cfg.pop("rl_strategy", None)
    # Avoid ensemble-only keys confusing child merge defaults
    for k in (
        "ensemble_threshold",
        "ensemble_weight_ta",
        "ensemble_weight_ml",
        "ensemble_weight_rl",
        "ensemble_adaptive_weights",
        "ensemble_require_agreement",
    ):
        cfg.pop(k, None)
    cfg["_ensemble_parent"] = _ENSEMBLE_SELF
    cfg["_ensemble_child"] = child_id
    return cfg


def _safe_get_strategy(name: str, config: dict) -> BaseStrategy | None:
    from app.services.bots.strategies import get_strategy, normalize_strategy_name

    key = normalize_strategy_name(name or "")
    if not key or key in _BLOCKED_CHILD:
        return None
    try:
        return get_strategy(key, config)
    except Exception:
        logger.exception("Hybrid ensemble failed to load child strategy %s", key)
        return None


class HybridEnsembleStrategy(BaseStrategy):
    """Weighted vote across TA + ML + RL signal generators."""

    def __init__(self, config: dict):
        super().__init__(config)
        self._cfg = merge_strategy_config(_ENSEMBLE_SELF, config or {})

        ta_id = str(self._cfg.get("ta_strategy") or _DEFAULT_TA).upper()
        ml_id = str(self._cfg.get("ml_strategy") or _DEFAULT_ML).upper()
        rl_id = str(self._cfg.get("rl_strategy") or _DEFAULT_RL).upper()
        if ta_id in _BLOCKED_CHILD:
            ta_id = _DEFAULT_TA
        if ml_id in _BLOCKED_CHILD:
            ml_id = _DEFAULT_ML
        if rl_id in _BLOCKED_CHILD:
            rl_id = _DEFAULT_RL

        self._ta_id = ta_id
        self._ml_id = ml_id
        self._rl_id = rl_id

        self._ta = _safe_get_strategy(ta_id, _child_config(self._cfg, ta_id))
        self._ml = _safe_get_strategy(ml_id, _child_config(self._cfg, ml_id))
        self._rl = _safe_get_strategy(rl_id, _child_config(self._cfg, rl_id))

    def evaluate(self, df_row) -> dict:
        w_ta, w_ml, w_rl = _resolve_weights(self._cfg)
        threshold = float(self._cfg.get("ensemble_threshold", 0.5))
        require_agree = bool(self._cfg.get("ensemble_require_agreement", False))

        components: list[tuple[str, float, dict]] = []
        for name, weight, strat in (
            ("ta", w_ta, self._ta),
            ("ml", w_ml, self._ml),
            ("rl", w_rl, self._rl),
        ):
            if strat is None or weight <= 0:
                components.append((name, weight, {"signal": "NONE", "confidence": 0.0}))
                continue
            try:
                raw = strat.evaluate(df_row)
            except Exception:
                logger.debug("Ensemble child %s evaluate failed", name, exc_info=True)
                raw = {"signal": "NONE", "confidence": 0.0}
            if not isinstance(raw, dict):
                raw = {"signal": "NONE", "confidence": 0.0}
            components.append((name, weight, raw))

        votes = {"BUY": 0.0, "SELL": 0.0, "NONE": 0.0}
        detail = {}
        actionable = []
        for name, weight, result in components:
            sig = _norm_signal(result.get("signal"))
            conf = _safe_conf(result)
            votes[sig] += weight * conf
            detail[name] = {
                "signal": sig,
                "confidence": round(conf, 4),
                "weight": round(weight, 4),
                "contribution": round(weight * conf, 4),
                "raw_signal": result.get("signal"),
            }
            if sig in ("BUY", "SELL"):
                actionable.append(sig)

        # Optional hard consensus: need ≥2 distinct components agreeing on side
        if require_agree and len(actionable) >= 1:
            from collections import Counter

            counts = Counter(actionable)
            top_side, top_n = counts.most_common(1)[0]
            if top_n < 2:
                return {
                    "signal": "NONE",
                    "confidence": 0.0,
                    "reject_reason": "ensemble_no_agreement",
                    "reject_detail": f"only {top_n} component(s) on {top_side}",
                    "ensemble": detail,
                    "votes": {k: round(v, 4) for k, v in votes.items()},
                    "model_type": "hybrid_ensemble",
                }

        best = max(votes, key=votes.get)
        best_score = float(votes[best])

        atr = 0.0
        if isinstance(df_row, dict):
            try:
                atr = float(df_row.get("ATR_14") or df_row.get("ATRr_14") or 0)
            except (TypeError, ValueError):
                atr = 0.0

        if best in ("BUY", "SELL") and best_score > threshold:
            out = {
                "signal": best,
                "confidence": round(min(best_score, 1.0), 4),
                "stop_loss_distance": atr * 1.5 if atr > 0 else None,
                "ensemble": detail,
                "votes": {k: round(v, 4) for k, v in votes.items()},
                "model_type": "hybrid_ensemble",
                "ta_strategy": self._ta_id,
                "ml_strategy": self._ml_id,
                "rl_strategy": self._rl_id,
            }
            return apply_ml_meta_label_gate(out, df_row, self._cfg)

        return {
            "signal": "NONE",
            "confidence": round(best_score, 4),
            "reject_reason": "ensemble_below_threshold" if best in ("BUY", "SELL") else "ensemble_none",
            "reject_detail": f"best={best} score={best_score:.3f} threshold={threshold:.3f}",
            "ensemble": detail,
            "votes": {k: round(v, 4) for k, v in votes.items()},
            "model_type": "hybrid_ensemble",
        }
