"""Scanner Auto-Deploy Agent — autonomously deploys bots based on screener signals."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import (
    SCANNER_DEPLOY_ENABLED,
    SCANNER_DEPLOY_MIN_CONFIDENCE,
    SCANNER_DEPLOY_MIN_SCORE,
    SCANNER_DEPLOY_MAX_CORRELATION,
    SCANNER_DEPLOY_MAX_PORTFOLIO_ALLOCATION,
    SCANNER_DEPLOY_MAX_CONCURRENT_BOTS,
    SCANNER_DEPLOY_BASE_ALLOCATION,
    SCANNER_DEPLOY_STRATEGY,
    SCANNER_DEPLOY_TIMEFRAME,
    SCANNER_DEPLOY_AUTO_STOP_ON_DD,
    SCANNER_DEPLOY_MAX_DRAWDOWN_PCT,
    SCANNER_DEPLOY_WATCHLIST,
    _normalize_crypto_watch_symbol,
)
from app.services.agent.reasoning import AgentReasoning, Observation
from app.services.agent.pipeline import rank_scan_rows, active_bot_symbols
from app.services.bots.correlation import summarize_basket_correlation

logger = logging.getLogger(__name__)


class ScannerDeployAgent:
    def __init__(self, bot_manager: Any, backtester: Any | None = None, agent_event_bus: Any | None = None) -> None:
        self.bot_manager = bot_manager
        self.backtester = backtester
        self.agent_event_bus = agent_event_bus
        self.watchlist = [
            n for n in (_normalize_crypto_watch_symbol(s) for s in SCANNER_DEPLOY_WATCHLIST) if n
        ]

    async def evaluate(self) -> dict[str, Any]:
        """Scan, filter, validate, and optionally deploy new bots."""
        results: dict[str, Any] = {
            "scanned": 0,
            "candidates": 0,
            "deployed": [],
            "skipped": [],
        }

        if not SCANNER_DEPLOY_ENABLED:
            return results

        # 1. Total capital & concurrent bots gate
        active_bots = [b for b in self.bot_manager.active_bots.values() if b.get("status") == "RUNNING"]
        total_allocated = sum(float(b.get("allocation", 0.0)) for b in active_bots)
        
        # Consider auto-deployed bots as those with pipeline_source="scanner"
        auto_bots = [b for b in active_bots if b.get("config", {}).get("pipeline_source") == "scanner"]
        num_auto_bots = len(auto_bots)

        if total_allocated >= SCANNER_DEPLOY_MAX_PORTFOLIO_ALLOCATION:
            logger.debug("ScannerDeployAgent blocked: total portfolio allocation (%.2f) >= max (%.2f).", total_allocated, SCANNER_DEPLOY_MAX_PORTFOLIO_ALLOCATION)
            return results
            
        if num_auto_bots >= SCANNER_DEPLOY_MAX_CONCURRENT_BOTS:
            logger.debug("ScannerDeployAgent blocked: max concurrent auto-deployed bots reached (%d).", num_auto_bots)
            return results

        # 2. Run Screener
        try:
            from app.services.scanner.market_scanner import MarketScannerService
            scanner = MarketScannerService(self.bot_manager.oms.feed if hasattr(self.bot_manager, "oms") else None)
            scan_res = await scanner.scan(self.watchlist, signal_filter="any")
            rows = scan_res.get("rows", [])
            results["scanned"] = len(rows)
        except Exception as exc:
            logger.error("ScannerDeployAgent failed to scan: %s", exc)
            return results

        # 3. Filter and Rank
        candidates = rank_scan_rows(
            rows,
            min_confidence=SCANNER_DEPLOY_MIN_CONFIDENCE,
            min_score=SCANNER_DEPLOY_MIN_SCORE,
        )
        results["candidates"] = len(candidates)

        existing_symbols = active_bot_symbols(
            self.bot_manager,
            strategy=SCANNER_DEPLOY_STRATEGY,
            timeframe=SCANNER_DEPLOY_TIMEFRAME
        )
        all_active_symbols = {b.get("symbol") for b in active_bots if b.get("symbol")}

        for row in candidates:
            # Re-check capacity per candidate
            if total_allocated >= SCANNER_DEPLOY_MAX_PORTFOLIO_ALLOCATION or num_auto_bots >= SCANNER_DEPLOY_MAX_CONCURRENT_BOTS:
                break

            symbol = _normalize_crypto_watch_symbol(row.get("symbol") or "")
            if not symbol:
                continue

            if symbol in existing_symbols:
                results["skipped"].append({"symbol": symbol, "reason": "Already deployed"})
                continue

            # 4. Correlation Gate
            basket = list(all_active_symbols) + [symbol]
            feed = getattr(self.bot_manager.oms, "feed", None)
            
            try:
                summary = summarize_basket_correlation(basket, feed=feed)
                high_pairs = summary.get("high_pairs") or []
                corr_reject = False
                max_corr = 0.0
                for pair in high_pairs:
                    if symbol in (pair["a"], pair["b"]):
                        corr = pair["correlation"]
                        if corr > max_corr:
                            max_corr = corr
                        if corr >= SCANNER_DEPLOY_MAX_CORRELATION:
                            corr_reject = True
                            results["skipped"].append({"symbol": symbol, "reason": f"High correlation ({corr:.2f}) with {pair['a'] if pair['b']==symbol else pair['b']}"})
                            break
                if corr_reject:
                    continue
            except Exception as exc:
                logger.warning("ScannerDeployAgent correlation check failed for %s: %s", symbol, exc)

            # 5. Backtest Validation Gate
            # We run a quick 7-day backtest.
            if not self.backtester:
                results["skipped"].append({"symbol": symbol, "reason": "Backtester disabled/missing"})
                continue

            backtest_cfg = {
                "symbol": symbol,
                "strategy": SCANNER_DEPLOY_STRATEGY,
                "timeframe": SCANNER_DEPLOY_TIMEFRAME,
                "days": 7,
            }
            try:
                bt_result = await self.backtester.run_backtest(backtest_cfg)
                bt_metrics = bt_result.get("metrics", {})
                pnl = bt_metrics.get("net_profit", 0.0)
                win_rate = bt_metrics.get("win_rate", 0.0)
                
                if pnl <= 0 or win_rate <= 50.0:
                    results["skipped"].append({"symbol": symbol, "reason": f"Backtest failed: PnL={pnl:.2f}, WinRate={win_rate:.1f}%"})
                    continue
            except Exception as exc:
                logger.warning("ScannerDeployAgent backtest failed for %s: %s", symbol, exc)
                results["skipped"].append({"symbol": symbol, "reason": f"Backtest error: {exc}"})
                continue

            # 6. Dynamic Allocation & Deployment
            conf = float(row.get("confidence", 0.0))
            alloc_multiplier = min(1.5, max(0.5, conf))
            allocation = SCANNER_DEPLOY_BASE_ALLOCATION * alloc_multiplier
            
            # Ensure we don't exceed max portfolio capacity with this allocation
            allocation = min(allocation, SCANNER_DEPLOY_MAX_PORTFOLIO_ALLOCATION - total_allocated)
            if allocation <= 0:
                continue

            bot_cfg = {
                "pipeline_source": "scanner",
                "min_confidence": SCANNER_DEPLOY_MIN_CONFIDENCE,
                "regime_routing_enabled": True,
            }
            if SCANNER_DEPLOY_AUTO_STOP_ON_DD:
                bot_cfg["auto_stop_loss_pct"] = SCANNER_DEPLOY_MAX_DRAWDOWN_PCT

            try:
                bot_id = await self.bot_manager.create_bot(
                    SCANNER_DEPLOY_STRATEGY,
                    symbol,
                    SCANNER_DEPLOY_TIMEFRAME,
                    allocation,
                    bot_cfg,
                )
                
                obs1 = Observation("screener_signal", "positive", conf, f"Score: {row.get('score')} Conf: {conf:.2f}")
                obs2 = Observation("correlation_check", "positive", 0.9, f"Max correlation to active basket: {max_corr:.2f}")
                obs3 = Observation("backtest_validation", "positive", 0.85, f"7d PnL: {pnl:.2f}, WinRate: {win_rate:.1f}%")
                
                reasoning = AgentReasoning(
                    observations=[obs1, obs2, obs3],
                    synthesis=f"High confidence setup on {symbol}. Backtest validated positive edge. Dynamically allocated ${allocation:.2f}.",
                    decision="DEPLOY",
                    confidence=conf,
                    recommendation_strength="strong",
                )

                if self.agent_event_bus:
                    from app.services.agent.agent_event_bus import AgentEvent
                    await self.agent_event_bus.publish(
                        AgentEvent(
                            source_agent="SCANNER_DEPLOY",
                            event_type="BOT_DEPLOYED",
                            payload={"bot_id": bot_id, "symbol": symbol, "allocation": allocation},
                            timestamp=time.time(),
                            reasoning=reasoning,
                        )
                    )

                results["deployed"].append({"bot_id": bot_id, "symbol": symbol, "allocation": allocation})
                total_allocated += allocation
                num_auto_bots += 1
                all_active_symbols.add(symbol)
                logger.info("ScannerDeployAgent deployed %s on %s with $%.2f allocation.", SCANNER_DEPLOY_STRATEGY, symbol, allocation)

            except Exception as exc:
                logger.error("ScannerDeployAgent failed to deploy bot for %s: %s", symbol, exc)
                results["skipped"].append({"symbol": symbol, "reason": f"Deploy error: {exc}"})

        return results
