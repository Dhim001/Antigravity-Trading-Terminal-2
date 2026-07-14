"""AgentEventBus: sync-safe init; Redis listener deferred to start()."""

from __future__ import annotations

import asyncio
import os
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.agent.agent_event_bus import AgentEvent, AgentEventBus


class TestAgentEventBus(unittest.IsolatedAsyncioTestCase):
    def test_init_without_running_loop_when_redis_configured(self):
        """Regression: create_task in __init__ used to raise RuntimeError."""
        fake_redis = MagicMock()
        fake_pubsub = MagicMock()
        fake_redis.pubsub.return_value = fake_pubsub

        with patch.dict(os.environ, {"REDIS_URL": "redis://127.0.0.1:6379/0"}):
            with patch(
                "app.services.agent.agent_event_bus.redis.from_url",
                return_value=fake_redis,
            ):
                bus = AgentEventBus()

        self.assertIsNotNone(bus._pubsub)
        self.assertIsNone(bus._listener_task)

    async def test_start_schedules_listener_once(self):
        fake_redis = MagicMock()
        fake_pubsub = MagicMock()
        fake_pubsub.subscribe = AsyncMock()
        fake_pubsub.unsubscribe = AsyncMock()
        fake_pubsub.aclose = AsyncMock()
        fake_redis.aclose = AsyncMock()

        async def empty_listen():
            if False:
                yield {}

        fake_pubsub.listen = empty_listen
        fake_redis.pubsub.return_value = fake_pubsub

        with patch.dict(os.environ, {"REDIS_URL": "redis://127.0.0.1:6379/0"}):
            with patch(
                "app.services.agent.agent_event_bus.redis.from_url",
                return_value=fake_redis,
            ):
                bus = AgentEventBus()

        await bus.start()
        self.assertIsNotNone(bus._listener_task)
        first = bus._listener_task
        await bus.start()
        self.assertIs(bus._listener_task, first)
        await bus.stop()

    async def test_local_publish_invokes_handlers(self):
        with patch.dict(os.environ, {"REDIS_URL": ""}, clear=False):
            bus = AgentEventBus()

        seen: list[AgentEvent] = []

        async def handler(ev: AgentEvent) -> None:
            seen.append(ev)

        bus.subscribe("BOT_PAUSED", handler)
        await bus.publish(
            AgentEvent(
                source_agent="RISK_SENTINEL",
                event_type="BOT_PAUSED",
                payload={"bot_id": "b1"},
                timestamp=time.time(),
            )
        )
        await asyncio.sleep(0)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].payload["bot_id"], "b1")

    def test_from_json_accepts_bytes(self):
        raw = AgentEvent(
            source_agent="A",
            event_type="X",
            payload={},
            timestamp=1.0,
        ).to_json()
        ev = AgentEvent.from_json(raw.encode("utf-8"))
        self.assertEqual(ev.event_type, "X")


if __name__ == "__main__":
    unittest.main()
