"""ML Auto-Retrain Scheduler.

Background service that monitors ML model age and alpha decay scores.
Queues walk-forward retraining when models go stale or performance
degrades beyond thresholds.

Designed to run as part of the bot manager's periodic maintenance loop.
"""

from __future__ import annotations

import asyncio
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


def _model_metadata_path(
    strategy: str,
    symbol: str,
    timeframe: str | None = None,
) -> str | None:
    """Get metadata.json path for a trained model."""
    from app.services.bots.ml_model_artifacts import model_root_for

    root = model_root_for(strategy, symbol, timeframe)
    if not root:
        return None
    return os.path.join(root, "metadata.json")


def get_model_age_hours(
    strategy: str,
    symbol: str,
    timeframe: str | None = None,
) -> float | None:
    """Get model age in hours. Returns None if no model exists."""
    path = _model_metadata_path(strategy, symbol, timeframe)
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


def get_model_metadata(
    strategy: str,
    symbol: str,
    timeframe: str | None = None,
) -> dict | None:
    """Load model metadata (with validation.json sidecar merge when fingerprints match)."""
    path = _model_metadata_path(strategy, symbol, timeframe)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        if not isinstance(meta, dict):
            return None
        from app.services.bots.ml_model_artifacts import apply_validation_sidecar, model_root_for

        root = model_root_for(strategy, symbol, timeframe)
        return apply_validation_sidecar(meta, root)
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
        # Queue via request_retrain(); ml_retrain_drain_loop submits train jobs.
    """

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
        self._pending_ttl_sec = max(3600.0, float(self.cooldown_hours) * 3600 * 2)
        self._max_last_retrain = 256
        self._max_pending = 64

    def _purge_retrain_maps(self) -> None:
        """TTL + size caps for pending/cooldown maps (MEMORY #14)."""
        now = time.time()
        ttl = self._pending_ttl_sec
        expired = []
        for key, info in list(self._pending.items()):
            requested = info.get("requested_at") or ""
            try:
                # ISO Z timestamps → epoch; fall back to drop if unparseable + over cap
                from datetime import datetime as _dt
                ts = _dt.fromisoformat(str(requested).replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = 0.0
            if ts and (now - ts) > ttl:
                expired.append(key)
            elif not ts:
                expired.append(key)
        for key in expired:
            self._pending.pop(key, None)

        while len(self._pending) > self._max_pending:
            # Drop oldest by requested_at
            oldest = min(
                self._pending.items(),
                key=lambda kv: str(kv[1].get("requested_at") or ""),
            )[0]
            self._pending.pop(oldest, None)

        if len(self._last_retrain) > self._max_last_retrain:
            ordered = sorted(self._last_retrain.items(), key=lambda kv: kv[1])
            for key, _ in ordered[: len(ordered) - self._max_last_retrain]:
                self._last_retrain.pop(key, None)

    def _cooldown_key(self, strategy: str, symbol: str, timeframe: str | None = None) -> str:
        from app.services.bots.ml_model_artifacts import normalize_model_timeframe

        tf = normalize_model_timeframe(timeframe)
        return f"{symbol.upper()}:{strategy.upper()}:{tf}"

    def _is_on_cooldown(self, strategy: str, symbol: str, timeframe: str | None = None) -> bool:
        key = self._cooldown_key(strategy, symbol, timeframe)
        last = self._last_retrain.get(key, 0)
        return (time.time() - last) < self.cooldown_hours * 3600

    def record_retrain(self, strategy: str, symbol: str, timeframe: str | None = None):
        """Record that a retrain was performed (resets cooldown)."""
        key = self._cooldown_key(strategy, symbol, timeframe)
        self._last_retrain[key] = time.time()
        self._pending.pop(key, None)

    # ── Centralized retrain coordinator ───────────────────────────────────────

    def request_retrain(
        self,
        strategy: str,
        symbol: str,
        reason: str,
        source: str,
        timeframe: str | None = None,
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
        timeframe : str, optional
            Bar timeframe for the model artifact (default 1m).

        Returns
        -------
        dict with: queued (bool), reason (str), source (str), key (str).
        """
        from app.services.bots.ml_model_artifacts import normalize_model_timeframe

        tf = normalize_model_timeframe(timeframe)
        key = self._cooldown_key(strategy, symbol, tf)

        self._purge_retrain_maps()

        if self._is_on_cooldown(strategy, symbol, tf):
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
            "timeframe": tf,
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
            "timeframe": tf,
        })
        if len(self._retrain_history) > 100:
            self._retrain_history = self._retrain_history[-100:]

        return {"queued": True, "reason": reason, "source": source, "key": key}

    def get_pending(self) -> dict[str, dict[str, Any]]:
        """Return all pending retrain requests (for dashboard display)."""
        self._purge_retrain_maps()
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

            from app.services.bots.ml_model_artifacts import normalize_model_timeframe

            tf = normalize_model_timeframe(
                bot.get("timeframe") or (bot.get("config") or {}).get("timeframe")
            )
            key = f"{symbol}:{strategy}:{tf}"
            if key in seen:
                continue
            seen.add(key)

            if self._is_on_cooldown(strategy, symbol, tf):
                continue

            age = get_model_age_hours(strategy, symbol, timeframe=tf)
            alpha = alpha_scores.get(f"{symbol}:{strategy}", 0.0) or alpha_scores.get(key, 0.0)
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
                    "timeframe": tf,
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

    def pop_next_pending(self, *, ml_only: bool = True) -> dict[str, Any] | None:
        """Remove and return the oldest pending retrain request.

        When ``ml_only`` is True, skip entries that are not ML signal strategies
        (e.g. META_LABEL — those train via their own callers).
        """
        self._purge_retrain_maps()
        if not self._pending:
            return None
        ordered = sorted(
            self._pending.items(),
            key=lambda kv: str(kv[1].get("requested_at") or ""),
        )
        for key, info in ordered:
            strategy = str(info.get("strategy") or "").upper()
            if ml_only and not is_ml_strategy(strategy):
                continue
            self._pending.pop(key, None)
            out = dict(info)
            out["key"] = key
            return out
        return None


async def _resolve_drain_candles(
    bot_manager,
    symbol: str,
    strategy: str,
    *,
    timeframe: str = "1m",
) -> list[dict]:
    """Best-effort candle pull + indicator enrich for auto-retrain."""
    from app.services.bots.candle_source import get_bot_candles
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe
    from app.services.bots.screener import MarketScreenerService

    tf = normalize_model_timeframe(timeframe)
    feed = getattr(getattr(bot_manager, "oms", None), "feed", None)
    if feed is None:
        return []
    candles = await asyncio.to_thread(
        get_bot_candles,
        symbol,
        feed,
        timeframe=tf,
        min_bars=2000,
    )
    if not candles or len(candles) < 200:
        return list(candles or [])
    try:
        screener = MarketScreenerService()

        def _enrich():
            return screener.process_candles(
                symbol, candles, {}, strategy, full_history=True,
            )

        df = await asyncio.to_thread(_enrich)
        if df is not None and not getattr(df, "empty", True):
            out = [dict(r) for r in df.to_dict("records")]
            for row in out:
                row.setdefault("_symbol", symbol)
            return out
    except Exception:
        logger.exception("Drain enrich failed for %s/%s — using raw candles", strategy, symbol)
    out = [dict(c) for c in candles]
    for row in out:
        row.setdefault("_symbol", symbol)
    return out


async def drain_one_pending_retrain(
    bot_manager,
    *,
    event_bus=None,
) -> dict[str, Any] | None:
    """Pop one pending ML retrain and submit a train job. Returns outcome or None."""
    scheduler = get_retrain_scheduler()
    item = scheduler.pop_next_pending(ml_only=True)
    if not item:
        return None

    strategy = str(item.get("strategy") or "").upper()
    symbol = str(item.get("symbol") or "").upper()
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe

    tf = normalize_model_timeframe(item.get("timeframe"))
    if not strategy or not symbol:
        return {"ok": False, "error": "missing strategy/symbol", "item": item}

    try:
        candles = await _resolve_drain_candles(
            bot_manager, symbol, strategy, timeframe=tf,
        )
    except Exception as exc:
        logger.exception("Drain candle fetch failed for %s/%s", strategy, symbol)
        # Re-queue so a later cycle can retry.
        scheduler.request_retrain(
            strategy, symbol,
            reason=f"drain retry after candle fetch error: {exc}",
            source="retrain_drain",
            timeframe=tf,
        )
        return {"ok": False, "error": str(exc), "strategy": strategy, "symbol": symbol}

    if len(candles) < 200:
        logger.warning(
            "Drain skipped %s/%s — insufficient candles (%d)",
            strategy, symbol, len(candles),
        )
        scheduler.request_retrain(
            strategy, symbol,
            reason=f"drain retry: insufficient candles ({len(candles)})",
            source="retrain_drain",
            timeframe=tf,
        )
        return {
            "ok": False,
            "error": f"insufficient candles ({len(candles)})",
            "strategy": strategy,
            "symbol": symbol,
        }

    from app.services.bots.ml_job_store import create_ml_job
    from app.services.bots.ml_train_executor import submit_train_job

    job_id = create_ml_job(kind="train", strategy=strategy, symbol=symbol)
    logger.info(
        "Retrain drain submitting train job %s for %s/%s @ %s (reasons=%s)",
        job_id, strategy, symbol, tf, item.get("reasons"),
    )
    try:
        result = await submit_train_job(
            strategy, symbol, candles, {"timeframe": tf},
            job_id=job_id, event_bus=event_bus,
        )
    except Exception as exc:
        logger.exception("Drain train job %s failed", job_id)
        return {
            "ok": False,
            "error": str(exc),
            "job_id": job_id,
            "strategy": strategy,
            "symbol": symbol,
        }

    ok = bool(isinstance(result, dict) and result.get("ok") and not result.get("cancelled"))
    return {
        "ok": ok,
        "job_id": job_id,
        "strategy": strategy,
        "symbol": symbol,
        "result": result if isinstance(result, dict) else None,
    }


async def ml_retrain_drain_loop(bot_manager, *, event_bus=None) -> None:
    """Background loop: drain pending ML retrain queue into real train jobs."""
    from app.config import ML_RETRAIN_AUTO_DRAIN, ML_RETRAIN_DRAIN_INTERVAL_SEC

    if not ML_RETRAIN_AUTO_DRAIN:
        logger.info("ML retrain auto-drain disabled (ML_RETRAIN_AUTO_DRAIN=false)")
        return

    interval = max(15.0, float(ML_RETRAIN_DRAIN_INTERVAL_SEC))
    logger.info("ML retrain drain loop started (interval=%.0fs)", interval)
    while True:
        try:
            await asyncio.sleep(interval)
            pending = get_retrain_scheduler().get_pending()
            if not pending:
                continue
            # Only drain ML entries; leave META_LABEL etc. for their callers.
            has_ml = any(
                is_ml_strategy(str(v.get("strategy") or ""))
                for v in pending.values()
            )
            if not has_ml:
                continue
            outcome = await drain_one_pending_retrain(bot_manager, event_bus=event_bus)
            if outcome:
                logger.info(
                    "Retrain drain outcome: ok=%s %s/%s job=%s",
                    outcome.get("ok"),
                    outcome.get("strategy"),
                    outcome.get("symbol"),
                    outcome.get("job_id"),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ML retrain drain loop error")


# Module-level singleton
_scheduler: MlRetrainScheduler | None = None


def get_retrain_scheduler() -> MlRetrainScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = MlRetrainScheduler()
    return _scheduler
