#!/usr/bin/env python3
"""Smoke-test IB Gateway / TWS connectivity. Skips when Gateway is unreachable.

Requires IB Gateway or TWS running with API enabled.

Usage (from repo root or backend/):
  python backend/scripts/ib_smoke_test.py
  cd backend && python scripts/ib_smoke_test.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import IB_CLIENT_ID, IB_HOST, IB_PORT, IB_USE_RTH
from app.services.ib_bars import bar_to_candle
from app.services.ib_contracts import stock_contract


async def main() -> int:
    try:
        from ib_async import IB
    except ImportError:
        print("SKIP: ib_async not installed (pip install ib_async)")
        return 0

    ib = IB()
    try:
        await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID, timeout=15)
    except Exception as exc:
        print(f"SKIP: could not connect to IB at {IB_HOST}:{IB_PORT} — {exc}")
        return 0

    print(f"OK: connected to IB ({IB_HOST}:{IB_PORT}, clientId={IB_CLIENT_ID})")

    contract = stock_contract("AAPL")
    qualified = await ib.qualifyContractsAsync(contract)
    if not qualified:
        print("FAIL: could not qualify AAPL")
        ib.disconnect()
        return 1

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
    if not bars:
        print("FAIL: no historical bars returned (check market data subscriptions)")
        ib.disconnect()
        return 1

    last = bar_to_candle(bars[-1])
    print(f"OK: AAPL last 1m bar close={last['close']} time={last['time']} ({len(bars)} bars)")
    ib.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
