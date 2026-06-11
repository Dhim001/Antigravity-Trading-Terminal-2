#!/usr/bin/env python3
"""WebSocket soak + feature smoke test for the trading terminal."""
import asyncio
import json
import time
import statistics
import websockets

WS_URL = "ws://127.0.0.1:8765"
SOAK_SECONDS = 60
SYMBOL = "BTCUSDT"


async def wait_for_type(ws, want_type, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = max(0.1, deadline - time.time())
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        if msg.get("type") == want_type:
            return msg
    raise TimeoutError(f"no {want_type} within {timeout}s")


async def send_action(ws, action, **kwargs):
    await ws.send(json.dumps({"action": action, **kwargs}))


async def run_soak():
    results = {
        "connected": False,
        "features": {},
        "soak": {},
        "errors": [],
    }

    t0 = time.perf_counter()
    try:
        async with websockets.connect(WS_URL, max_size=4 * 1024 * 1024) as ws:
            results["connected"] = True
            results["features"]["connect_ms"] = round((time.perf_counter() - t0) * 1000, 1)

            types_seen = set()
            for _ in range(8):
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                types_seen.add(msg.get("type"))

            results["features"]["handshake"] = sorted(types_seen)
            results["features"]["handshake_ok"] = {"terminal_config", "account_update", "trade_history"}.issubset(types_seen)

            t1 = time.perf_counter()
            await send_action(ws, "subscribe_symbol", symbol=SYMBOL)
            hist = await wait_for_type(ws, "history_update", timeout=45)
            hist_len = len(hist.get("data", {}).get(SYMBOL, []))
            results["features"]["history_bars"] = hist_len
            results["features"]["history_ms"] = round((time.perf_counter() - t1) * 1000, 1)

            await send_action(ws, "place_order", symbol=SYMBOL, side="BUY", type="MARKET", quantity=0.001)
            order_msg = await wait_for_type(ws, "order_result", timeout=15)
            results["features"]["place_order"] = order_msg.get("data", {}).get("status") == "success"

            await wait_for_type(ws, "account_update", timeout=15)
            results["features"]["account_after_order"] = True

            await send_action(ws, "admin_get_stats")
            await wait_for_type(ws, "system_stats", timeout=15)
            results["features"]["admin_stats"] = True

            try:
                await send_action(ws, "bot_get_all")
                bots = await wait_for_type(ws, "bots_update", timeout=5)
                results["features"]["bot_list"] = isinstance(bots.get("data"), list)
            except TimeoutError:
                results["features"]["bot_list"] = "timeout"
                results["errors"].append("bot_get_all: no bots_update in 5s")

            # 60s soak
            market_count = 0
            ob_count = 0
            gaps = []
            last = time.perf_counter()
            soak_start = time.perf_counter()

            while time.perf_counter() - soak_start < SOAK_SECONDS:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=8)
                except asyncio.TimeoutError:
                    gaps.append(8.0)
                    results["errors"].append("recv gap >8s during soak")
                    continue
                now = time.perf_counter()
                gaps.append(now - last)
                last = now
                msg = json.loads(raw)
                mt = msg.get("type")
                if mt == "market_update":
                    market_count += 1
                elif mt == "orderbook_update":
                    ob_count += 1

            duration = time.perf_counter() - soak_start
            results["soak"] = {
                "duration_s": round(duration, 1),
                "market_updates": market_count,
                "market_hz": round(market_count / duration, 2) if duration else 0,
                "orderbook_updates": ob_count,
                "gap_median_ms": round(statistics.median(gaps) * 1000, 1) if gaps else None,
                "gap_max_ms": round(max(gaps) * 1000, 1) if gaps else None,
                "gaps_over_2s": sum(1 for g in gaps if g > 2),
            }

    except Exception as e:
        results["errors"].append(repr(e))

    results["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    print(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    asyncio.run(run_soak())
