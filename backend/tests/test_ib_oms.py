"""IB OMS unit tests (no Gateway required)."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ib_oms import IbOMSService


class TestIbOms(unittest.TestCase):
    def test_uses_fallback_when_disabled(self) -> None:
        feed = MagicMock()
        feed._symbols = {"AAPL": {"price": 100.0, "asset": "AAPL", "quote": "USD", "decimals": 2}}
        with patch("app.services.ib_oms.IB_OMS_ENABLED", False):
            with patch("app.services.ib_oms.IB_READ_ONLY_API", False):
                oms = IbOMSService(feed)
        self.assertTrue(oms.use_fallback)

    def test_place_order_delegates_to_fallback(self) -> None:
        feed = MagicMock()
        feed._symbols = {"AAPL": {"price": 100.0, "asset": "AAPL", "quote": "USD", "decimals": 2}}
        with patch("app.services.ib_oms.IB_OMS_ENABLED", False):
            with patch("app.services.ib_oms.IB_READ_ONLY_API", False):
                oms = IbOMSService(feed)
        oms.fallback_oms.place_order = AsyncMock(return_value={"status": "success"})
        import asyncio

        result = asyncio.run(oms.place_order({"symbol": "AAPL", "type": "MARKET", "side": "BUY", "quantity": 1}))
        self.assertEqual(result["status"], "success")
        oms.fallback_oms.place_order.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
