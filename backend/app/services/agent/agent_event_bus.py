"""Pub/sub for inter-agent coordination with durable SQLite/Postgres history."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import redis.asyncio as redis
from app.services.agent.reasoning import AgentReasoning

logger = logging.getLogger(__name__)

Handler = Callable[["AgentEvent"], Awaitable[None]]


@dataclass
class AgentEvent:
    source_agent: str
    event_type: str
    payload: dict[str, Any]
    timestamp: float
    reasoning: AgentReasoning | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "source_agent": self.source_agent,
                "event_type": self.event_type,
                "payload": self.payload,
                "timestamp": self.timestamp,
                "reasoning": self.reasoning.to_dict() if self.reasoning else None,
            }
        )

    @classmethod
    def from_dict(cls, parsed: dict[str, Any]) -> "AgentEvent":
        if not isinstance(parsed, dict):
            raise ValueError("AgentEvent.from_dict expects a dict")
        reasoning = (
            AgentReasoning.from_dict(parsed["reasoning"]) if parsed.get("reasoning") else None
        )
        payload = parsed.get("payload")
        return cls(
            source_agent=str(parsed.get("source_agent") or ""),
            event_type=str(parsed.get("event_type") or ""),
            payload=payload if isinstance(payload, dict) else {},
            timestamp=float(parsed.get("timestamp") or 0.0),
            reasoning=reasoning,
        )

    @classmethod
    def from_json(cls, data: str | bytes) -> "AgentEvent":
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return cls.from_dict(json.loads(data))


def _row_get(row: Any, key: str, idx: int) -> Any:
    """Read a column from sqlite3.Row / psycopg dict_row / plain tuple rows."""
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return row[idx]


class AgentEventBus:
    """Pub/sub event bus for inter-agent communication (local, optional Redis).

    Construction is sync-safe. When Redis is configured, call ``await start()``
    from a running event loop (server startup) so the listener can be scheduled.

    Every ``publish`` is also persisted to the ``agent_events`` table so
    ``recent_events`` polls (e.g. ``BOT_PAUSED`` in regime rotation) survive
    restarts; the in-memory ring buffer remains as a fallback when the DB is
    unavailable. Subscribers registered via ``subscribe`` are invoked on both
    the local path and the Redis listener path.
    """

    def __init__(self, max_history: int = 1000):
        self._handlers: dict[str, list[Handler]] = {}
        self._history: deque[AgentEvent] = deque(maxlen=max_history)
        redis_url = (os.environ.get("REDIS_URL") or "").strip()
        self._redis = redis.from_url(redis_url) if redis_url else None
        self._pubsub = self._redis.pubsub() if self._redis else None
        self._listener_task: asyncio.Task | None = None
        # Cap concurrent handler tasks per publish burst (MEMORY #16).
        self._handler_limit = max(1, int(os.environ.get("AGENT_EVENT_HANDLER_CONCURRENCY", "32")))
        self._handler_sem: asyncio.Semaphore | None = None
        self._db_ready = False

    # ------------------------------------------------------------------
    # Durable history (agent_events table)
    # ------------------------------------------------------------------

    def _ensure_db(self) -> bool:
        if self._db_ready:
            return True
        try:
            from app.database import ensure_agent_event_tables

            ensure_agent_event_tables()
            self._db_ready = True
        except Exception as exc:
            logger.debug("AgentEventBus DB schema ensure skipped: %s", exc)
        return self._db_ready

    def _persist_event(self, event: AgentEvent) -> None:
        """Best-effort insert into agent_events — never breaks publish."""
        if not self._ensure_db():
            return
        try:
            from app.db.connection import get_connection

            conn = get_connection()
            try:
                reasoning_json = (
                    json.dumps(event.reasoning.to_dict()) if event.reasoning else None
                )
                conn.cursor().execute(
                    """
                    INSERT INTO agent_events
                        (event_type, source, bot_id, payload, reasoning, ts, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_type,
                        event.source_agent,
                        event.payload.get("bot_id"),
                        json.dumps(event.payload),
                        reasoning_json,
                        float(event.timestamp),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("AgentEventBus DB persist failed (%s): %s", event.event_type, exc)

    def _recent_events_db(self, event_type: str, cutoff_time: float) -> list[AgentEvent]:
        from app.db.connection import get_connection

        conn = get_connection()
        try:
            rows = conn.cursor().execute(
                """
                SELECT event_type, source, payload, reasoning, ts
                FROM agent_events
                WHERE event_type = ? AND ts >= ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (event_type, cutoff_time, self._history.maxlen or 1000),
            ).fetchall()
        finally:
            conn.close()

        events: list[AgentEvent] = []
        for row in reversed(rows):
            try:
                raw_payload = _row_get(row, "payload", 2)
                raw_reasoning = _row_get(row, "reasoning", 3)
                payload = json.loads(raw_payload) if raw_payload else {}
                reasoning = (
                    AgentReasoning.from_dict(json.loads(raw_reasoning))
                    if raw_reasoning
                    else None
                )
                events.append(
                    AgentEvent(
                        source_agent=str(_row_get(row, "source", 1) or ""),
                        event_type=str(_row_get(row, "event_type", 0) or ""),
                        payload=payload if isinstance(payload, dict) else {},
                        timestamp=float(_row_get(row, "ts", 4) or 0.0),
                        reasoning=reasoning,
                    )
                )
            except Exception as exc:
                logger.debug("AgentEventBus skipping unreadable event row: %s", exc)
        return events

    def _get_handler_sem(self) -> asyncio.Semaphore:
        if self._handler_sem is None:
            self._handler_sem = asyncio.Semaphore(self._handler_limit)
        return self._handler_sem

    async def _spawn_handler(self, handler: Handler, event: AgentEvent) -> None:
        """Run handler under concurrency cap without unbounded Task pile-up."""
        sem = self._get_handler_sem()
        # Acquire before create_task so bursts wait here instead of queuing Tasks.
        await sem.acquire()

        async def _run() -> None:
            try:
                await self._safe_run(handler, event)
            finally:
                sem.release()

        asyncio.create_task(_run())

    async def start(self) -> None:
        """Schedule Redis pub/sub listener once a running loop is available."""
        if not self._pubsub:
            return
        if self._listener_task is not None and not self._listener_task.done():
            return
        self._listener_task = asyncio.create_task(
            self._start_listening(),
            name="agent_event_bus_listener",
        )
        logger.info("AgentEventBus Redis listener started")

    async def stop(self) -> None:
        """Cancel the Redis listener (best-effort)."""
        task = self._listener_task
        self._listener_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe("agent_events")
                await self._pubsub.aclose()
            except Exception as exc:
                logger.debug("AgentEventBus pubsub close skipped: %s", exc)
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception as exc:
                logger.debug("AgentEventBus redis close skipped: %s", exc)

    async def _start_listening(self) -> None:
        assert self._pubsub is not None
        await self._pubsub.subscribe("agent_events")
        async for message in self._pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                event = AgentEvent.from_json(message["data"])
                self._history.append(event)
                for handler in self._handlers.get(event.event_type, []):
                    await self._spawn_handler(handler, event)
            except Exception as exc:
                logger.error("Failed to parse or handle Redis AgentEvent: %s", exc)

    async def _safe_run(self, handler: Handler, ev: AgentEvent) -> None:
        try:
            await handler(ev)
        except Exception as exc:
            logger.error("AgentEventBus handler error on %s: %s", ev.event_type, exc)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """Register a handler for ``event_type`` (optional; history still updated on publish)."""
        self._handlers.setdefault(event_type, []).append(handler)

    async def publish(self, event: AgentEvent) -> None:
        """Publish an event to Redis (or locally if Redis is disabled).

        Local mode appends to the ring buffer used by ``recent_events`` even when
        no subscribers are registered. Every publish is also written to the
        durable ``agent_events`` table (best-effort).
        """
        self._persist_event(event)
        if self._redis:
            await self._redis.publish("agent_events", event.to_json())
            return

        # Fallback to local memory bus (must run under an event loop).
        self._history.append(event)
        for handler in self._handlers.get(event.event_type, []):
            await self._spawn_handler(handler, event)

    def recent_events(self, event_type: str, lookback_sec: float) -> list[AgentEvent]:
        """Fetch recently published events of a certain type within the lookback window.

        Reads from the durable ``agent_events`` table so history survives a
        restart; falls back to the in-memory ring buffer if the DB read fails.
        """
        cutoff_time = time.time() - lookback_sec
        if self._ensure_db():
            try:
                return self._recent_events_db(event_type, cutoff_time)
            except Exception as exc:
                logger.debug("AgentEventBus DB read failed, using memory: %s", exc)
        return [
            e
            for e in self._history
            if e.event_type == event_type and e.timestamp >= cutoff_time
        ]
