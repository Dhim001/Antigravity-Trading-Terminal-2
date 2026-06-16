"""Chart Analyst orchestrator — features, rules, optional LLM, cache, persistence."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import (
    AGENT_ENABLED,
    AGENT_LLM_COOLDOWN_SEC,
    AGENT_LLM_ENABLED,
    AGENT_LLM_MIN_CONFIDENCE,
    REDIS_URL,
)
from app.db.connection import get_connection
from app.services.agent.candle_source import get_agent_candles
from app.services.agent.feature_builder import FeatureBuilder
from app.services.agent.llm_client import summarize_insight
from app.services.agent.models import ChartAgentInsight
from app.services.agent.rule_engine import score_dataframe
from app.services.bots.screener import MarketScreenerService

logger = logging.getLogger(__name__)

BroadcastFn = Callable[[dict], Awaitable[None]] | None

_service: "ChartAnalystService | None" = None


def get_chart_analyst() -> "ChartAnalystService":
    if _service is None:
        raise RuntimeError("ChartAnalystService not initialized — call init_chart_analyst() at boot")
    return _service


def init_chart_analyst(
    screener: MarketScreenerService | None = None,
    feed: Any | None = None,
    broadcast_fn: BroadcastFn = None,
) -> "ChartAnalystService":
    global _service
    _service = ChartAnalystService(screener=screener, feed=feed, broadcast_fn=broadcast_fn)
    return _service


class ChartAnalystService:
    CACHE_TTL_SEC = 600

    def __init__(
        self,
        screener: MarketScreenerService | None = None,
        feed: Any | None = None,
        broadcast_fn: BroadcastFn = None,
    ):
        self.feed = feed
        self.broadcast_fn = broadcast_fn
        self.feature_builder = FeatureBuilder(screener)
        self._cache: dict[str, tuple[float, dict]] = {}
        self._llm_last_at: dict[str, float] = {}
        self._redis = None
        if REDIS_URL:
            try:
                import redis

                self._redis = redis.from_url(REDIS_URL)
            except Exception as exc:
                logger.debug("Redis cache unavailable for agent: %s", exc)

    def get_cached(self, symbol: str) -> dict | None:
        sym = symbol.upper()
        entry = self._cache.get(sym)
        if entry and time.monotonic() - entry[0] < self.CACHE_TTL_SEC:
            return entry[1]
        if self._redis:
            try:
                raw = self._redis.get(f"agent:insight:{sym}")
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
        return None

    def _set_cache(self, insight: ChartAgentInsight) -> None:
        payload = insight.to_dict()
        sym = insight.symbol.upper()
        self._cache[sym] = (time.monotonic(), payload)
        if self._redis:
            try:
                self._redis.setex(
                    f"agent:insight:{sym}",
                    self.CACHE_TTL_SEC,
                    json.dumps(payload),
                )
            except Exception:
                pass

    def _should_call_llm(self, insight: ChartAgentInsight, force: bool) -> bool:
        if not AGENT_LLM_ENABLED and not force:
            return False
        if insight.signal not in ("BUY", "SELL"):
            return False
        if insight.confidence < AGENT_LLM_MIN_CONFIDENCE:
            return False
        last = self._llm_last_at.get(insight.symbol.upper(), 0.0)
        if not force and (time.monotonic() - last) < AGENT_LLM_COOLDOWN_SEC:
            return False
        return True

    def persist(self, insight: ChartAgentInsight) -> None:
        from app.db.connection import is_postgres

        conn = get_connection()
        cursor = conn.cursor()
        payload = json.dumps(insight.to_dict())
        if is_postgres():
            cursor.execute(
                """
                INSERT INTO agent_insights (insight_id, symbol, bar_time, payload, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (insight_id) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    created_at = CURRENT_TIMESTAMP
                """,
                (insight.insight_id, insight.symbol, insight.bar_time, payload),
            )
        else:
            cursor.execute(
                """
                INSERT OR REPLACE INTO agent_insights
                (insight_id, symbol, bar_time, payload, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (insight.insight_id, insight.symbol, insight.bar_time, payload),
            )
        conn.commit()
        conn.close()

    def list_insights(self, symbol: str, limit: int = 20) -> list[dict]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT payload FROM agent_insights
            WHERE symbol = ?
            ORDER BY bar_time DESC
            LIMIT ?
            """,
            (symbol.upper(), limit),
        )
        rows = cursor.fetchall()
        conn.close()
        out = []
        for row in rows:
            raw = row[0] if not isinstance(row, dict) else row.get("payload")
            if raw:
                out.append(json.loads(raw) if isinstance(raw, str) else raw)
        return out

    def _audit(
        self,
        insight: ChartAgentInsight,
        *,
        llm_called: bool,
        latency_ms: float,
    ) -> None:
        logger.info(
            "agent_audit insight_id=%s symbol=%s signal=%s confidence=%.3f "
            "score=%d llm_called=%s latency_ms=%.1f",
            insight.insight_id,
            insight.symbol,
            insight.signal,
            insight.confidence,
            insight.score,
            llm_called,
            latency_ms,
        )

    async def analyze(
        self,
        symbol: str,
        *,
        candles: list[dict] | None = None,
        force_llm: bool = False,
        timeframe: str = "1m",
        broadcast: bool = True,
    ) -> ChartAgentInsight | None:
        if not AGENT_ENABLED:
            return None

        t0 = time.monotonic()
        sym = symbol.upper()

        if candles is None:
            candles = await get_agent_candles(sym, self.feed)

        df = await asyncio.to_thread(self.feature_builder.build, sym, candles)
        insight = score_dataframe(df, sym, timeframe=timeframe)
        if insight is None:
            return None

        llm_called = False
        if self._should_call_llm(insight, force_llm):
            narrative, model = await summarize_insight(insight.to_dict())
            if narrative:
                insight.narrative = narrative
                insight.model = model
                llm_called = True
                self._llm_last_at[sym] = time.monotonic()

        self._set_cache(insight)
        await asyncio.to_thread(self.persist, insight)

        latency_ms = (time.monotonic() - t0) * 1000
        self._audit(insight, llm_called=llm_called, latency_ms=latency_ms)
        try:
            from app.observability.metrics import inc, observe

            inc("agent_analyze_total", labels={"signal": insight.signal})
            observe("agent_analyze_duration_seconds", latency_ms / 1000.0)
            if llm_called:
                inc("agent_llm_calls_total")
        except Exception:
            pass

        if broadcast and self.broadcast_fn:
            from app.api.outbound import agent_insight

            await self.broadcast_fn(agent_insight(insight.to_dict()))

        return insight

    async def ensure_for_bar(
        self,
        symbol: str,
        candles: list[dict],
        bar_time: int | None,
        *,
        force_llm: bool = False,
    ) -> ChartAgentInsight | None:
        """Populate cache for the closed bar if missing or stale."""
        cached = self.get_cached(symbol)
        if cached and bar_time is not None and cached.get("bar_time") == bar_time:
            return ChartAgentInsight.from_dict(cached)
        return await self.analyze(symbol, candles=candles, force_llm=force_llm, broadcast=False)

    def symbols_to_analyze(self, bot_manager, connection_manager) -> set[str]:
        symbols: set[str] = set()
        if bot_manager and bot_manager.active_bots:
            for bot in bot_manager.active_bots.values():
                if bot.get("strategy", "").upper() == "CHART_AGENT":
                    symbols.add(bot["symbol"])
        if connection_manager and connection_manager.client_symbols:
            symbols.update(connection_manager.client_symbols.values())
        return symbols
