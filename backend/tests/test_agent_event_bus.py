"""AgentEventBus: sync-safe init; Redis listener deferred to start(); durable history."""

from __future__ import annotations

import asyncio
import os
import time
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.agent.agent_event_bus import AgentEvent, AgentEventBus
from app.services.agent.reasoning import AgentReasoning, Observation


def _make_reasoning() -> AgentReasoning:
    return AgentReasoning(
        observations=[
            Observation(
                source="ADX",
                signal="trending",
                confidence=0.9,
                detail="ADX is 31.20",
                data={"adx": 31.2},
            )
        ],
        synthesis="Market regime shifted to trend.",
        decision="ROTATE",
        confidence=0.87,
        alternatives_considered=["HOLD"],
        uncertainty_sources=["small sample"],
        recommendation_strength="strong",
    )


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

    def test_reasoning_round_trip_through_event_json(self):
        reasoning = _make_reasoning()
        ev = AgentEvent(
            source_agent="REGIME_ROTATION",
            event_type="REGIME_CHANGED",
            payload={"bot_id": "b1"},
            timestamp=123.5,
            reasoning=reasoning,
        )
        restored = AgentEvent.from_json(ev.to_json())

        self.assertIsNotNone(restored.reasoning)
        self.assertEqual(restored.reasoning.to_dict(), reasoning.to_dict())
        self.assertEqual(restored.reasoning.observations[0].data, {"adx": 31.2})

    def test_from_dict_tolerates_missing_fields(self):
        ev = AgentEvent.from_dict({"event_type": "X"})
        self.assertEqual(ev.event_type, "X")
        self.assertEqual(ev.payload, {})
        self.assertIsNone(ev.reasoning)

        reasoning = AgentReasoning.from_dict({"decision": "REDUCE_SIZE"})
        self.assertEqual(reasoning.decision, "REDUCE_SIZE")
        self.assertEqual(reasoning.observations, [])
        self.assertEqual(reasoning.recommendation_strength, "moderate")

    async def test_recent_events_survive_restart_via_db(self):
        """Events persisted by one bus instance are visible to a fresh instance."""
        with patch.dict(os.environ, {"REDIS_URL": ""}, clear=False):
            bus_a = AgentEventBus()
            event_type = f"TEST_DURABLE_{uuid.uuid4().hex[:8]}"
            await bus_a.publish(
                AgentEvent(
                    source_agent="TEST",
                    event_type=event_type,
                    payload={"bot_id": "b-restart"},
                    timestamp=time.time(),
                    reasoning=_make_reasoning(),
                )
            )

            # Simulate a restart: brand-new bus, empty in-memory history.
            bus_b = AgentEventBus()
            self.assertEqual(len(bus_b._history), 0)
            events = bus_b.recent_events(event_type, lookback_sec=3600)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["bot_id"], "b-restart")
        self.assertIsNotNone(events[0].reasoning)
        self.assertEqual(events[0].reasoning.decision, "ROTATE")

    async def test_recent_events_falls_back_to_memory_when_db_fails(self):
        with patch.dict(os.environ, {"REDIS_URL": ""}, clear=False):
            bus = AgentEventBus()
            event_type = f"TEST_FALLBACK_{uuid.uuid4().hex[:8]}"
            ev = AgentEvent(
                source_agent="TEST",
                event_type=event_type,
                payload={},
                timestamp=time.time(),
            )
            bus._history.append(ev)

            with patch.object(bus, "_recent_events_db", side_effect=RuntimeError("db down")):
                events = bus.recent_events(event_type, lookback_sec=60)

        self.assertEqual(len(events), 1)
        self.assertIs(events[0], ev)

    async def test_subscriber_invoked_on_publish_with_reasoning(self):
        with patch.dict(os.environ, {"REDIS_URL": ""}, clear=False):
            bus = AgentEventBus()

        seen: list[AgentEvent] = []

        async def handler(ev: AgentEvent) -> None:
            seen.append(ev)

        event_type = f"TEST_SUB_{uuid.uuid4().hex[:8]}"
        bus.subscribe(event_type, handler)
        await bus.publish(
            AgentEvent(
                source_agent="TEST",
                event_type=event_type,
                payload={"bot_id": "b2"},
                timestamp=time.time(),
                reasoning=_make_reasoning(),
            )
        )
        await asyncio.sleep(0)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].reasoning.synthesis, "Market regime shifted to trend.")

    async def test_streak_escalate_subscriber_without_alpha_decay(self):
        """STREAK_ESCALATE handler must not raise even if alpha_decay is absent."""
        import sys

        from app.services.bots import agent_event_subscribers as subs

        bus = AgentEventBus.__new__(AgentEventBus)  # handlers only; no init needed
        bus._handlers = {}
        subs.register_agent_event_subscribers(bus)

        narrate_calls: list[tuple[str, dict]] = []

        async def fake_narrate(event_type: str, payload: dict) -> None:
            narrate_calls.append((event_type, payload))

        with patch.object(subs, "_narrate", fake_narrate):
            with patch.dict(sys.modules, {"app.services.bots.alpha_decay": None}):
                for handler in bus._handlers["STREAK_ESCALATE"]:
                    await handler(
                        AgentEvent(
                            source_agent="PRETRADE_INTEL",
                            event_type="STREAK_ESCALATE",
                            payload={
                                "bot_id": "b-streak",
                                "symbol": "AAPL",
                                "streak": 4,
                                "verdict": "REDUCE_SIZE",
                            },
                            timestamp=time.time(),
                        )
                    )

        self.assertTrue(subs.is_streak_escalation_ignored("b-streak"))
        self.assertEqual(narrate_calls[0][1]["action"], "streak_escalate")
        subs._streak_ignore_until.pop("b-streak", None)

    async def test_bot_paused_subscriber_feeds_paused_cache(self):
        from app.services.bots import agent_event_subscribers as subs

        async def fake_narrate(event_type: str, payload: dict) -> None:
            return None

        with patch.object(subs, "_narrate", fake_narrate):
            await subs._on_bot_paused(
                AgentEvent(
                    source_agent="RISK_SENTINEL",
                    event_type="BOT_PAUSED",
                    payload={"bot_id": "b-paused", "reason": "loss_streak"},
                    timestamp=time.time(),
                )
            )

        self.assertIn("b-paused", subs.recently_paused_bot_ids(3600))
        subs._paused_bots.pop("b-paused", None)


if __name__ == "__main__":
    unittest.main()
