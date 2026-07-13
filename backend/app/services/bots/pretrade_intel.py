"""Pre-Trade Intelligence Agent — last-mile entry gating for risk mitigation."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import (
    PRETRADE_GAP_VETO_PCT,
    PRETRADE_INTEL_ENABLED,
    PRETRADE_REDUCE_SIZE_FACTOR,
    PRETRADE_SENTIMENT_MIN_MENTIONS,
    PRETRADE_SENTIMENT_THRESHOLD,
    PRETRADE_SETUP_FAIL_LIMIT,
    PRETRADE_SETUP_LOOKBACK_HOURS,
)
from app.database import get_connection
from app.services.agent.anomaly_detector import detect_bar_anomaly
from app.services.agent.bar_time import coerce_bar_time
from app.services.agent.reasoning import AgentReasoning, Observation
from app.services.altdata.event_policy import check_entry_gates
from app.services.altdata.store import get_aggregate_sentiment
from app.services.bots.candle_source import get_bot_candles
from app.services.bots.correlation import summarize_basket_correlation
from app.services.bots.portfolio_risk import list_bot_exposures

logger = logging.getLogger(__name__)


class PreTradeIntel:
    def __init__(self, bot_manager: Any, agent_event_bus: Any | None = None) -> None:
        self.bot_manager = bot_manager
        self.agent_event_bus = agent_event_bus

    async def evaluate(
        self,
        bot: dict[str, Any],
        side: str,
        price: float,
        signal_data: dict[str, Any],
        bar_time: Any,
    ) -> dict[str, Any]:
        """Perform a multi-source validation scan before executing an entry order.

        Returns a verdict dict:
        {
            "verdict": "CONFIRM" | "VETO" | "REDUCE_SIZE",
            "vetoes": list of violation reasons,
            "size_multiplier": float (e.g. 0.5),
            "reasoning": string summarizing the finding
        }
        """
        observations: list[Observation] = []
        vetoes: list[str] = []
        uncertainty_sources: list[str] = []
        verdict = "CONFIRM"
        size_multiplier = 1.0

        if not PRETRADE_INTEL_ENABLED:
            return {
                "verdict": verdict,
                "vetoes": vetoes,
                "size_multiplier": size_multiplier,
                "reasoning": "Pre-Trade Intelligence is disabled.",
                "reasoning_chain": None
            }

        symbol = bot["symbol"]
        strategy = bot["strategy"]
        bot_config = bot.get("config") or {}

        # Coerce bar_time to normalize seconds vs milliseconds
        ts_sec = coerce_bar_time(bar_time) or int(time.time())

        # 1. Macro & Corporate Event Proximity Gate
        try:
            gate_ok, gate_reason, gate_kind = check_entry_gates(
                symbol, ts_sec, bot_config, is_exit=False
            )
            if not gate_ok:
                verdict = "VETO"
                vetoes.append(f"event_policy_{gate_kind or 'unknown'}: {gate_reason}")
                observations.append(Observation("event_policy", "danger", 0.95, gate_reason))
            else:
                observations.append(Observation("event_policy", "positive", 0.95, "No macro event conflicts."))
        except Exception as exc:
            logger.error("PreTradeIntel event check failed: %s", exc)
            uncertainty_sources.append(f"event_check_failed: {str(exc)}")
            observations.append(Observation("events", "neutral", 0.0, "Check failed due to error."))

        # 2. Correlated Exposure Risk Check
        try:
            bot_exposures = list_bot_exposures()
            active_symbols = list({row["symbol"] for row in bot_exposures if row["symbol"] != symbol})
            if active_symbols:
                basket = active_symbols + [symbol]
                feed = getattr(self.bot_manager.oms, "feed", None)
                summary = summarize_basket_correlation(basket, feed=feed)
                high_pairs = summary.get("high_pairs") or []
                
                has_corr_risk = False
                for pair in high_pairs:
                    if symbol in (pair["a"], pair["b"]):
                        corr = pair["correlation"]
                        if corr >= 0.7:
                            other = pair["b"] if pair["a"] == symbol else pair["a"]
                            for row in bot_exposures:
                                if row["symbol"] == other:
                                    other_size = row["size"]
                                    # Matching direction triggers size reduction
                                    if (side == "BUY" and other_size > 0) or (side == "SELL" and other_size < 0):
                                        verdict = "REDUCE_SIZE"
                                        vetoes.append(f"correlation_exposure: {other} (corr={corr:.2f})")
                                        size_multiplier = min(size_multiplier, PRETRADE_REDUCE_SIZE_FACTOR)
                                        observations.append(Observation("correlation_exposure", "danger", 0.85, f"High directional correlation ({corr:.2f}) with {other}"))
                                        has_corr_risk = True
                if not has_corr_risk:
                    observations.append(Observation("correlation_exposure", "neutral", 0.85, "No high directional correlation risk detected."))
            else:
                observations.append(Observation("correlation_exposure", "positive", 0.85, "No other active positions to correlate with."))
        except Exception as exc:
            logger.error("PreTradeIntel correlation check failed: %s", exc)
            uncertainty_sources.append(f"correlation_check_failed: {str(exc)}")
            observations.append(Observation("portfolio_correlation", "neutral", 0.0, "Check failed due to error."))

        try:
            conn = get_connection()
            cursor = conn.cursor()
            one_day_ago = time.time() - (PRETRADE_SETUP_LOOKBACK_HOURS * 3600.0)
            try:
                cursor.execute(
                    """
                    SELECT t.pnl FROM bot_trades t
                    JOIN bots b ON t.bot_id = b.id
                    WHERE t.symbol = ? AND b.strategy = ? AND t.timestamp >= datetime(?, 'unixepoch') AND t.is_exit = 1
                    ORDER BY t.timestamp DESC LIMIT ?
                    """,
                    (symbol, strategy, one_day_ago, PRETRADE_SETUP_FAIL_LIMIT),
                )
                rows = cursor.fetchall()
            except Exception:
                try:
                    cursor.execute(
                        """
                        SELECT pnl FROM bot_trades 
                        WHERE bot_id = ? AND timestamp >= datetime(?, 'unixepoch') AND is_exit = 1
                        ORDER BY timestamp DESC LIMIT ?
                        """,
                        (bot["id"], one_day_ago, PRETRADE_SETUP_FAIL_LIMIT),
                    )
                    rows = cursor.fetchall()
                except Exception as fallback_exc:
                    logger.error("PreTradeIntel fallback query failed: %s", fallback_exc)
                    uncertainty_sources.append(f"trade_history_query_failed: {str(fallback_exc)}")
                    rows = []
            conn.close()

            if len(rows) >= PRETRADE_SETUP_FAIL_LIMIT:
                pnls = [float(r[0] or 0.0) for r in rows]
                if all(p < 0.0 for p in pnls):
                    verdict = "VETO"
                    reason = f"{len(pnls)} losses in last {PRETRADE_SETUP_LOOKBACK_HOURS}h"
                    vetoes.append(f"failures_streak: {reason}")
                    observations.append(Observation("failures_streak", "danger", 0.90, reason))
                else:
                    observations.append(Observation("failures_streak", "positive", 0.90, "No sustained loss streak."))
            else:
                observations.append(Observation("failures_streak", "positive", 0.90, "Sufficiently low recent failures."))
        except Exception as exc:
            logger.error("PreTradeIntel failure streak check failed: %s", exc)
            uncertainty_sources.append(f"failure_streak_check_failed: {str(exc)}")
            observations.append(Observation("failure_streak", "neutral", 0.0, "Check failed due to error."))

        # 4. News Sentiment Divergence check
        try:
            sentiment = get_aggregate_sentiment(symbol, lookback_hours=24.0)
            mentions = sentiment.get("mentions", 0) if sentiment else 0
            if sentiment and mentions >= PRETRADE_SENTIMENT_MIN_MENTIONS:
                score = float(sentiment.get("score") or 0.0)
                if (side == "BUY" and score <= -PRETRADE_SENTIMENT_THRESHOLD) or (
                    side == "SELL" and score >= PRETRADE_SENTIMENT_THRESHOLD
                ):
                    verdict = "REDUCE_SIZE"
                    vetoes.append(f"sentiment_divergence: score={score:+.2f}")
                    size_multiplier = min(size_multiplier, PRETRADE_REDUCE_SIZE_FACTOR)
                    observations.append(Observation("sentiment_divergence", "danger", 0.80, f"Sentiment divergence (score {score:+.2f})"))
                else:
                    observations.append(Observation("sentiment_divergence", "positive", 0.80, f"Sentiment aligns or neutral (score {score:+.2f})"))
            elif sentiment:
                uncertainty_sources.append(f"Not enough sentiment mentions ({mentions}) for high confidence.")
                observations.append(Observation("sentiment", "neutral", 0.60, f"Score {sentiment.get('score', 0):.2f} but low volume ({mentions})."))
            else:
                uncertainty_sources.append("Sentiment data unavailable or incomplete.")
                observations.append(Observation("sentiment", "neutral", 0.50, "Data missing."))
        except Exception as exc:
            logger.error("PreTradeIntel sentiment check failed: %s", exc)
            uncertainty_sources.append(f"sentiment_check_failed: {str(exc)}")
            observations.append(Observation("sentiment", "neutral", 0.0, "Check failed due to error."))

        # 5. Price / Volatility Anomalies Check
        try:
            feed = getattr(self.bot_manager.oms, "feed", None)
            ohlcv = get_bot_candles(symbol, feed, timeframe=bot.get("timeframe", "1m"), min_bars=50)
            if ohlcv and len(ohlcv) >= 30:
                df = self.bot_manager.screener.process_candles(symbol, ohlcv, strategy="CHART_AGENT")
                if not df.empty:
                    anomaly = detect_bar_anomaly(df, len(df) - 1)
                    if anomaly.get("is_anomaly"):
                        kinds = anomaly.get("kinds") or []
                        gap_val = anomaly.get("gap_pct")
                        if gap_val is not None and gap_val >= PRETRADE_GAP_VETO_PCT:
                            verdict = "VETO"
                            vetoes.append(f"price_gap_anomaly: {gap_val:.2f}% gap")
                            observations.append(Observation("market_anomaly", "danger", 0.95, f"Price gap of {gap_val:.2f}%"))
                        elif "price_gap" in kinds or "return_spike" in kinds or "volume_spike" in kinds:
                            verdict = "VETO"
                            reason = f"market_anomaly: {', '.join(kinds)}"
                            vetoes.append(reason)
                            observations.append(Observation("market_anomaly", "danger", 0.90, reason))
                    else:
                        observations.append(Observation("market_anomaly", "positive", 0.90, "No market anomalies detected."))
            else:
                uncertainty_sources.append("Not enough bars for anomaly check.")
                observations.append(Observation("market_anomaly", "neutral", 0.50, "Not enough bars for anomaly check."))
        except Exception as exc:
            logger.error("PreTradeIntel anomaly check failed: %s", exc)
            uncertainty_sources.append(f"anomaly_check_failed: {str(exc)}")
            observations.append(Observation("market_anomaly", "neutral", 0.0, "Check failed due to error."))

        # Resolve final verdict state logic: VETO overrides REDUCE_SIZE
        if "VETO" in [verdict] or any(
            v.startswith(("event_policy", "failures_streak", "price_gap", "market_anomaly"))
            for v in vetoes
        ):
            verdict = "VETO"
            size_multiplier = 0.0
        elif "correlation_exposure" in str(vetoes) or "sentiment_divergence" in str(vetoes):
            verdict = "REDUCE_SIZE"

        reasoning_str = "; ".join(vetoes) if vetoes else "Confirmation passed."
        confidence = 0.9 if verdict == "VETO" else (0.75 if verdict == "REDUCE_SIZE" else 0.85)
        
        # recommendation_strength maps intuitively to the verdict
        if verdict == "VETO":
            recommendation_strength = "strong"
        elif verdict == "REDUCE_SIZE":
            recommendation_strength = "moderate"
        else:
            recommendation_strength = "strong" if not uncertainty_sources else "moderate"

        agent_reasoning = AgentReasoning(
            observations=observations,
            synthesis=reasoning_str,
            decision=verdict,
            confidence=confidence,
            uncertainty_sources=uncertainty_sources,
            recommendation_strength=recommendation_strength,
        )

        return {
            "verdict": verdict,
            "vetoes": vetoes,
            "size_multiplier": size_multiplier,
            "reasoning": reasoning_str,
            "reasoning_chain": agent_reasoning.to_dict(),
        }
