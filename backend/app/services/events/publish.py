"""Optional cross-process event publishing (registered by server/worker at startup)."""

from __future__ import annotations

from typing import Awaitable, Callable

_publishers: dict[str, Callable[[dict], Awaitable[None]]] = {}


def register_publisher(channel: str, fn: Callable[[dict], Awaitable[None]]):
    _publishers[channel] = fn


async def publish(channel: str, payload: dict | None = None):
    fn = _publishers.get(channel)
    if fn:
        await fn(payload or {})
