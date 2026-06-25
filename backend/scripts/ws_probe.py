"""Quick WS probe for market_update cadence."""
from __future__ import annotations

import asyncio
import json
import sys
import time

try:
    import msgpack
except ImportError:
    msgpack = None

import websockets

MSGPACK_MARKER = 0x01


def decode(raw):
    if isinstance(raw, str):
        return json.loads(raw)
    data = raw if isinstance(raw, (bytes, bytearray)) else bytes(raw)
    if data and data[0] == MSGPACK_MARKER and msgpack:
        return msgpack.unpackb(data[1:], raw=False)
    return json.loads(data.decode("utf-8"))


async def main(symbol: str = "BTCUSDT", seconds: float = 8.0) -> int:
    uri = "ws://127.0.0.1:8785"
    updates = 0
    prices: list[float] = []
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"action": "subscribe_symbol", "symbol": symbol, "limit": 100}))
        t0 = time.time()
        while time.time() - t0 < seconds:
            raw = await asyncio.wait_for(ws.recv(), timeout=2)
            msg = decode(raw)
            if msg.get("type") == "market_update":
                info = (msg.get("data") or {}).get(symbol) or {}
                p = info.get("price")
                if p is not None:
                    updates += 1
                    prices.append(float(p))
    print(f"symbol={symbol} market_updates={updates} unique_prices={len(set(prices))}")
    if prices:
        print(f"  first={prices[0]} last={prices[-1]}")
    return 0 if updates > 0 else 1


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    raise SystemExit(asyncio.run(main(sym)))
