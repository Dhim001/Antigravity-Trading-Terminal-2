"""Quick soak: HTTP health + WS market_update cadence (Massive profile default ports)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

try:
    import httpx
except ImportError:
    httpx = None

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


async def ws_soak(host: str, port: int, symbol: str, seconds: float) -> dict:
    uri = f"ws://{host}:{port}"
    updates = 0
    prices: list[float] = []
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"action": "subscribe_symbol", "symbol": symbol, "limit": 100}))
        t0 = time.time()
        while time.time() - t0 < seconds:
            raw = await asyncio.wait_for(ws.recv(), timeout=3)
            msg = decode(raw)
            if msg.get("type") == "market_update":
                info = (msg.get("data") or {}).get(symbol) or {}
                p = info.get("price")
                if p is not None:
                    updates += 1
                    prices.append(float(p))
    return {
        "updates": updates,
        "unique_prices": len(set(prices)),
        "first": prices[0] if prices else None,
        "last": prices[-1] if prices else None,
    }


def fetch_health(http_url: str) -> dict:
    if httpx is None:
        return {"error": "httpx not installed"}
    r = httpx.get(http_url, timeout=8)
    r.raise_for_status()
    return r.json()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Soak WS + health for trading terminal")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--ws-port", type=int, default=8785)
    parser.add_argument("--http-port", type=int, default=8786)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args()

    http_url = f"http://{args.host}:{args.http_port}/health"
    print(f"=== soak {args.seconds}s symbol={args.symbol} ===")
    print(f"HTTP {http_url}")

    try:
        health = fetch_health(http_url)
        print(
            f"health: mode={health.get('terminal_mode')} "
            f"ws_clients={health.get('ws_clients')} "
            f"execution={health.get('execution_mode')}"
        )
        massive = health.get("massive") or {}
        if massive:
            print(
                f"  massive crypto_lag={massive.get('crypto_lag_sec')} "
                f"poll_fallback={massive.get('poll_fallback')}"
            )
    except Exception as exc:
        print(f"health FAILED: {exc}")
        return 2

    try:
        ws_stats = await ws_soak(args.host, args.ws_port, args.symbol, args.seconds)
    except Exception as exc:
        print(f"ws FAILED: {exc}")
        return 3

    rate = ws_stats["updates"] / max(args.seconds, 0.001)
    print(
        f"ws: updates={ws_stats['updates']} "
        f"unique_prices={ws_stats['unique_prices']} "
        f"rate={rate:.1f}/s"
    )
    if ws_stats["first"] is not None:
        print(f"  first={ws_stats['first']} last={ws_stats['last']}")

    ok = ws_stats["updates"] > 0 and ws_stats["unique_prices"] > 1
    print("PASS" if ok else "FAIL (no live price movement)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
