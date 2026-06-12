"""In-process and Redis event buses for decoupled bot workers."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

Handler = Callable[[dict], Awaitable[None]]


class LocalEventBus:
    """Single-process bus — publish invokes subscribers directly."""

    def __init__(self):
        self._handlers: dict[str, list[Handler]] = {}

    def subscribe(self, channel: str, handler: Handler):
        self._handlers.setdefault(channel, []).append(handler)

    async def publish(self, channel: str, payload: dict):
        for handler in self._handlers.get(channel, []):
            try:
                await handler(payload)
            except Exception as exc:
                logger.error("LocalEventBus handler error on %s: %s", channel, exc)

    async def start(self):
        return

    async def stop(self):
        return


class RedisEventBus:
    """Redis pub/sub for multi-process server ↔ worker communication."""

    def __init__(self, url: str):
        self._url = url
        self._handlers: dict[str, list[Handler]] = {}
        self._redis = None
        self._pub = None
        self._task: asyncio.Task | None = None
        self._running = False

    def subscribe(self, channel: str, handler: Handler):
        self._handlers.setdefault(channel, []).append(handler)

    async def publish(self, channel: str, payload: dict):
        if not self._pub:
            raise RuntimeError("RedisEventBus not started")
        await self._pub.publish(channel, json.dumps(payload))

    async def _listen(self):
        pubsub = self._redis.pubsub()
        channels = list(self._handlers.keys())
        if not channels:
            return
        await pubsub.subscribe(*channels)
        logger.info("RedisEventBus subscribed to %s", channels)
        async for message in pubsub.listen():
            if not self._running:
                break
            if message["type"] != "message":
                continue
            channel = message["channel"]
            if isinstance(channel, bytes):
                channel = channel.decode()
            try:
                payload = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            for handler in self._handlers.get(channel, []):
                try:
                    await handler(payload)
                except Exception as exc:
                    logger.error("RedisEventBus handler error on %s: %s", channel, exc)

    async def start(self):
        import redis.asyncio as redis

        self._redis = redis.from_url(self._url, decode_responses=False)
        self._pub = redis.from_url(self._url, decode_responses=False)
        self._running = True
        if self._handlers:
            self._task = asyncio.create_task(self._listen())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._redis:
            await self._redis.aclose()
        if self._pub:
            await self._pub.aclose()


def create_event_bus(redis_url: str | None) -> LocalEventBus | RedisEventBus:
    if redis_url:
        return RedisEventBus(redis_url)
    return LocalEventBus()
