"""In-memory pub/sub for inter-agent coordination."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass
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
    def from_json(cls, data: str | bytes) -> "AgentEvent":
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        parsed = json.loads(data)
        reasoning = (
            AgentReasoning.from_dict(parsed["reasoning"]) if parsed.get("reasoning") else None
        )
        return cls(
            source_agent=parsed["source_agent"],
            event_type=parsed["event_type"],
            payload=parsed["payload"],
            timestamp=parsed["timestamp"],
            reasoning=reasoning,
        )


class AgentEventBus:
    """Pub/sub event bus for inter-agent communication (local, optional Redis).

    Construction is sync-safe. When Redis is configured, call ``await start()``
    from a running event loop (server startup) so the listener can be scheduled.

    Production today mostly polls ``recent_events`` (e.g. ``BOT_PAUSED`` in
    regime rotation). ``subscribe`` handlers are supported but none are
    registered yet — keep publishing so history stays available for pollers
    and future subscribers.
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
        no subscribers are registered.
        """
        if self._redis:
            await self._redis.publish("agent_events", event.to_json())
            return

        # Fallback to local memory bus (must run under an event loop).
        self._history.append(event)
        for handler in self._handlers.get(event.event_type, []):
            await self._spawn_handler(handler, event)

    def recent_events(self, event_type: str, lookback_sec: float) -> list[AgentEvent]:
        """Fetch recently published events of a certain type within the lookback window."""
        cutoff_time = time.time() - lookback_sec
        return [
            e
            for e in self._history
            if e.event_type == event_type and e.timestamp >= cutoff_time
        ]
