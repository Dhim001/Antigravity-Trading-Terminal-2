"""Optional IB Gateway integration test (skipped when Gateway is down)."""

from __future__ import annotations

import socket
import unittest

from app.config import IB_HOST, IB_PORT, TERMINAL_MODE


def _gateway_reachable() -> bool:
    try:
        with socket.create_connection((IB_HOST, IB_PORT), timeout=2.0):
            return True
    except OSError:
        return False


@unittest.skipUnless(_gateway_reachable(), f"IB Gateway not reachable at {IB_HOST}:{IB_PORT}")
class TestIbGatewayIntegration(unittest.TestCase):
    def test_health_live_ib_mode(self) -> None:
        """Run backend/scripts/ib_smoke_test.py logic via import when Gateway is up."""
        import asyncio

        from app.config import IB_SMOKE_CLIENT_ID, IB_USE_RTH
        from app.services.ib_bars import bars_to_candles
        from app.services.ib_contracts import stock_contract

        async def _run() -> int:
            from ib_async import IB

            ib = IB()
            await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_SMOKE_CLIENT_ID, timeout=15)
            try:
                contract = stock_contract("AAPL")
                qualified = await ib.qualifyContractsAsync(contract)
                self.assertTrue(qualified)
                bars = await ib.reqHistoricalDataAsync(
                    qualified[0],
                    endDateTime="",
                    durationStr="1 D",
                    barSizeSetting="1 min",
                    whatToShow="TRADES",
                    useRTH=IB_USE_RTH,
                    formatDate=1,
                    keepUpToDate=False,
                )
                candles = bars_to_candles(bars)
                self.assertGreater(len(candles), 10)
                return 0
            finally:
                ib.disconnect()

        if TERMINAL_MODE != "LIVE_IB":
            self.skipTest("Set TERMINAL_MODE=LIVE_IB for full integration naming")
        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
