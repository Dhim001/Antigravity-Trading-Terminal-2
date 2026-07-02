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
    AGENT_LLM_SIM_COOLDOWN_SEC,
    REDIS_URL,
    TERMINAL_MODE,
)
from app.db.connection import get_connection
from app.services.agent.bar_time import (
    bar_times_match,
    candles_match_timeframe,
    coerce_bar_time,
)
from app.services.agent.candle_source import get_agent_candles
from app.services.agent.feature_builder import FeatureBuilder
from app.services.agent.llm.router import is_llm_available, summarize_insight
from app.services.agent.models import ChartAgentInsight, insight_cache_key
from app.services.agent.rule_engine import score_dataframe
from app.services.bots.screener import MarketScreenerService
from app.services.market.timeframes import normalize_timeframe

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
    # 3.6-B: Adaptive per-timeframe cache TTL — prevents stale insights on fast bars.
    # 1m needs very short TTL (< 2 bars); higher TFs can tolerate longer caching.
    TF_CACHE_TTL_SEC: dict[str, int] = {
        "1m":  90,    # ~1.5 bars
        "3m":  210,
        "5m":  360,   # ~1.2 bars
        "15m": 900,   # 1 bar
        "30m": 1800,
        "1h":  3600,
        "2h":  7200,
        "4h":  14400,
        "1d":  86400,
    }
    _DEFAULT_CACHE_TTL_SEC = 600  # fallback for unknown timeframes

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

    def _cache_ttl(self, timeframe: str) -> int:
        """Return the cache TTL in seconds for the given timeframe."""
        tf = str(timeframe or "1m").lower().strip()
        return self.TF_CACHE_TTL_SEC.get(tf, self._DEFAULT_CACHE_TTL_SEC)

    def get_cached(self, symbol: str, timeframe: str = "1m") -> dict | None:
        key = insight_cache_key(symbol, timeframe)
        ttl = self._cache_ttl(timeframe)
        entry = self._cache.get(key)
        if entry and time.monotonic() - entry[0] < ttl:
            return entry[1]
        if self._redis:
            try:
                raw = self._redis.get(f"agent:insight:{key}")
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
        # Legacy in-memory key (pre Phase 5)
        if normalize_timeframe(timeframe) == "1m":
            legacy = self._cache.get(symbol.upper())
            if legacy and time.monotonic() - legacy[0] < ttl:
                return legacy[1]
        return None

    def _set_cache(self, insight: ChartAgentInsight) -> None:
        payload = insight.to_dict()
        key = insight_cache_key(insight.symbol, insight.timeframe)
        self._cache[key] = (time.monotonic(), payload)
        ttl = self._cache_ttl(insight.timeframe)
        if self._redis:
            try:
                self._redis.setex(
                    f"agent:insight:{key}",
                    ttl,  # 3.6-B: use per-TF TTL for Redis as well
                    json.dumps(payload),
                )
            except Exception:
                pass

    def _llm_cooldown_sec(self) -> int:
        if TERMINAL_MODE == "SIMULATED":
            return AGENT_LLM_SIM_COOLDOWN_SEC
        return AGENT_LLM_COOLDOWN_SEC

    async def _llm_enabled(self) -> bool:
        if not AGENT_LLM_ENABLED:
            return False
        return await is_llm_available()

    def _should_call_llm(self, insight: ChartAgentInsight, force: bool) -> bool:
        if not AGENT_LLM_ENABLED and not force:
            return False
        if insight.signal not in ("BUY", "SELL"):
            return False
        if insight.confidence < AGENT_LLM_MIN_CONFIDENCE:
            return False
        llm_key = insight_cache_key(insight.symbol, insight.timeframe)
        last = self._llm_last_at.get(llm_key, 0.0)
        if not force and (time.monotonic() - last) < self._llm_cooldown_sec():
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

    def persist_deep_reasoning(self, insight_id: str, deep: dict) -> bool:
        """Merge deep_reasoning enrichment into a stored insight payload."""
        if not insight_id or not deep:
            return False
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT payload FROM agent_insights WHERE insight_id = ?",
            (insight_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return False
        raw = row[0] if not isinstance(row, dict) else row.get("payload")
        if not raw:
            conn.close()
            return False
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
        payload["deep_reasoning"] = deep
        encoded = json.dumps(payload)
        cursor.execute(
            "UPDATE agent_insights SET payload = ? WHERE insight_id = ?",
            (encoded, insight_id),
        )
        conn.commit()
        conn.close()
        sym = payload.get("symbol")
        tf = payload.get("timeframe", "1m")
        if sym:
            key = insight_cache_key(sym, tf)
            entry = self._cache.get(key)
            if entry:
                self._cache[key] = (entry[0], payload)
        return True

    def list_insights(self, symbol: str, limit: int = 20, timeframe: str | None = None) -> list[dict]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT payload FROM agent_insights
            WHERE symbol = ?
            ORDER BY bar_time DESC
            LIMIT ?
            """,
            (symbol.upper(), limit * 3 if timeframe else limit),
        )
        rows = cursor.fetchall()
        conn.close()
        tf_filter = normalize_timeframe(timeframe) if timeframe else None
        out = []
        for row in rows:
            raw = row[0] if not isinstance(row, dict) else row.get("payload")
            if not raw:
                continue
            item = json.loads(raw) if isinstance(raw, str) else raw
            if tf_filter and normalize_timeframe(item.get("timeframe", "1m")) != tf_filter:
                continue
            out.append(item)
            if len(out) >= limit:
                break
        return out

    def _audit(
        self,
        insight: ChartAgentInsight,
        *,
        llm_called: bool,
        latency_ms: float,
        llm_provider: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        logger.info(
            "agent_audit insight_id=%s symbol=%s timeframe=%s signal=%s confidence=%.3f "
            "score=%d llm_called=%s llm_provider=%s llm_model=%s latency_ms=%.1f",
            insight.insight_id,
            insight.symbol,
            insight.timeframe,
            insight.signal,
            insight.confidence,
            insight.score,
            llm_called,
            llm_provider or "—",
            llm_model or "—",
            latency_ms,
        )

    async def analyze(
        self,
        symbol: str,
        *,
        candles: list[dict] | None = None,
        force_llm: bool = False,
        timeframe: str = "1m",
        bar_time: int | None = None,
        llm_model: str | None = None,
        broadcast: bool = True,
    ) -> ChartAgentInsight | None:
        if not AGENT_ENABLED:
            return None

        t0 = time.monotonic()
        sym = symbol.upper()
        tf = normalize_timeframe(timeframe) if timeframe and timeframe != "tick" else "1m"
        target_bar = coerce_bar_time(bar_time)

        if candles is None:
            candles = await get_agent_candles(sym, self.feed, timeframe=tf)
        elif not candles_match_timeframe(candles, tf):
            logger.warning(
                "Chart analyst %s %s: candle spacing looks like 1m — refetching native series",
                sym,
                tf,
            )
            candles = await get_agent_candles(sym, self.feed, timeframe=tf)

        if candles and not candles_match_timeframe(candles, tf):
            logger.warning(
                "Chart analyst %s %s: candle series does not match timeframe — skipping analyze",
                sym,
                tf,
            )
            return None

        if not candles or len(candles) < 50:
            logger.info(
                "Chart analyst skipped %s %s: only %s bars (need >= 50)",
                sym,
                tf,
                len(candles or []),
            )
            return None

        df = await asyncio.to_thread(self.feature_builder.build, sym, candles)
        insight = score_dataframe(
            df,
            sym,
            timeframe=tf,
            expected_bar_time=target_bar,
        )
        if insight is None:
            return None

        llm_called = False
        llm_provider = None
        if self._should_call_llm(insight, force_llm):
            if await self._llm_enabled() or force_llm:
                narrative, model, provider = await summarize_insight(insight.to_dict(), model=llm_model)
                if narrative:
                    insight.narrative = narrative
                    insight.model = model
                    llm_called = True
                    llm_provider = provider
                    self._llm_last_at[insight_cache_key(sym, tf)] = time.monotonic()

        self._set_cache(insight)
        await asyncio.to_thread(self.persist, insight)

        latency_ms = (time.monotonic() - t0) * 1000
        self._audit(
            insight,
            llm_called=llm_called,
            latency_ms=latency_ms,
            llm_provider=llm_provider,
            llm_model=insight.model,
        )
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
        timeframe: str = "1m",
        force_llm: bool = False,
        llm_model: str | None = None,
    ) -> ChartAgentInsight | None:
        """Populate cache for the closed bar if missing or stale."""
        tf = normalize_timeframe(timeframe) if timeframe and timeframe != "tick" else "1m"
        target = coerce_bar_time(bar_time)
        cached = self.get_cached(symbol, timeframe=tf)
        if (
            cached
            and target is not None
            and bar_times_match(cached.get("bar_time"), target)
            and normalize_timeframe(cached.get("timeframe", "1m")) == tf
        ):
            return ChartAgentInsight.from_dict(cached)
        insight = await self.analyze(
            symbol,
            candles=candles,
            force_llm=force_llm,
            llm_model=llm_model,
            timeframe=tf,
            bar_time=target,
            broadcast=False,
        )
        if insight is None or target is None:
            return insight
        if not bar_times_match(insight.bar_time, target):
            logger.warning(
                "Chart analyst ensure_for_bar %s %s: insight bar_time %s != target %s",
                symbol.upper(),
                tf,
                insight.bar_time,
                target,
            )
            return None
        return insight

    def symbols_to_analyze(self, bot_manager, connection_manager) -> set[str]:
        """Symbols needing 1m watchlist analysis on bar close (legacy hook)."""
        symbols: set[str] = set()
        if bot_manager and bot_manager.active_bots:
            for bot in bot_manager.active_bots.values():
                if bot.get("strategy", "").upper() == "CHART_AGENT":
                    if _bot_bar_timeframe(bot) == "1m":
                        symbols.add(bot["symbol"])
        if connection_manager and connection_manager.client_symbols:
            symbols.update(connection_manager.client_symbols.values())
        return symbols

    def chart_agent_targets(self, bot_manager) -> list[tuple[str, str]]:
        """(symbol, timeframe) pairs for active CHART_AGENT bar-close bots."""
        targets: list[tuple[str, str]] = []
        if not bot_manager or not bot_manager.active_bots:
            return targets
        seen: set[tuple[str, str]] = set()
        for bot in bot_manager.active_bots.values():
            if bot.get("status") != "RUNNING":
                continue
            if bot.get("strategy", "").upper() != "CHART_AGENT":
                continue
            if bot.get("execution_mode", "BAR_CLOSE") == "TICK":
                continue
            sym = bot["symbol"]
            tf = _bot_bar_timeframe(bot)
            key = (sym, tf)
            if key not in seen:
                seen.add(key)
                targets.append(key)
        return targets


def _bot_bar_timeframe(bot: dict) -> str:
    raw = (bot.get("timeframe") or bot.get("config", {}).get("timeframe") or "1m").strip()
    try:
        return normalize_timeframe(raw)
    except ValueError:
        return "1m"
