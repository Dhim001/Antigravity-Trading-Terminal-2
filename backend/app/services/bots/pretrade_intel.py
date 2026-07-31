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
from app.services.bots.pretrade_context import (
    apply_failures_streak,
    build_trade_state_snapshot,
    consecutive_loss_count,
)

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

        streak_action: dict[str, Any] | None = None
        trade_state: dict[str, Any] | None = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            one_day_ago = time.time() - (PRETRADE_SETUP_LOOKBACK_HOURS * 3600.0)
            # Fetch enough exits for severe-streak stepping (not just fail_limit).
            fetch_n = max(PRETRADE_SETUP_FAIL_LIMIT, 10)
            try:
                fetch_n = max(
                    fetch_n,
                    int(bot_config.get("pretrade_streak_severe_limit") or 5),
                    int(bot_config.get("max_consecutive_losses") or 5),
                )
            except (TypeError, ValueError):
                pass
            try:
                cursor.execute(
                    """
                    SELECT t.pnl FROM bot_trades t
                    JOIN bots b ON t.bot_id = b.id
                    WHERE t.symbol = ? AND b.strategy = ? AND t.timestamp >= datetime(?, 'unixepoch') AND t.is_exit = 1
                    ORDER BY t.timestamp DESC LIMIT ?
                    """,
                    (symbol, strategy, one_day_ago, fetch_n),
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
                        (bot["id"], one_day_ago, fetch_n),
                    )
                    rows = cursor.fetchall()
                except Exception as fallback_exc:
                    logger.error("PreTradeIntel fallback query failed: %s", fallback_exc)
                    uncertainty_sources.append(f"trade_history_query_failed: {str(fallback_exc)}")
                    rows = []
            # Win-rate in lookback (for ML / agent awareness).
            wins = 0
            losses = 0
            try:
                cursor.execute(
                    """
                    SELECT t.pnl FROM bot_trades t
                    JOIN bots b ON t.bot_id = b.id
                    WHERE t.symbol = ? AND b.strategy = ? AND t.timestamp >= datetime(?, 'unixepoch') AND t.is_exit = 1
                    """,
                    (symbol, strategy, one_day_ago),
                )
                for r in cursor.fetchall() or []:
                    p = float(r[0] or 0.0)
                    if p > 0:
                        wins += 1
                    elif p < 0:
                        losses += 1
            except Exception:
                pass
            conn.close()

            pnls = [float(r[0] or 0.0) for r in rows]
            streak = consecutive_loss_count(pnls, newest_first=True)
            total_closed = wins + losses
            win_rate = (wins / total_closed) if total_closed > 0 else 0.5
            hours_since_loss = PRETRADE_SETUP_LOOKBACK_HOURS
            if pnls and pnls[0] < 0:
                hours_since_loss = 0.0

            fail_limit = int(
                bot_config.get("pretrade_setup_fail_limit", PRETRADE_SETUP_FAIL_LIMIT)
            )
            streak_action = apply_failures_streak(
                pnls,
                bot_config=bot_config,
                setup_fail_limit=fail_limit,
                newest_first=True,
            )
            if streak_action:
                if streak_action["verdict"] == "VETO":
                    verdict = "VETO"
                elif verdict != "VETO":
                    verdict = "REDUCE_SIZE"
                vetoes.extend(streak_action.get("vetoes") or [])
                size_multiplier = min(
                    size_multiplier, float(streak_action.get("size_multiplier") or 0.5)
                )
                observations.append(
                    Observation(
                        "failures_streak",
                        "danger",
                        0.90,
                        streak_action.get("reason") or "loss streak",
                    )
                )
            else:
                observations.append(
                    Observation(
                        "failures_streak",
                        "positive",
                        0.90,
                        "No sustained loss streak.",
                    )
                )
            trade_state = build_trade_state_snapshot(
                consecutive_losses=streak,
                losses_in_lookback=losses,
                last_pretrade_verdict=None,
                streak_size_mult=float(
                    streak_action.get("size_multiplier") if streak_action else 1.0
                ),
                cool_until_ts=bot.get("_pretrade_streak_cool_until"),
                win_rate_24h=win_rate,
                hours_since_last_loss=hours_since_loss,
            )
        except Exception as exc:
            logger.error("PreTradeIntel failure streak check failed: %s", exc)
            uncertainty_sources.append(f"failure_streak_check_failed: {str(exc)}")
            observations.append(Observation("failure_streak", "neutral", 0.0, "Check failed due to error."))

        # 4. News Sentiment Divergence check
        try:
            sentiment = get_aggregate_sentiment(symbol, lookback_hours=24.0)
            # Store returns aggregate_score/mention_count; accept legacy aliases too.
            mentions = 0
            if sentiment:
                mentions = int(
                    sentiment.get("mentions")
                    if sentiment.get("mentions") is not None
                    else (sentiment.get("mention_count") or 0)
                )
            if sentiment and mentions >= PRETRADE_SENTIMENT_MIN_MENTIONS:
                score = float(
                    sentiment.get("score")
                    if sentiment.get("score") is not None
                    else (sentiment.get("aggregate_score") or 0.0)
                )
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
                score_disp = sentiment.get("score", sentiment.get("aggregate_score", 0))
                uncertainty_sources.append(f"Not enough sentiment mentions ({mentions}) for high confidence.")
                observations.append(Observation("sentiment", "neutral", 0.60, f"Score {score_disp:.2f} but low volume ({mentions})."))
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

        # 6. VAE regime meta-layer (proposal §2.6)
        try:
            from app.services.bots.strategies_vae_regime import (
                assess_vae_regime_for_meta,
                vae_regime_gate_enabled,
            )

            if vae_regime_gate_enabled(bot_config):
                feed = getattr(self.bot_manager.oms, "feed", None)
                ohlcv = get_bot_candles(
                    symbol, feed, timeframe=bot.get("timeframe", "1m"), min_bars=50
                )
                row_for_vae = None
                lookback_rows: list[dict] = []
                if ohlcv and len(ohlcv) >= 20:
                    df = self.bot_manager.screener.process_candles(
                        symbol, ohlcv, bot_config, "VAE_REGIME_DETECTOR"
                    )
                    if df is not None and not getattr(df, "empty", True):
                        lookback_rows = [dict(r) for r in df.iloc[-25:-1].to_dict("records")]
                        row_for_vae = dict(df.iloc[-1])
                        row_for_vae.setdefault("_symbol", symbol)
                if row_for_vae is None and isinstance(signal_data, dict):
                    # Fallback: score from the signal bar when candles unavailable.
                    row_for_vae = dict(signal_data)
                    row_for_vae.setdefault("_symbol", symbol)

                if row_for_vae is not None:
                    assessment = assess_vae_regime_for_meta(
                        symbol,
                        row_for_vae,
                        lookback_rows=lookback_rows,
                        config=bot_config,
                    )
                    if assessment.regime_action == "suppress":
                        verdict = "VETO"
                        vetoes.append(f"vae_regime_unstable: {assessment.reason}")
                        observations.append(
                            Observation(
                                "vae_regime",
                                "danger",
                                0.90,
                                assessment.reason,
                            )
                        )
                    elif assessment.regime_action == "caution":
                        if verdict != "VETO":
                            verdict = "REDUCE_SIZE"
                        vetoes.append(f"vae_regime_caution: {assessment.reason}")
                        size_multiplier = min(size_multiplier, PRETRADE_REDUCE_SIZE_FACTOR)
                        observations.append(
                            Observation(
                                "vae_regime",
                                "danger",
                                0.80,
                                assessment.reason,
                            )
                        )
                    elif assessment.regime_action == "amplify":
                        observations.append(
                            Observation(
                                "vae_regime",
                                "positive",
                                0.85,
                                assessment.reason,
                            )
                        )
                    elif assessment.regime_action == "skip":
                        uncertainty_sources.append("VAE regime model unavailable.")
                        observations.append(
                            Observation(
                                "vae_regime",
                                "neutral",
                                0.40,
                                assessment.reason,
                            )
                        )
                    else:
                        observations.append(
                            Observation(
                                "vae_regime",
                                "positive",
                                0.85,
                                assessment.reason,
                            )
                        )
        except Exception as exc:
            logger.error("PreTradeIntel VAE regime check failed: %s", exc)
            uncertainty_sources.append(f"vae_regime_check_failed: {str(exc)}")
            observations.append(Observation("vae_regime", "neutral", 0.0, "Check failed due to error."))

        # Resolve final verdict: structural risks stay hard VETO.
        # failures_streak defaults to REDUCE_SIZE (adaptive); only VETO when
        # streak_action escalates (explicit veto mode or sentinel max).
        hard_hit = any(
            v.startswith(
                ("event_policy", "price_gap", "market_anomaly", "vae_regime_unstable")
            )
            for v in vetoes
        )
        streak_veto = bool(streak_action and streak_action.get("verdict") == "VETO")
        if hard_hit or streak_veto:
            verdict = "VETO"
            size_multiplier = 0.0
        elif (
            "correlation_exposure" in str(vetoes)
            or "sentiment_divergence" in str(vetoes)
            or "vae_regime_caution" in str(vetoes)
            or any(v.startswith("failures_streak") for v in vetoes)
        ):
            verdict = "REDUCE_SIZE"
            if streak_action and streak_action.get("verdict") == "REDUCE_SIZE":
                size_multiplier = min(
                    size_multiplier,
                    float(streak_action.get("size_multiplier") or 0.5),
                )
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

        if trade_state is not None:
            trade_state["last_pretrade_verdict"] = verdict
            trade_state["streak_size_mult"] = float(size_multiplier)

        return {
            "verdict": verdict,
            "vetoes": vetoes,
            "size_multiplier": size_multiplier,
            "reasoning": reasoning_str,
            "reasoning_chain": agent_reasoning.to_dict(),
            "trade_state": trade_state,
            "streak_action": streak_action,
        }
