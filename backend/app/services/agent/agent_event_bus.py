"""In-memory pub/sub for inter-agent coordination."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import os
import json
import redis.asyncio as redis
from app.services.agent.reasoning import AgentReasoning

logger = logging.getLogger(__name__)

Handler = Callable[['AgentEvent'], Awaitable[None]]

@dataclass
class AgentEvent:
    source_agent: str
    event_type: str
    payload: dict[str, Any]
    timestamp: float
    reasoning: AgentReasoning | None = None
    
    def to_json(self) -> str:
        return json.dumps({
            "source_agent": self.source_agent,
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "reasoning": self.reasoning.to_dict() if self.reasoning else None
        })
        
    @classmethod
    def from_json(cls, data: str) -> 'AgentEvent':
        parsed = json.loads(data)
        reasoning = AgentReasoning.from_dict(parsed["reasoning"]) if parsed.get("reasoning") else None
        return cls(
            source_agent=parsed["source_agent"],
            event_type=parsed["event_type"],
            payload=parsed["payload"],
            timestamp=parsed["timestamp"],
            reasoning=reasoning
        )

class AgentEventBus:
    """Redis-backed pub/sub event bus for inter-agent communication."""

    def __init__(self, max_history: int = 1000):
        self._handlers: dict[str, list[Handler]] = {}
        self._history: deque[AgentEvent] = deque(maxlen=max_history)
        redis_url = os.environ.get("REDIS_URL")
        self._redis = redis.from_url(redis_url) if redis_url else None
        self._pubsub = self._redis.pubsub() if self._redis else None
        self._listener_task = None
        
        if self._pubsub:
            asyncio.create_task(self._start_listening())

    async def _start_listening(self) -> None:
        await self._pubsub.subscribe("agent_events")
        async for message in self._pubsub.listen():
            if message["type"] == "message":
                try:
                    event = AgentEvent.from_json(message["data"])
                    self._history.append(event)
                    for handler in self._handlers.get(event.event_type, []):
                        asyncio.create_task(self._safe_run(handler, event))
                except Exception as exc:
                    logger.error("Failed to parse or handle Redis AgentEvent: %s", exc)

    async def _safe_run(self, handler: Handler, ev: AgentEvent) -> None:
        try:
            await handler(ev)
        except Exception as exc:
            logger.error("AgentEventBus handler error on %s: %s", ev.event_type, exc)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """Subscribe a callback to a specific agent event type."""
        self._handlers.setdefault(event_type, []).append(handler)

    async def publish(self, event: AgentEvent) -> None:
        """Publish an event to Redis (or locally if Redis is disabled)."""
        if self._redis:
            await self._redis.publish("agent_events", event.to_json())
        else:
            # Fallback to local memory bus
            self._history.append(event)
            for handler in self._handlers.get(event.event_type, []):
                asyncio.create_task(self._safe_run(handler, event))

    def recent_events(self, event_type: str, lookback_sec: float) -> list[AgentEvent]:
        """Fetch recently published events of a certain type within the lookback window."""
        cutoff_time = time.time() - lookback_sec
        return [
            e for e in self._history
            if e.event_type == event_type and e.timestamp >= cutoff_time
        ]
