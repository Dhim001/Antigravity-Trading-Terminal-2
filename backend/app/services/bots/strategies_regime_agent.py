"""REGIME_STRATEGY_AGENT — deterministic regime → exclusive TA specialist."""

from __future__ import annotations

import logging
import math
from typing import Any

from app.services.bots.indicators import merge_strategy_config
from app.services.bots.regime_classify import (
    ALLOWLISTED_CHILDREN,
    classify_atr_adx_regime,
    resolve_regime_strategy_map,
)
from app.services.bots.strategies import BaseStrategy

logger = logging.getLogger(__name__)

_SELF = "REGIME_STRATEGY_AGENT"
DEFAULT_FALLBACK = "BRS_SCALPING"
_BLOCKED_CHILD = frozenset({
    "REGIME_STRATEGY_AGENT",
    "HYBRID_ENSEMBLE",
    "CUSTOM",
    "TICK_MOMENTUM",
    "TICK_MEAN_REVERT",
    "TICK_BREAKOUT",
    "CHART_AGENT",
    "ABSORPTION_AGENT",
})

# Only these parent keys are forwarded into specialist configs.
# Dumping the full REGIME blob polluted children and mismatched indicator columns.
_SHARED_CHILD_KEYS = frozenset({
    "rsi_length",
    "atr_length",
    "adx_length",
    "stoch_k",
    "stoch_d",
    "stoch_smooth",
    "bb_length",
    "bb_std",
    "st_length",
    "st_multiplier",
    "rsi_oversold",
    "rsi_overbought",
    "stoch_oversold",
    "stoch_overbought",
    "adx_threshold",
    "block_elevated_vol",
    "use_rsi_confirmation",
    "rsi_overbought_gate",
    "rsi_oversold_gate",
    "direction_mode",
    "symbol",
    "timeframe",
})


def _is_missing(val: Any) -> bool:
    if val is None:
        return True
    try:
        return isinstance(val, float) and math.isnan(val)
    except Exception:
        return False


def _child_config(parent: dict, child_id: str) -> dict:
    """Build a clean specialist config (child defaults + intentional overrides)."""
    parent = parent or {}
    overrides = {k: parent[k] for k in _SHARED_CHILD_KEYS if k in parent and parent[k] is not None}
    nested = parent.get("child_config")
    if isinstance(nested, dict):
        patch = nested.get(child_id) or nested.get(str(child_id).lower())
        if isinstance(patch, dict):
            overrides = {**overrides, **patch}
    return merge_strategy_config(child_id, overrides)


def _safe_get_strategy(name: str, config: dict):
    from app.services.bots.strategies import get_strategy, normalize_strategy_name

    key = normalize_strategy_name(name or "")
    if not key or key in _BLOCKED_CHILD:
        return None
    # Hard allowlist — map overrides outside the TA arsenal need indicator union work.
    if key not in ALLOWLISTED_CHILDREN:
        return None
    try:
        return get_strategy(key, config)
    except Exception:
        logger.exception("REGIME_STRATEGY_AGENT failed to load child %s", key)
        return None


class RegimeStrategyAgent(BaseStrategy):
    """Classify ATR/ADX regime with hysteresis; evaluate one TA specialist."""

    def __init__(self, config: dict):
        super().__init__(config)
        self._cfg = merge_strategy_config(_SELF, config or {})
        self._map = resolve_regime_strategy_map(self._cfg)
        # Clamp map values to allowlisted children
        cleaned = {}
        for regime, strat in self._map.items():
            key = str(strat or "").upper()
            cleaned[regime] = key if key in ALLOWLISTED_CHILDREN else DEFAULT_FALLBACK
        self._map = cleaned
        try:
            self._hysteresis = max(1, int(self._cfg.get("regime_hysteresis_bars", 3)))
        except (TypeError, ValueError):
            self._hysteresis = 3
        try:
            self._min_hold = max(0, int(self._cfg.get("regime_min_hold_bars", 15)))
        except (TypeError, ValueError):
            self._min_hold = 15
        try:
            self._atr_ratio = float(self._cfg.get("atr_ratio_elevated", 1.5))
        except (TypeError, ValueError):
            self._atr_ratio = 1.5
        try:
            self._adx_trend = float(self._cfg.get("adx_trend", 25))
        except (TypeError, ValueError):
            self._adx_trend = 25.0
        try:
            self._atr_len = int(self._cfg.get("atr_length", 14))
        except (TypeError, ValueError):
            self._atr_len = 14
        try:
            self._adx_len = int(self._cfg.get("adx_length", 14))
        except (TypeError, ValueError):
            self._adx_len = 14

        # Instance-local hysteresis — never module/class globals (WF/sweep safe).
        self._active_regime: str | None = None
        self._pending_regime: str | None = None
        self._pending_streak: int = 0
        self._bars_since_switch: int = 0

        self._children: dict[str, Any] = {}
        for strat in sorted(set(self._map.values()) | set(ALLOWLISTED_CHILDREN)):
            child = _safe_get_strategy(strat, _child_config(self._cfg, strat))
            if child is not None:
                self._children[strat] = child

    def _update_regime(self, observed: str) -> tuple[str, bool]:
        """Apply hysteresis + min-hold; return (active_regime, switched)."""
        switched = False
        if self._active_regime is None:
            self._active_regime = observed
            self._pending_regime = observed
            self._pending_streak = self._hysteresis
            self._bars_since_switch = 0
            return self._active_regime, False  # init is not a flip

        self._bars_since_switch += 1

        if observed == self._active_regime:
            self._pending_regime = observed
            self._pending_streak = 0
            return self._active_regime, False

        if self._bars_since_switch < self._min_hold:
            self._pending_regime = observed
            self._pending_streak = 0
            return self._active_regime, False

        if observed == self._pending_regime:
            self._pending_streak += 1
        else:
            self._pending_regime = observed
            self._pending_streak = 1

        if self._pending_streak >= self._hysteresis:
            self._active_regime = observed
            self._pending_streak = 0
            self._bars_since_switch = 0
            switched = True

        return self._active_regime, switched

    def evaluate(self, df_row: dict) -> dict:
        observed = classify_atr_adx_regime(
            df_row,
            atr_length=self._atr_len,
            adx_length=self._adx_len,
            atr_ratio_elevated=self._atr_ratio,
            adx_trend=self._adx_trend,
        )
        active, switched = self._update_regime(observed)
        child_id = self._map.get(active) or DEFAULT_FALLBACK
        # If VWAP specialist is selected but VWAP is missing, fall back to BRS
        # so elevated_vol windows are not dead air.
        if child_id == "VWAP_PULLBACK" and _is_missing((df_row or {}).get("VWAP")):
            child_id = DEFAULT_FALLBACK

        child = self._children.get(child_id)
        if child is None:
            child = _safe_get_strategy(child_id, _child_config(self._cfg, child_id))
            if child is not None:
                self._children[child_id] = child

        meta_reasons = [
            f"regime={active}",
            f"selected={child_id}",
            f"observed={observed}",
            f"bars_since_switch={self._bars_since_switch}",
            f"streak={self._pending_streak}",
        ]
        if switched:
            meta_reasons.append(f"switched_to={active}")

        base_meta = {
            "regime": active,
            "observed_regime": observed,
            "selected_strategy": child_id,
            "regime_streak": self._pending_streak,
            "bars_since_switch": self._bars_since_switch,
            "regime_switched": switched,
        }

        if child is None:
            return {
                "signal": "NONE",
                "reject_reason": f"regime child unavailable: {child_id}",
                "reasons": meta_reasons,
                **base_meta,
            }

        try:
            result = child.evaluate(df_row) or {}
        except Exception as exc:
            logger.debug("REGIME_STRATEGY_AGENT child %s error: %s", child_id, exc)
            return {
                "signal": "NONE",
                "reject_reason": f"child evaluate error: {child_id}",
                "reasons": meta_reasons,
                **base_meta,
            }

        if not isinstance(result, dict):
            return {
                "signal": "NONE",
                "reject_reason": "child returned non-dict",
                "reasons": meta_reasons,
                **base_meta,
            }

        out = dict(result)
        child_reasons = out.get("reasons")
        reasons = list(meta_reasons)
        if isinstance(child_reasons, list):
            reasons.extend(str(r) for r in child_reasons)
        elif child_reasons:
            reasons.append(str(child_reasons))
        out["reasons"] = reasons
        out.update(base_meta)
        if not out.get("signal"):
            out["signal"] = "NONE"

        # Persist insight_snapshot so the manager can store it on the trade row.
        # The child may already set one (e.g. CHART_AGENT); if not, build a compact
        # snapshot from the regime-agent's own output so the calibration/meta-label
        # pipeline has regime + child strategy context for training.
        if not out.get("insight_snapshot"):
            out["insight_snapshot"] = {
                "signal": out.get("signal"),
                "score": out.get("score"),
                "confidence": out.get("confidence"),
                "reasons": reasons[:5],
                "sub_reports": out.get("sub_reports"),
                "regime": base_meta["regime"],
                "observed_regime": base_meta["observed_regime"],
                "selected_strategy": base_meta["selected_strategy"],
                "regime_streak": base_meta["regime_streak"],
                "bars_since_switch": base_meta["bars_since_switch"],
                "regime_switched": base_meta["regime_switched"],
                "bar_time": df_row.get("time") if isinstance(df_row, dict) else None,
                "timeframe": self._cfg.get("timeframe") or "1m",
            }

        return out
