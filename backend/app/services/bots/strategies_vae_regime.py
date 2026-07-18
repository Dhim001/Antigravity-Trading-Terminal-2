"""VAE_REGIME_DETECTOR strategy — anomaly-driven regime change detector.

Uses a trained VAE to compute anomaly scores on each bar's features.

Standalone mode: BUY/SELL when anomaly + RSI/MACD momentum align.

Meta-layer (proposal §2.6): other strategies opt in via
`vae_regime_gate_enabled` or `filter_strategy=VAE_REGIME_DETECTOR` /
`filter_mode=REGIME_GATE`. Shared helper `assess_vae_regime_for_meta`
feeds PreTradeIntel, StrategyFilter, runtime/backtest gates, and
RegimeRotationAgent.
  - Normal (score < anomaly_threshold): pass-through
  - Elevated + momentum: amplify (confirm / faster rotate)
  - Elevated + no direction: caution (reduce size)
  - High (score > suppress_threshold): suppress entries
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.services.bots.indicators import merge_strategy_config
from app.services.bots.ml_feature_engineering import (
    bar_to_signal_features,
    signal_features_to_vector,
)
from app.services.bots.ml_vae_regime import get_vae_store
from app.services.bots.strategies import BaseStrategy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VaeRegimeAssessment:
    """Meta-layer regime verdict for gates / PreTrade / rotation."""

    anomaly_score: float | None
    regime: str  # normal | anomalous | unstable | unknown
    regime_action: str  # normal | amplify | caution | suppress | skip
    reason: str
    model_available: bool


def _truthy_flag(value: Any) -> bool | None:
    """Return True/False for explicit flags, None if unset."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return None


def vae_regime_gate_enabled(bot_config: dict | None) -> bool:
    """Whether the VAE meta-layer gate should run for this bot.

    Explicit `vae_regime_gate_enabled` wins. Otherwise auto-on when
    `filter_strategy` is VAE_REGIME_DETECTOR (REGIME_GATE composition).
    """
    cfg = bot_config or {}
    flag = _truthy_flag(cfg.get("vae_regime_gate_enabled"))
    if flag is not None:
        return flag
    filt = str(cfg.get("filter_strategy") or "").strip().upper()
    return filt == "VAE_REGIME_DETECTOR"


def _resolve_vae_symbol(symbol: str | None, row: dict | None, config: dict | None) -> str:
    cfg = config or {}
    raw = (
        cfg.get("model_symbol")
        or (symbol or "")
        or (row or {}).get("_symbol")
        or cfg.get("symbol")
        or ""
    )
    return str(raw).strip().upper()


def assess_vae_regime_for_meta(
    symbol: str,
    row: dict,
    *,
    lookback_rows: list[dict] | None = None,
    config: dict | None = None,
) -> VaeRegimeAssessment:
    """Score current bar and map to suppress / amplify / caution / normal.

    Soft-fails (action=skip) when no model or scoring fails — callers must
    not block entries on skip.
    """
    cfg = merge_strategy_config("VAE_REGIME_DETECTOR", config or {})
    sym = _resolve_vae_symbol(symbol, row, cfg)
    if not sym or not isinstance(row, dict):
        return VaeRegimeAssessment(
            None, "unknown", "skip", "missing symbol or row", False
        )

    history = list(lookback_rows or [])
    # Prefer lookback excluding current if last row is the same bar.
    if history and history[-1] is row:
        lookback = history[:-1]
    else:
        lookback = history

    try:
        features = bar_to_signal_features(row, lookback_rows=lookback[-24:] if lookback else None)
        feat_vec = signal_features_to_vector(features)
    except Exception as exc:
        logger.debug("VAE feature extract failed for %s: %s", sym, exc)
        return VaeRegimeAssessment(None, "unknown", "skip", f"features: {exc}", False)

    store = get_vae_store()
    pinned = cfg.get("model_version") or None
    score = store.anomaly_score(sym, feat_vec, model_version=pinned or None)
    if score is None:
        return VaeRegimeAssessment(
            None, "unknown", "skip", "no VAE model or score unavailable", False
        )

    anomaly_thresh = float(cfg.get("anomaly_threshold", 2.0))
    suppress_thresh = float(cfg.get("suppress_threshold", 3.5))
    try:
        rsi = float(row.get("RSI_14") or 50)
    except (TypeError, ValueError):
        rsi = 50.0
    try:
        macd_h = float(row.get("MACDh_12_26_9") or 0)
    except (TypeError, ValueError):
        macd_h = 0.0

    if score > suppress_thresh:
        return VaeRegimeAssessment(
            score,
            "unstable",
            "suppress",
            f"VAE unstable score={score:.2f} > {suppress_thresh}",
            True,
        )

    if score > anomaly_thresh:
        bullish = rsi > 60 and macd_h > 0
        bearish = rsi < 40 and macd_h < 0
        if bullish or bearish:
            return VaeRegimeAssessment(
                score,
                "anomalous",
                "amplify",
                f"VAE anomalous score={score:.2f} with momentum",
                True,
            )
        return VaeRegimeAssessment(
            score,
            "anomalous",
            "caution",
            f"VAE anomalous score={score:.2f} without clear direction",
            True,
        )

    return VaeRegimeAssessment(
        score,
        "normal",
        "normal",
        f"VAE normal score={score:.2f}",
        True,
    )


class VaeRegimeStrategy(BaseStrategy):
    """VAE-based regime change detector and signal modulator.

    When used standalone, generates signals based on anomaly scores
    combined with momentum indicators. Meta-layer consumers should prefer
    `assess_vae_regime_for_meta` rather than instantiating this class.

    Config:
        anomaly_threshold (float): Score above which regime is "anomalous" (default 2.0).
        suppress_threshold (float): Score above which all entries are suppressed (default 3.5).
        model_symbol (str): Override symbol for model lookup.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self._cfg = merge_strategy_config("VAE_REGIME_DETECTOR", config or {})
        self._bar_history: deque = deque(maxlen=25)
        self._anomaly_history: deque = deque(maxlen=20)

    def evaluate(self, df_row) -> dict:
        self._bar_history.append(dict(df_row))

        if len(self._bar_history) < 20:
            return {"signal": "NONE"}

        symbol = self._cfg.get("model_symbol") or str(df_row.get("_symbol", ""))
        if not symbol:
            symbol = str(self.config.get("symbol", "")).upper()
        if not symbol:
            return {"signal": "NONE"}

        lookback_rows = list(self._bar_history)[:-1]
        assessment = assess_vae_regime_for_meta(
            symbol,
            df_row,
            lookback_rows=lookback_rows,
            config=self._cfg,
        )

        if not assessment.model_available or assessment.anomaly_score is None:
            return {"signal": "NONE"}

        score = float(assessment.anomaly_score)
        self._anomaly_history.append(score)
        avg_score = sum(self._anomaly_history) / len(self._anomaly_history)

        atr = df_row.get("ATR_14") or df_row.get("ATRr_14") or 0
        try:
            atr = float(atr)
        except (TypeError, ValueError):
            atr = 0.0

        result: dict = {
            "signal": "NONE",
            "anomaly_score": round(score, 4),
            "avg_anomaly_score": round(avg_score, 4),
            "regime": assessment.regime,
            "regime_action": assessment.regime_action,
            "model_type": "vae_regime",
        }

        if assessment.regime_action == "suppress":
            return result

        if assessment.regime_action == "amplify":
            rsi = float(df_row.get("RSI_14") or 50)
            macd_h = float(df_row.get("MACDh_12_26_9") or 0)
            if rsi > 60 and macd_h > 0:
                result["signal"] = "BUY"
            elif rsi < 40 and macd_h < 0:
                result["signal"] = "SELL"
            if result["signal"] in ("BUY", "SELL"):
                result["confidence"] = round(min(score / 5.0, 0.95), 4)
                result["stop_loss_distance"] = atr * 2.0 if atr > 0 else None
            return result

        return result

    def get_anomaly_score(self, symbol: str, features: np.ndarray) -> float | None:
        """Public API for other strategies to query regime state."""
        store = get_vae_store()
        return store.anomaly_score(symbol, features)
