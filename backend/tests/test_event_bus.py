"""Tests for distributed event bus."""

import unittest

from app.services.events.event_bus import LocalEventBus


class TestLocalEventBus(unittest.IsolatedAsyncioTestCase):
    async def test_publish_subscribe(self):
        bus = LocalEventBus()
        seen = []

        async def handler(payload):
            seen.append(payload)

        bus.subscribe("test:channel", handler)
        await bus.publish("test:channel", {"symbol": "BTCUSDT"})
        self.assertEqual(seen, [{"symbol": "BTCUSDT"}])


if __name__ == "__main__":
    unittest.main()
