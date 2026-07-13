"""Risk Sentinel Agent — proactive portfolio safety, streak auto-pause, correlation alerts."""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from app.config import (
    BOT_MAX_CONSECUTIVE_LOSSES,
    RISK_CORRELATION_THRESHOLD,
    RISK_DYNAMIC_CORRELATION_ENABLED,
    RISK_SENTINEL_AUTO_PAUSE_ON_STREAK,
    RISK_SENTINEL_ENABLED,
    RISK_SENTINEL_MAX_CORRELATION_EXPOSURE_PCT,
    RISK_SENTINEL_MAX_VELOCITY,
)
from app.services.bots import analytics as bot_analytics
from app.services.bots.correlation import summarize_basket_correlation
from app.services.bots.portfolio_risk import list_bot_exposures, _mark_prices
from app.services.notifications import types as ntypes
from app.services.notifications.dispatcher import emit_notification
from app.services.notifications.events import NotificationEvent
from app.services.agent.working_memory import WorkingMemory
from app.services.agent.reasoning import AgentReasoning, Observation

logger = logging.getLogger(__name__)


class RiskSentinel:
    def __init__(self, agent_event_bus: Any | None = None) -> None:
        # Store historical drawdown percentages to compute velocity
        self.drawdown_history: deque[tuple[float, float]] = deque(maxlen=20)
        self.bot_memories: dict[str, WorkingMemory] = {}
        self.agent_event_bus = agent_event_bus

    async def evaluate(self, snapshot: Any, oms: Any, bot_manager: Any) -> dict[str, Any]:
        """Evaluate portfolio-level and bot-level risk parameters.

        Triggers auto-pausing and alert warnings if thresholds are breached.
        """
        results: dict[str, Any] = {
            "velocity_breached": False,
            "streak_paused_count": 0,
            "correlation_warnings": [],
        }

        if not RISK_SENTINEL_ENABLED:
            return results

        now = time.time()

        # 1. Drawdown Velocity & Acceleration Check
        current_dd = float(snapshot.current_drawdown_pct)
        self.drawdown_history.append((now, current_dd))

        if len(self.drawdown_history) >= 2:
            prev_time, prev_dd = self.drawdown_history[-2]
            dd_diff = current_dd - prev_dd
            time_diff = now - prev_time

            # Drawdown velocity (drawdown percentage change per check tick or per second)
            # To handle variable polling intervals, we evaluate the absolute difference
            # in drawdown pct between consecutive ticks.
            if time_diff < 300.0 and dd_diff >= RISK_SENTINEL_MAX_VELOCITY:
                results["velocity_breached"] = True
                reason = (
                    f"Risk Sentinel: Drawdown spiked by {dd_diff:.2f}% (limit {RISK_SENTINEL_MAX_VELOCITY:.1f}%) "
                    f"in a single interval (from {prev_dd:.2f}% to {current_dd:.2f}%)."
                )
                logger.warning(reason)

                # Send risk sentinel alert
                await emit_notification(
                    NotificationEvent(
                        event_type=ntypes.RISK_SENTINEL,
                        title="Drawdown velocity breach",
                        body=reason,
                        severity="error",
                        payload={
                            "current_drawdown": current_dd,
                            "prev_drawdown": prev_dd,
                            "change_pct": dd_diff,
                            "limit_pct": RISK_SENTINEL_MAX_VELOCITY,
                        },
                    )
                )

                # Proactively pause active running bots to halt losses
                paused_count = 0
                for bot_id, bot in list(bot_manager.active_bots.items()):
                    if bot.get("status") == "RUNNING":
                        if bot_id not in self.bot_memories:
                            self.bot_memories[bot_id] = WorkingMemory()
                        if self.bot_memories[bot_id].is_cooling_down():
                            continue
                        try:
                            await bot_manager.pause_bot(bot_id)
                            self.bot_memories[bot_id].set_cooldown(3600.0)

                            obs = Observation("drawdown_velocity", "danger", 0.95, f"Spike of {dd_diff:.2f}%", {"prev_dd": prev_dd, "current_dd": current_dd})
                            reasoning = AgentReasoning(
                                observations=[obs],
                                synthesis="Drawdown velocity spiked rapidly.",
                                decision="PAUSE",
                                confidence=0.95,
                                recommendation_strength="strong"
                            )

                            await bot_manager.log_bot_event(
                                bot_id,
                                "WARN",
                                f"Auto-paused by Risk Sentinel drawdown velocity spike of {dd_diff:.2f}%.",
                                meta={"reasoning_chain": reasoning.to_dict()}
                            )
                            if self.agent_event_bus:
                                from app.services.agent.agent_event_bus import AgentEvent
                                await self.agent_event_bus.publish(
                                    AgentEvent(
                                        source_agent="RISK_SENTINEL",
                                        event_type="BOT_PAUSED",
                                        payload={"bot_id": bot_id, "reason": "drawdown_velocity_spike"},
                                        timestamp=time.time(),
                                        reasoning=reasoning,
                                    )
                                )
                            paused_count += 1
                        except Exception as exc:
                            logger.error("Sentinel failed to pause bot %s: %s", bot_id, exc)
                
                # Narrate to copilot
                asyncio.create_task(
                    agent_narrate_event(
                        "RiskSentinel",
                        {
                            "action": "paused_all_bots",
                            "reason": f"Drawdown velocity breached {RISK_SENTINEL_MAX_VELOCITY}% limit.",
                            "bots_paused": paused_count,
                            "current_drawdown": current_dd,
                        }
                    )
                )

                if paused_count > 0:
                    logger.warning("Risk Sentinel paused %d active bot(s) due to velocity spike.", paused_count)

        # 2. Consecutive Loss Streak Auto-Pause
        if RISK_SENTINEL_AUTO_PAUSE_ON_STREAK:
            for bot_id, bot in list(bot_manager.active_bots.items()):
                if bot.get("status") != "RUNNING":
                    continue

                if bot_id not in self.bot_memories:
                    self.bot_memories[bot_id] = WorkingMemory()
                if self.bot_memories[bot_id].is_cooling_down():
                    continue

                cfg = bot.get("config") or {}
                max_streak = int(cfg.get("max_consecutive_losses", BOT_MAX_CONSECUTIVE_LOSSES))
                if max_streak <= 0:
                    continue

                streak = bot_analytics.get_recent_consecutive_losses(bot_id)
                if streak >= max_streak:
                    try:
                        await bot_manager.pause_bot(bot_id)
                        cooldown = 3600.0
                        self.bot_memories[bot_id].set_cooldown(cooldown)
                        results["streak_paused_count"] += 1
                        reason = (
                            f"Bot {bot.get('symbol')} reached consecutive loss limit ({streak}/{max_streak}). "
                            f"Auto-paused by Risk Sentinel."
                        )
                        obs = Observation("failures_streak", "danger", 0.95, f"Reached {streak} losses", {"streak": streak, "max_streak": max_streak})
                        reasoning = AgentReasoning(
                            observations=[obs],
                            synthesis="Consecutive loss limit reached.",
                            decision="PAUSE",
                            confidence=0.95,
                            recommendation_strength="strong"
                        )

                        await bot_manager.log_bot_event(bot_id, "WARN", reason, meta={"reasoning_chain": reasoning.to_dict()})
                        
                        if self.agent_event_bus:
                            from app.services.agent.agent_event_bus import AgentEvent
                            await self.agent_event_bus.publish(
                                AgentEvent(
                                    source_agent="RISK_SENTINEL",
                                    event_type="BOT_PAUSED",
                                    payload={"bot_id": bot_id, "reason": "loss_streak"},
                                    timestamp=time.time(),
                                    reasoning=reasoning,
                                )
                            )
                        
                        await emit_notification(
                            NotificationEvent(
                                event_type=ntypes.RISK_SENTINEL,
                                title="Bot auto-paused by Risk Sentinel",
                                body=reason,
                                severity="warning",
                                payload={
                                    "bot_id": bot_id,
                                    "symbol": bot.get("symbol"),
                                    "streak": streak,
                                    "max_streak": max_streak,
                                },
                            )
                        )

                        # Narrate to copilot
                        asyncio.create_task(
                            agent_narrate_event(
                                "RiskSentinel",
                                {
                                    "action": "paused_single_bot",
                                    "reason": f"Hit max consecutive losses ({streak}).",
                                    "bot_id": bot_id,
                                }
                            )
                        )
                    except Exception as exc:
                        logger.error("Sentinel failed to auto-pause bot %s on loss streak: %s", bot_id, exc)

        # 3. Dynamic Correlation Sizing Caps Check
        if RISK_DYNAMIC_CORRELATION_ENABLED:
            bot_exposures = list_bot_exposures()
            active_symbols = list({row["symbol"] for row in bot_exposures})

            if len(active_symbols) >= 2:
                # Find high-correlation pairs
                summary = summarize_basket_correlation(active_symbols, feed=oms.feed)
                high_pairs = summary.get("high_pairs") or []

                if high_pairs:
                    # Map symbol exposures
                    marks = _mark_prices(oms, set(active_symbols))
                    symbol_exp: dict[str, float] = {}
                    symbol_net_size: dict[str, float] = {}
                    symbol_direction: dict[str, str] = {}

                    for row in bot_exposures:
                        sym = row["symbol"]
                        size = row["size"]
                        mark = marks.get(sym) or row["avg_price"]
                        symbol_exp[sym] = symbol_exp.get(sym, 0.0) + abs(size * mark)
                        symbol_net_size[sym] = symbol_net_size.get(sym, 0.0) + size

                    for sym, net_size in symbol_net_size.items():
                        if net_size > 1e-8:
                            symbol_direction[sym] = "LONG"
                        elif net_size < -1e-8:
                            symbol_direction[sym] = "SHORT"

                    account_equity = float(snapshot.account_equity)
                    if account_equity <= 0:
                        account_equity = 1.0

                    for pair in high_pairs:
                        a, b = pair["a"], pair["b"]
                        corr = pair["correlation"]

                        # Check if direction matches
                        dir_a = symbol_direction.get(a)
                        dir_b = symbol_direction.get(b)

                        if dir_a and dir_b and dir_a == dir_b:
                            # Both on same side, sum combined exposure
                            combined_exp = symbol_exp.get(a, 0.0) + symbol_exp.get(b, 0.0)
                            combined_pct = (combined_exp / account_equity) * 100.0

                            if combined_pct >= RISK_SENTINEL_MAX_CORRELATION_EXPOSURE_PCT:
                                warn_msg = (
                                    f"Concentration risk alert: Highly correlated assets {a}/{b} "
                                    f"(correlation {corr:.2f}) share matching direction ({dir_a}) "
                                    f"with combined exposure of ${combined_exp:,.2f} ({combined_pct:.1f}% of equity, "
                                    f"limit {RISK_SENTINEL_MAX_CORRELATION_EXPOSURE_PCT:.1f}%)."
                                )
                                logger.warning(warn_msg)
                                results["correlation_warnings"].append(pair)

                                await emit_notification(
                                    NotificationEvent(
                                        event_type=ntypes.RISK_SENTINEL,
                                        title="High correlation exposure warning",
                                        body=warn_msg,
                                        severity="warning",
                                        payload={
                                            "symbol_a": a,
                                            "symbol_b": b,
                                            "correlation": corr,
                                            "direction": dir_a,
                                            "combined_exposure": combined_exp,
                                            "combined_pct": combined_pct,
                                            "limit_pct": RISK_SENTINEL_MAX_CORRELATION_EXPOSURE_PCT,
                                        },
                                    )
                                )

        return results
