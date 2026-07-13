"""Regime Rotation Agent — automatic bot strategy rotation based on market conditions."""

from __future__ import annotations

import json
import logging
import math
from typing import Any

from app.config import (
    REGIME_ROTATION_ENABLED,
    REGIME_ROTATION_FLATTEN_ON_ROTATE,
)
from app.database import get_connection
from app.services.agent.bar_time import coerce_bar_time
from app.services.agent.working_memory import WorkingMemory
from app.services.agent.reasoning import AgentReasoning, Observation
from app.services.bots.candle_source import get_bot_candles
from app.services.bots.indicators import adx_col, atr_col, merge_strategy_config
from app.services.bots.optimization_store import list_optimization_runs
from app.services.notifications import types as ntypes
from app.services.notifications.dispatcher import emit_notification
from app.services.notifications.events import NotificationEvent

logger = logging.getLogger(__name__)


class RegimeRotationAgent:
    def __init__(self, bot_manager: Any, agent_event_bus: Any | None = None) -> None:
        self.bot_manager = bot_manager
        self.bot_memories: dict[str, WorkingMemory] = {}
        self.agent_event_bus = agent_event_bus

    async def evaluate(self) -> dict[str, Any]:
        """Scan active bots and dynamically rotate strategies based on classified market regimes."""
        results: dict[str, Any] = {
            "rotations": [],
            "flattened_count": 0,
        }

        if not REGIME_ROTATION_ENABLED:
            return results
            
        recently_paused_bot_ids = set()
        if self.agent_event_bus:
            recent_pauses = self.agent_event_bus.recent_events("BOT_PAUSED", lookback_sec=3600)
            for event in recent_pauses:
                if "bot_id" in event.payload:
                    recently_paused_bot_ids.add(event.payload["bot_id"])

        # Copy keys to safely modify bot registry if needed
        for bot_id, bot in list(self.bot_manager.active_bots.items()):
            if bot.get("status") != "RUNNING":
                continue
                
            if bot_id in recently_paused_bot_ids:
                logger.debug("RegimeRotationAgent skipping bot %s (recently paused by %s)", bot_id, "RISK_SENTINEL")
                continue

            cfg = bot.get("config") or {}
            if not cfg.get("regime_rotation_enabled"):
                continue

            symbol = bot.get("symbol")
            timeframe = bot.get("timeframe", "1m")
            current_strategy = bot.get("strategy")

            # 1. Fetch candles & compute indicators using process_candles with CHART_AGENT indicators
            ohlcv = get_bot_candles(
                symbol,
                self.bot_manager.oms.feed,
                timeframe=timeframe,
                min_bars=100,
            )
            if not ohlcv or len(ohlcv) < 50:
                continue

            df = self.bot_manager.screener.process_candles(
                symbol,
                ohlcv,
                strategy="CHART_AGENT",
            )
            if df.empty:
                continue

            row = df.iloc[-1]
            adx_val = row.get(adx_col(14))
            atr_val = row.get(atr_col(14))
            median_atr = row.get(f"{atr_col(14)}_median_20")

            if adx_val is None or atr_val is None or not median_atr:
                continue

            # 2. Classify market regime
            ratio = float(atr_val) / float(median_atr) if median_atr > 0 else 1.0

            if ratio >= 1.5:
                regime = "elevated_vol"
                target_strategy = "VWAP_PULLBACK"
            elif float(adx_val) > 25:
                regime = "trending"
                target_strategy = "SUPERTREND_ADX"
            else:
                regime = "ranging"
                target_strategy = "BRS_SCALPING"

            # 3. Memory and hysteresis logic
            if bot_id not in self.bot_memories:
                self.bot_memories[bot_id] = WorkingMemory()
            
            memory = self.bot_memories[bot_id]
            if memory.is_cooling_down():
                continue

            _, streak = memory.update_decision(target_strategy)

            if current_strategy == target_strategy:
                continue

            if streak < 3:
                # Wait for 3 consecutive cycles of the new regime before rotating
                continue

            # Build reasoning chain for rotation
            obs_adx = Observation("ADX", "trending" if float(adx_val) > 25 else "ranging", 0.90, f"ADX is {float(adx_val):.2f}", {"adx": float(adx_val)})
            obs_vol = Observation("ATR_ratio", "danger" if ratio >= 1.5 else "neutral", 0.90, f"ATR ratio is {ratio:.2f}", {"ratio": ratio})
            uncertainty_sources = []
            if len(ohlcv) < 200:
                uncertainty_sources.append(f"Small sample size for indicators ({len(ohlcv)} bars).")

            recommendation_strength = "strong" if streak >= 5 else "moderate"

            reasoning = AgentReasoning(
                observations=[obs_adx, obs_vol],
                synthesis=f"Market regime shifted to {regime} (Streak: {streak}).",
                decision="ROTATE",
                confidence=0.85 + (min(streak, 10) * 0.01),
                uncertainty_sources=uncertainty_sources,
                recommendation_strength=recommendation_strength
            )

            logger.info(
                "Regime shift detected for %s: rotating bot %s from %s to %s (regime: %s, streak: %d)",
                symbol,
                bot_id,
                current_strategy,
                target_strategy,
                regime,
                streak,
            )

            # Apply a cooldown of 15 minutes (900 seconds) after deciding to rotate
            memory.set_cooldown(900.0)

            # 4. Flatten active position before rotation if enabled
            pos_size = self.bot_manager._get_bot_position_size(bot_id, symbol)
            if abs(pos_size) > 1e-8:
                if REGIME_ROTATION_FLATTEN_ON_ROTATE:
                    try:
                        pos = self.bot_manager._get_bot_position(bot_id, symbol)
                        avg = float(pos.get("avg_price") or 0.0)
                        price = self.bot_manager._mark_price(symbol, avg or 1.0)
                        side = "SELL" if pos_size > 0 else "BUY"
                        
                        await self.bot_manager._execute_order(
                            bot,
                            side,
                            abs(pos_size),
                            price,
                            {
                                "signal": "CLOSE",
                                "reasons": [
                                    f"Regime rotation: {current_strategy} -> {target_strategy} due to {regime} market"
                                ],
                                "reasoning_chain": reasoning.to_dict()
                            },
                            is_exit=True,
                            bar_time=coerce_bar_time(ohlcv[-1]["time"]),
                            entry_price=avg or price,
                        )
                        results["flattened_count"] += 1
                        logger.info("Successfully flattened position on %s for bot %s prior to rotation.", symbol, bot_id)
                    except Exception as exc:
                        logger.error(
                            "Failed to flatten position on %s before rotating strategy for bot %s: %s",
                            symbol,
                            bot_id,
                            exc,
                        )
                        continue  # Skip rotation to avoid unmatched open positions

            # 5. Lookup optimal config from past sweep runs or fallback to defaults
            best_config = None
            try:
                runs = list_optimization_runs(symbol=symbol, limit=20)
                for run in runs:
                    if run.get("strategy") == target_strategy and run.get("best_config"):
                        best_config = run.get("best_config")
                        break
            except Exception as exc:
                logger.warning(
                    "Failed to query optimization runs for %s %s: %s",
                    symbol,
                    target_strategy,
                    exc,
                )

            if not best_config:
                best_config = merge_strategy_config(target_strategy, {})

            # Carry over the opt-in flag to the new strategy configuration
            best_config["regime_rotation_enabled"] = True

            # 6. Apply rotation in DB and memory
            try:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE bots SET strategy = ?, config = ? WHERE id = ?",
                    (target_strategy, json.dumps(best_config), bot_id),
                )
                conn.commit()
                conn.close()

                # Apply changes to local in-memory dict
                bot["strategy"] = target_strategy
                bot["config"] = best_config
                
                # Refresh strategy runtime instance in-place
                self.bot_manager._refresh_strategy_instance(bot_id)

                rotate_msg = f"Regime Rotation: Bot rotated successfully from {current_strategy} to {target_strategy} due to {regime} market condition."
                await self.bot_manager.log_bot_event(bot_id, "INFO", rotate_msg, meta={"reasoning_chain": reasoning.to_dict()})
                
                await emit_notification(
                    NotificationEvent(
                        event_type=ntypes.BOT_STATUS,
                        title="Bot strategy rotated",
                        body=f"Bot {bot_id} ({symbol}) strategy rotated from {current_strategy} to {target_strategy} due to {regime} market.",
                        severity="info",
                        payload={
                            "bot_id": bot_id,
                            "symbol": symbol,
                            "old_strategy": current_strategy,
                            "new_strategy": target_strategy,
                            "regime": regime,
                            "reasoning_chain": reasoning.to_dict()
                        }
                    )
                )

                if self.agent_event_bus:
                    from app.services.agent.agent_event_bus import AgentEvent
                    import time
                    await self.agent_event_bus.publish(
                        AgentEvent(
                            source_agent="REGIME_ROTATION",
                            event_type="REGIME_CHANGED",
                            payload={
                                "symbol": symbol,
                                "old_regime": str(bot.get("meta", {}).get("regime", "unknown")),
                                "new_regime": regime,
                                "old_strategy": current_strategy,
                                "new_strategy": target_strategy,
                                "bot_id": bot_id,
                                "old_bot_id": bot_id,
                            },
                            timestamp=time.time(),
                            reasoning=reasoning,
                        )
                    )

                self.bot_manager.active_bots[bot_id]["strategy"] = target_strategy
                results["rotations"].append({
                    "bot_id": bot_id,
                    "symbol": symbol,
                    "from_strategy": current_strategy,
                    "to_strategy": target_strategy,
                    "regime": regime,
                })

                # Narrate to copilot
                try:
                    import asyncio
                    from app.services.agent.copilot import agent_narrate_event
                    asyncio.create_task(
                        agent_narrate_event(
                            "RegimeRotation",
                            {
                                "action": "rotated_strategy",
                                "bot_id": bot_id,
                                "symbol": symbol,
                                "from_strategy": current_strategy,
                                "to_strategy": target_strategy,
                                "regime": regime,
                            }
                        )
                    )
                except Exception as exc:
                    logger.error("Failed to narrate regime rotation event: %s", exc)

            except Exception as exc:
                logger.exception("Failed to apply in-place strategy rotation for bot %s: %s", bot_id, exc)

        return results
