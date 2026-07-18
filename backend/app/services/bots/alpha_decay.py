"""Alpha Decay Monitor Agent — track strategy performance degradation vs backtest expectations."""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from typing import Any

from app.config import (
    ALPHA_DECAY_AUTO_PAUSE,
    ALPHA_DECAY_AUTO_RETRAIN,
    ALPHA_DECAY_ENABLED,
    ALPHA_DECAY_MIN_TRADES,
)
from app.database import get_connection
from app.services.bots.candle_source import get_bot_candles
from app.services.bots.indicators import adx_col
from app.services.bots.meta_label_model import get_meta_label_store, train_meta_label_model
from app.services.bots.backtest_store import list_backtest_runs
from app.services.bots.optimization_store import list_optimization_runs
from app.services.notifications import types as ntypes
from app.services.notifications.dispatcher import emit_notification
from app.services.notifications.events import NotificationEvent

logger = logging.getLogger(__name__)


def get_strategy_category(strategy: str) -> str:
    try:
        from app.services.bots.strategy_catalog import _BAR_BUILTIN
        for s in _BAR_BUILTIN:
            if s["id"] == strategy:
                return s.get("category", "trend")
    except Exception:
        pass
    return "trend"


def get_backtest_expectations(symbol: str, strategy: str) -> tuple[float | None, float | None]:
    """Retrieve win rate and Sharpe ratio expectations from recent backtest or optimization runs."""
    try:
        runs = list_backtest_runs(symbol=symbol, limit=20)
        for r in runs:
            if r.get("strategy") == strategy and r.get("summary"):
                summary = r["summary"]
                win_rate = summary.get("win_rate")
                sharpe = summary.get("sharpe_ratio")
                if win_rate is not None or sharpe is not None:
                    return win_rate, sharpe
    except Exception:
        pass

    try:
        opt_runs = list_optimization_runs(symbol=symbol, limit=20)
        for r in opt_runs:
            if r.get("strategy") == strategy and r.get("best_config"):
                results = r.get("results") or []
                for res in results:
                    summary = res.get("summary") or res
                    win_rate = summary.get("win_rate")
                    sharpe = summary.get("sharpe_ratio")
                    if win_rate is not None or sharpe is not None:
                        return win_rate, sharpe
    except Exception:
        pass

    return None, None


def _compute_live_sharpe(returns: list[float], timestamps: list[str]) -> float | None:
    if len(returns) < 3:
        return None
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = variance ** 0.5
    if std_r < 1e-12:
        return None
    try:
        t0_dt = datetime.fromisoformat(timestamps[-1].replace("Z", "+00:00"))
        t1_dt = datetime.fromisoformat(timestamps[0].replace("Z", "+00:00"))
        duration_years = (t1_dt - t0_dt).total_seconds() / (365.25 * 86400)
    except Exception:
        duration_years = 0.0
    if duration_years > 0:
        return round((mean_r / std_r) * (len(returns) / duration_years) ** 0.5, 2)
    return round((mean_r / std_r) * (len(returns) ** 0.5), 2)


class AlphaDecayMonitor:
    def __init__(self, bot_manager: Any) -> None:
        self.bot_manager = bot_manager

    async def evaluate(self) -> dict[str, Any]:
        """Scan active bots to evaluate performance indicators for alpha decay."""
        results: dict[str, Any] = {
            "decaying_bots": [],
            "paused_bots": [],
            "retrained_models": [],
        }

        if not ALPHA_DECAY_ENABLED:
            return results

        for bot_id, bot in list(self.bot_manager.active_bots.items()):
            if bot.get("status") != "RUNNING":
                continue

            cfg = bot.get("config") or {}
            # Allow opt-out per bot via config override
            if cfg.get("alpha_decay_monitor_disabled"):
                continue

            symbol = bot.get("symbol")
            timeframe = bot.get("timeframe", "1m")
            strategy = bot.get("strategy")
            decay_reasons: list[str] = []

            # 1. Fetch backtest expectations
            bt_win_rate, bt_sharpe = get_backtest_expectations(symbol, strategy)
            expected_win_rate = float(bt_win_rate if bt_win_rate is not None else 55.0)
            if expected_win_rate <= 1.0:
                expected_win_rate *= 100.0  # normalize decimal to percentage

            expected_sharpe = float(bt_sharpe if bt_sharpe is not None else 1.5)

            # Retrieve exits from database
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT pnl, timestamp FROM bot_trades 
                WHERE bot_id = ? AND is_exit = 1 AND pnl IS NOT NULL 
                ORDER BY timestamp DESC LIMIT 30
                """,
                (bot_id,),
            )
            rows = cursor.fetchall()
            conn.close()

            # Ensure we have enough statistical samples
            if len(rows) >= ALPHA_DECAY_MIN_TRADES:
                pnls = [float(r[0]) for r in rows]
                timestamps = [str(r[1]) for r in rows]

                # --- Metric 1: Rolling Win Rate Divergence ---
                rolling_win_rate = (sum(1 for p in pnls[:20] if p > 0) / len(pnls[:20])) * 100
                if rolling_win_rate < expected_win_rate - 15.0:
                    decay_reasons.append(
                        f"Win Rate Decay: Live rolling win rate {rolling_win_rate:.1f}% "
                        f"is >15% below expected {expected_win_rate:.1f}%"
                    )

                # --- Metric 2: Sharpe Decay ---
                if expected_sharpe > 0.0:
                    live_sharpe = _compute_live_sharpe(pnls, timestamps)
                    if live_sharpe is not None and live_sharpe < expected_sharpe * 0.5:
                        decay_reasons.append(
                            f"Sharpe Decay: Live Sharpe ratio {live_sharpe:.2f} "
                            f"is <50% of expected {expected_sharpe:.2f}"
                        )

            # --- Metric 3: Regime Mismatch ---
            category = get_strategy_category(strategy)
            ohlcv = get_bot_candles(symbol, self.bot_manager.oms.feed, timeframe=timeframe, min_bars=50)
            if ohlcv and len(ohlcv) >= 30:
                df = self.bot_manager.screener.process_candles(symbol, ohlcv, strategy="CHART_AGENT")
                if not df.empty:
                    adx_col_name = adx_col(14)
                    if adx_col_name in df.columns:
                        recent_adxs = df[adx_col_name].dropna().tail(50).tolist()
                        if recent_adxs:
                            trending_bars = sum(1 for adx in recent_adxs if adx > 25)
                            trending_pct = trending_bars / len(recent_adxs)

                            if category == "trend" and trending_pct < 0.3:
                                decay_reasons.append(
                                    f"Regime Mismatch: Trending strategy running in ranging market "
                                    f"(Trending bars: {trending_pct:.1%})"
                                )
                            elif category in ("scalp", "market_making") and trending_pct > 0.7:
                                decay_reasons.append(
                                    f"Regime Mismatch: Ranging/Mean-Reversion strategy running in trending market "
                                    f"(Trending bars: {trending_pct:.1%})"
                                )

            # --- Metric 4: Consecutive Filter Rejections ---
            signal_history = bot.get("signal_history")
            if signal_history and len(signal_history) >= 10:
                rejections = sum(1 for x in signal_history if x is False)
                reject_ratio = rejections / len(signal_history)
                if reject_ratio >= 0.8:
                    decay_reasons.append(
                        f"Filter Stale: {reject_ratio:.1%} of recent signals blocked by filters"
                    )

            # --- Metric 5: Meta-Label Confidence Drift ---
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT insight_snapshot FROM bot_trades 
                WHERE bot_id = ? AND is_exit = 0 AND insight_snapshot IS NOT NULL 
                ORDER BY timestamp DESC LIMIT 20
                """,
                (bot_id,),
            )
            entry_rows = cursor.fetchall()
            conn.close()

            if len(entry_rows) >= 5:
                probs = []
                for er in entry_rows:
                    try:
                        snap = json.loads(er[0]) if isinstance(er[0], str) else er[0]
                    except Exception:
                        snap = None
                    if isinstance(snap, dict) and snap.get("confidence") is not None:
                        probs.append(float(snap["confidence"]))
                if probs:
                    avg_live_prob = sum(probs) / len(probs)
                    meta = get_meta_label_store().get_metadata(bot_id)
                    train_win_rate = None
                    if meta:
                        train_win_rate = meta.get("metrics", {}).get("train_win_rate")

                    if train_win_rate is not None and avg_live_prob < train_win_rate - 0.15:
                        decay_reasons.append(
                            f"Meta-Label Drift: Avg P(win) dropped to {avg_live_prob:.2f} "
                            f"(expected {train_win_rate:.2f})"
                        )

            # --- Metric 6: ML Model Staleness ---
            try:
                from app.services.bots.ml_retrain_scheduler import (
                    get_model_age_hours,
                    get_model_metadata,
                )
                from app.services.bots.ml_walk_forward_validator import is_ml_strategy

                if is_ml_strategy(strategy):
                    model_age = get_model_age_hours(strategy, symbol)
                    max_age = float(cfg.get("ml_max_model_age_hours", 168))
                    if model_age is not None and model_age > max_age:
                        decay_reasons.append(
                            f"ML Model Stale: Model is {model_age:.0f}h old "
                            f"(max {max_age:.0f}h)"
                        )
                    elif model_age is None:
                        decay_reasons.append(
                            "ML Model Missing: No trained model found for this symbol"
                        )

                    # --- Metric 7: OOS Accuracy Drift ---
                    model_meta = get_model_metadata(strategy, symbol)
                    if model_meta:
                        wf_accuracy = model_meta.get("metrics", {}).get("val_accuracy")
                        if wf_accuracy is not None and len(rows) >= ALPHA_DECAY_MIN_TRADES:
                            pnls_ml = [float(r[0]) for r in rows]
                            live_win_rate_ml = sum(1 for p in pnls_ml if p > 0) / len(pnls_ml)
                            # Significant degradation: live win rate > 15% below
                            # the training validation accuracy
                            if live_win_rate_ml < wf_accuracy - 0.15:
                                decay_reasons.append(
                                    f"ML Accuracy Drift: Live win rate {live_win_rate_ml:.1%} "
                                    f"is >15% below training accuracy {wf_accuracy:.1%}"
                                )
            except ImportError:
                pass  # ML modules not installed

            # --- Decay Remediation Actions ---
            if decay_reasons:
                logger.warning("Alpha Decay detected for bot %s (%s): %s", bot_id, symbol, "; ".join(decay_reasons))
                results["decaying_bots"].append({
                    "bot_id": bot_id,
                    "symbol": symbol,
                    "reasons": decay_reasons,
                })

                # ML-specific retrain: use the retrain scheduler for ML strategies
                ml_retrained = False
                if ALPHA_DECAY_AUTO_RETRAIN:
                    try:
                        from app.services.bots.ml_walk_forward_validator import is_ml_strategy as _is_ml
                        from app.services.bots.ml_retrain_scheduler import get_retrain_scheduler

                        if _is_ml(strategy):
                            scheduler = get_retrain_scheduler()
                            should, reason = scheduler.should_retrain(strategy, symbol, alpha_score=0.8)
                            if should:
                                scheduler.record_retrain(strategy, symbol)
                                results["retrained_models"].append(bot_id)
                                ml_retrained = True
                                await self.bot_manager.log_bot_event(
                                    bot_id,
                                    "INFO",
                                    f"Alpha Decay: ML retrain scheduled ({reason}). "
                                    f"Model will be retrained with walk-forward validation.",
                                )
                    except ImportError:
                        pass

                # Retrain meta-label model (existing behavior, for non-ML strategies)
                if ALPHA_DECAY_AUTO_RETRAIN and not ml_retrained:
                    try:
                        import asyncio
                        retrain_res = await asyncio.to_thread(train_meta_label_model, bot_id)
                        if retrain_res.get("ok"):
                            results["retrained_models"].append(bot_id)
                            await self.bot_manager.log_bot_event(
                                bot_id,
                                "INFO",
                                "Alpha Decay Monitor: Successfully retrained meta-label model with fresh trade history.",
                            )
                    except Exception as exc:
                        logger.error("Failed to retrain meta-label model for bot %s on decay: %s", bot_id, exc)

                # Auto-pause bot if configured
                if ALPHA_DECAY_AUTO_PAUSE:
                    try:
                        await self.bot_manager.pause_bot(bot_id)
                        results["paused_bots"].append(bot_id)
                        
                        decay_report = (
                            f"Alpha Decay Circuit Breaker: Automatically paused bot due to performance degradation. "
                            f"Decay report: {'; '.join(decay_reasons)}"
                        )
                        await self.bot_manager.log_bot_event(bot_id, "WARN", decay_report)
                    except Exception as exc:
                        logger.error("Failed to auto-pause decaying bot %s: %s", bot_id, exc)

                # Dispatch Alert Notification
                try:
                    await emit_notification(
                        NotificationEvent(
                            event_type=ntypes.ALPHA_DECAY,
                            title="Strategy Alpha Decay Detected",
                            body=f"Bot {bot_id} ({symbol}) strategy performance decays: {decay_reasons[0]}",
                            severity="warning",
                            payload={
                                "bot_id": bot_id,
                                "symbol": symbol,
                                "reasons": decay_reasons,
                                "auto_paused": ALPHA_DECAY_AUTO_PAUSE,
                                "auto_retrained": ALPHA_DECAY_AUTO_RETRAIN,
                            },
                        )
                    )
                except Exception as exc:
                    logger.error("Failed to emit alpha decay notification for bot %s: %s", bot_id, exc)

                # Narrate to copilot
                try:
                    import asyncio
                    from app.services.agent.copilot import agent_narrate_event
                    asyncio.create_task(
                        agent_narrate_event(
                            "AlphaDecay",
                            {
                                "action": "decay_detected",
                                "bot_id": bot_id,
                                "symbol": symbol,
                                "reasons": decay_reasons,
                                "auto_paused": ALPHA_DECAY_AUTO_PAUSE,
                                "auto_retrained": ALPHA_DECAY_AUTO_RETRAIN,
                                "why": decay_reasons[0] if decay_reasons else "edge degradation",
                            },
                        )
                    )
                except Exception as exc:
                    logger.error("Failed to narrate alpha decay event: %s", exc)

        return results
