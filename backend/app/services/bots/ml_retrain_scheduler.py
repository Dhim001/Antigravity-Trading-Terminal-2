"""ML Auto-Retrain Scheduler.

Background service that monitors ML model age and alpha decay scores.
Queues walk-forward retraining when models go stale or performance
degrades beyond thresholds.

Designed to run as part of the bot manager's periodic maintenance loop.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from app.config import BASE_DIR
from app.services.bots.ml_walk_forward_validator import (
    ML_STRATEGIES,
    is_ml_strategy,
)

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────

DEFAULT_MAX_MODEL_AGE_HOURS = 168  # 7 days
DEFAULT_COOLDOWN_HOURS = 24        # Min time between retrains per symbol
DEFAULT_ALPHA_DECAY_THRESHOLD = 0.4  # Retrain if decay score exceeds this
DEFAULT_MIN_TRADES_BEFORE_EVAL = 20  # Don't evaluate until enough trades

MODEL_DIRS = {
    "ML_SIGNAL_BOOST": "ml_signal_models",
    "LSTM_DIRECTION": "lstm_signal_models",
    "RL_PPO_AGENT": "rl_ppo_models",
    "TCN_MULTI_HORIZON": "tcn_signal_models",
    "VAE_REGIME_DETECTOR": "vae_regime_models",
    "TRANSFORMER_SIGNAL": "transformer_signal_models",
    "GNN_CROSS_ASSET": "gnn_signal_models",
}


def _model_metadata_path(strategy: str, symbol: str) -> str | None:
    """Get metadata.json path for a trained model."""
    subdir = MODEL_DIRS.get(strategy.upper())
    if not subdir:
        return None
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in symbol.upper())
    return os.path.join(BASE_DIR, "data", subdir, safe, "metadata.json")


def get_model_age_hours(strategy: str, symbol: str) -> float | None:
    """Get model age in hours. Returns None if no model exists."""
    path = _model_metadata_path(strategy, symbol)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        trained_at = meta.get("trained_at", "")
        if not trained_at:
            # Fallback to file mtime
            return (time.time() - os.path.getmtime(path)) / 3600.0
        dt = datetime.fromisoformat(trained_at.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return delta.total_seconds() / 3600.0
    except Exception:
        return None


def get_model_metadata(strategy: str, symbol: str) -> dict | None:
    """Load model metadata."""
    path = _model_metadata_path(strategy, symbol)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ── Retrain scheduler ─────────────────────────────────────────────────────


class MlRetrainScheduler:
    """Monitors ML models and schedules retraining when needed.

    Also acts as the **centralized retrain coordinator** — all retrain
    triggers (alpha decay, posttrade learner, periodic scheduler) must call
    ``request_retrain()`` instead of retraining directly.  This enforces
    a shared cooldown and deduplicates concurrent requests.

    Usage:
        scheduler = MlRetrainScheduler()
        actions = scheduler.check(active_bots)
        for action in actions:
            await action["retrain_fn"](...)    """

    def __init__(
        self,
        *,
        max_age_hours: float = DEFAULT_MAX_MODEL_AGE_HOURS,
        cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
        alpha_threshold: float = DEFAULT_ALPHA_DECAY_THRESHOLD,
        min_trades: int = DEFAULT_MIN_TRADES_BEFORE_EVAL,
    ):
        self.max_age_hours = max_age_hours
        self.cooldown_hours = cooldown_hours
        self.alpha_threshold = alpha_threshold
        self.min_trades = min_trades
        self._last_retrain: dict[str, float] = {}  # symbol_strategy → timestamp
        self._pending: dict[str, dict[str, Any]] = {}  # key → request info
        self._retrain_history: list[dict[str, Any]] = []  # audit trail

    def _cooldown_key(self, strategy: str, symbol: str) -> str:
        return f"{symbol.upper()}:{strategy.upper()}"

    def _is_on_cooldown(self, strategy: str, symbol: str) -> bool:
        key = self._cooldown_key(strategy, symbol)
        last = self._last_retrain.get(key, 0)
        return (time.time() - last) < self.cooldown_hours * 3600

    def record_retrain(self, strategy: str, symbol: str):
        """Record that a retrain was performed (resets cooldown)."""
        key = self._cooldown_key(strategy, symbol)
        self._last_retrain[key] = time.time()
        self._pending.pop(key, None)

    # ── Centralized retrain coordinator ───────────────────────────────────────

    def request_retrain(
        self,
        strategy: str,
        symbol: str,
        reason: str,
        source: str,
    ) -> dict[str, Any]:
        """Centralized retrain request — deduplicates and enforces cooldown.

        Parameters
        ----------
        strategy : str
            Strategy ID (e.g. 'ML_SIGNAL_BOOST') or bot_id for meta-label.
        symbol : str
            Trading symbol.
        reason : str
            Human-readable reason for the retrain request.
        source : str
            Trigger origin: 'alpha_decay' | 'retrain_scheduler' | 'posttrade_learner'.

        Returns
        -------
        dict with: queued (bool), reason (str), source (str), key (str).
        """
        key = self._cooldown_key(strategy, symbol)

        if self._is_on_cooldown(strategy, symbol):
            logger.debug(
                "Retrain request from %s for %s skipped (cooldown)", source, key,
            )
            return {"queued": False, "reason": "cooldown", "source": source, "key": key}

        if key in self._pending:
            self._pending[key]["reasons"].append(f"{source}: {reason}")
            logger.debug(
                "Retrain request from %s for %s merged into pending", source, key,
            )
            return {"queued": False, "reason": "already_pending", "source": source, "key": key}

        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._pending[key] = {
            "strategy": strategy,
            "symbol": symbol,
            "reasons": [f"{source}: {reason}"],
            "requested_at": ts,
        }
        logger.info("Retrain queued for %s by %s: %s", key, source, reason)

        # Record in audit history (keep last 100 entries)
        self._retrain_history.append({
            "key": key,
            "source": source,
            "reason": reason,
            "requested_at": ts,
        })
        if len(self._retrain_history) > 100:
            self._retrain_history = self._retrain_history[-100:]

        return {"queued": True, "reason": reason, "source": source, "key": key}

    def get_pending(self) -> dict[str, dict[str, Any]]:
        """Return all pending retrain requests (for dashboard display)."""
        return dict(self._pending)

    def get_retrain_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent retrain history for audit trail."""
        return list(reversed(self._retrain_history[-limit:]))

    def check(
        self,
        active_bots: list[dict],
        *,
        alpha_scores: dict[str, float] | None = None,
    ) -> list[dict[str, Any]]:
        """Check all active ML bots and return retrain recommendations.

        Parameters
        ----------
        active_bots : list[dict]
            List of active bot dicts with 'strategy' and 'symbol' keys.
        alpha_scores : dict, optional
            Map of 'symbol:strategy' → alpha decay score (0-1, higher = more decayed).

        Returns
        -------
        List of retrain action dicts with:
            strategy, symbol, reason, priority, model_age_hours
        """
        alpha_scores = alpha_scores or {}
        actions = []

        seen = set()
        for bot in active_bots:
            strategy = str(bot.get("strategy", "")).upper()
            symbol = str(bot.get("symbol", "")).upper()
            if not is_ml_strategy(strategy) or not symbol:
                continue

            key = f"{symbol}:{strategy}"
            if key in seen:
                continue
            seen.add(key)

            if self._is_on_cooldown(strategy, symbol):
                continue

            age = get_model_age_hours(strategy, symbol)
            alpha = alpha_scores.get(key, 0.0)
            reason = None
            priority = 0

            # No model exists — must train
            if age is None:
                reason = "no_model"
                priority = 10

            # Model is stale
            elif age > self.max_age_hours:
                reason = "stale"
                priority = 5

            # Alpha decay exceeded
            elif alpha > self.alpha_threshold:
                reason = "alpha_decay"
                priority = 8

            if reason:
                actions.append({
                    "strategy": strategy,
                    "symbol": symbol,
                    "reason": reason,
                    "priority": priority,
                    "model_age_hours": round(age, 1) if age is not None else None,
                    "alpha_score": round(alpha, 4),
                })

        # Sort by priority (highest first)
        actions.sort(key=lambda a: -a["priority"])
        return actions

    def should_retrain(self, strategy: str, symbol: str, alpha_score: float = 0.0) -> tuple[bool, str]:
        """Quick check for a single bot.

        Returns (should_retrain, reason).
        """
        if not is_ml_strategy(strategy):
            return False, "not_ml"
        if self._is_on_cooldown(strategy, symbol):
            return False, "cooldown"

        age = get_model_age_hours(strategy, symbol)
        if age is None:
            return True, "no_model"
        if age > self.max_age_hours:
            return True, f"stale ({age:.0f}h > {self.max_age_hours:.0f}h)"
        if alpha_score > self.alpha_threshold:
            return True, f"alpha_decay ({alpha_score:.2f} > {self.alpha_threshold:.2f})"
        return False, "healthy"


# Module-level singleton
_scheduler: MlRetrainScheduler | None = None


def get_retrain_scheduler() -> MlRetrainScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = MlRetrainScheduler()
    return _scheduler
