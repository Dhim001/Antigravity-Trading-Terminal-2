#!/usr/bin/env python3
"""
Medium performance + stress test for the trading terminal backend.

Exercises concurrent WebSocket clients, action round-trips, HTTP bursts,
and sustained market-data soak. Prints JSON results and PASS/FAIL summary.

Usage:
  python scripts/perf_stress_test.py
  python scripts/perf_stress_test.py --clients 15 --duration 30 --http-burst 40
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import websockets

WS_URL = "ws://127.0.0.1:8765"
HTTP_URL = "http://127.0.0.1:8766"

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "AAPL", "NVDA", "TSLA",
    "SOLUSDT", "MSFT", "SPY", "GOOGL", "AMZN",
    "META", "AMD", "QQQ", "BNBUSDT", "DOGEUSDT",
]

# Medium-tier budgets (local simulated mode; tune for CI hardware)
THRESHOLDS = {
    "ws_connect_p95_ms": 2500,
    "history_p95_ms": 12000,
    "action_rtt_p95_ms": 2000,
    "client_connect_ratio": 1.0,
    "market_hz_min": 0.8,
    "soak_gap_max_ms": 6000,
    "http_health_p95_ms": 300,
    "http_account_p95_ms": 1500,
    "http_burst_ok_ratio": 0.98,
    "http_burst_p95_ms": 3000,
}


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(len(ordered) * pct / 100)))
    return ordered[idx]


async def wait_for_type(ws, want_type: str, timeout: float = 15) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = max(0.1, deadline - time.time())
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        if msg.get("type") == want_type:
            return msg
    raise TimeoutError(f"no {want_type} within {timeout}s")


async def drain_handshake(ws, max_msgs: int = 12) -> set[str]:
    types: set[str] = set()
    for _ in range(max_msgs):
        raw = await asyncio.wait_for(ws.recv(), timeout=15)
        types.add(json.loads(raw).get("type"))
    return types


async def send_action(ws, action: str, **kwargs) -> None:
    await ws.send(json.dumps({"action": action, **kwargs}))


@dataclass
class ClientResult:
    client_id: int
    connected: bool = False
    connect_ms: float | None = None
    history_ms: float | None = None
    action_rtts_ms: list[float] = field(default_factory=list)
    market_count: int = 0
    gap_max_ms: float = 0
    errors: list[str] = field(default_factory=list)


async def client_session(
    client_id: int,
    symbol: str,
    duration_s: float,
    action_rounds: int,
    ws_url: str,
) -> ClientResult:
    result = ClientResult(client_id=client_id)
    t0 = time.perf_counter()
    try:
        async with websockets.connect(ws_url, max_size=4 * 1024 * 1024, open_timeout=10) as ws:
            result.connected = True
            result.connect_ms = (time.perf_counter() - t0) * 1000

            await drain_handshake(ws)

            t_hist = time.perf_counter()
            await send_action(ws, "subscribe_symbol", symbol=symbol)
            await wait_for_type(ws, "history_update", timeout=45)
            result.history_ms = (time.perf_counter() - t_hist) * 1000

            for _ in range(action_rounds):
                t_action = time.perf_counter()
                await send_action(ws, "get_account")
                await wait_for_type(ws, "account_update", timeout=10)
                result.action_rtts_ms.append((time.perf_counter() - t_action) * 1000)

            soak_start = time.perf_counter()
            last = soak_start
            while time.perf_counter() - soak_start < duration_s:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=8)
                except asyncio.TimeoutError:
                    gap_ms = 8000
                    result.gap_max_ms = max(result.gap_max_ms, gap_ms)
                    result.errors.append("recv gap >8s")
                    continue
                now = time.perf_counter()
                gap_ms = (now - last) * 1000
                result.gap_max_ms = max(result.gap_max_ms, gap_ms)
                last = now
                if json.loads(raw).get("type") == "market_update":
                    result.market_count += 1

    except Exception as exc:
        result.errors.append(repr(exc))

    return result


async def run_ws_stress(clients: int, duration_s: float, action_rounds: int, ws_url: str) -> dict[str, Any]:
    tasks = [
        client_session(i, SYMBOLS[i % len(SYMBOLS)], duration_s, action_rounds, ws_url)
        for i in range(clients)
    ]
    results = await asyncio.gather(*tasks)

    connect_ms = [r.connect_ms for r in results if r.connect_ms is not None]
    history_ms = [r.history_ms for r in results if r.history_ms is not None]
    action_rtts = [v for r in results for v in r.action_rtts_ms]
    connected = sum(1 for r in results if r.connected)
    total_market = sum(r.market_count for r in results)
    soak_duration = duration_s * max(connected, 1)
    errors = [e for r in results for e in r.errors]

    return {
        "clients": clients,
        "connected": connected,
        "connect_p95_ms": round(percentile(connect_ms, 95) or 0, 1),
        "history_p95_ms": round(percentile(history_ms, 95) or 0, 1),
        "action_rtt_p95_ms": round(percentile(action_rtts, 95) or 0, 1),
        "action_rtt_median_ms": round(statistics.median(action_rtts), 1) if action_rtts else None,
        "market_total": total_market,
        "market_hz": round(total_market / soak_duration, 2) if soak_duration else 0,
        "gap_max_ms": round(max((r.gap_max_ms for r in results), default=0), 1),
        "errors": errors[:20],
        "error_count": len(errors),
    }


async def http_latency_probe(path: str, samples: int, http_url: str) -> dict[str, Any]:
    latencies: list[float] = []
    errors = 0
    async with httpx.AsyncClient(base_url=http_url, timeout=15) as client:
        for _ in range(samples):
            t0 = time.perf_counter()
            try:
                resp = await client.get(path)
                if resp.status_code >= 400:
                    errors += 1
                latencies.append((time.perf_counter() - t0) * 1000)
            except Exception:
                errors += 1
    return {
        "path": path,
        "samples": samples,
        "ok": samples - errors,
        "p95_ms": round(percentile(latencies, 95) or 0, 1),
        "median_ms": round(statistics.median(latencies), 1) if latencies else None,
    }


async def http_burst(path: str, count: int, http_url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=http_url, timeout=20) as client:
        async def one() -> tuple[bool, float]:
            t0 = time.perf_counter()
            try:
                resp = await client.get(path)
                return resp.status_code < 400, (time.perf_counter() - t0) * 1000
            except Exception:
                return False, (time.perf_counter() - t0) * 1000

        results = await asyncio.gather(*[one() for _ in range(count)])
    oks = sum(1 for ok, _ in results if ok)
    latencies = [ms for _, ms in results]
    return {
        "path": path,
        "count": count,
        "ok_ratio": round(oks / count, 3) if count else 0,
        "p95_ms": round(percentile(latencies, 95) or 0, 1),
    }


async def single_client_burst_subscribe(count: int, ws_url: str) -> dict[str, Any]:
    """Rapid symbol switches on one connection."""
    latencies: list[float] = []
    errors = 0
    async with websockets.connect(ws_url, max_size=4 * 1024 * 1024) as ws:
        await drain_handshake(ws)
        for i in range(count):
            sym = SYMBOLS[i % len(SYMBOLS)]
            t0 = time.perf_counter()
            try:
                await send_action(ws, "subscribe_symbol", symbol=sym)
                await wait_for_type(ws, "history_update", timeout=20)
                latencies.append((time.perf_counter() - t0) * 1000)
            except Exception:
                errors += 1
    return {
        "subscribe_burst": count,
        "ok": count - errors,
        "p95_ms": round(percentile(latencies, 95) or 0, 1),
        "median_ms": round(statistics.median(latencies), 1) if latencies else None,
        "errors": errors,
    }


def evaluate(results: dict[str, Any]) -> list[tuple[str, bool, str]]:
    ws = results["websocket"]
    http = results["http"]
    checks: list[tuple[str, bool, str]] = []

    ratio = ws["connected"] / ws["clients"] if ws["clients"] else 0
    checks.append((
        "WS clients connected",
        ratio >= THRESHOLDS["client_connect_ratio"],
        f"{ws['connected']}/{ws['clients']}",
    ))
    checks.append((
        "WS connect p95",
        ws["connect_p95_ms"] <= THRESHOLDS["ws_connect_p95_ms"],
        f"{ws['connect_p95_ms']}ms <= {THRESHOLDS['ws_connect_p95_ms']}ms",
    ))
    checks.append((
        "History load p95",
        ws["history_p95_ms"] <= THRESHOLDS["history_p95_ms"],
        f"{ws['history_p95_ms']}ms <= {THRESHOLDS['history_p95_ms']}ms",
    ))
    checks.append((
        "Action RTT p95",
        ws["action_rtt_p95_ms"] <= THRESHOLDS["action_rtt_p95_ms"],
        f"{ws['action_rtt_p95_ms']}ms <= {THRESHOLDS['action_rtt_p95_ms']}ms",
    ))
    checks.append((
        "Market throughput",
        ws["market_hz"] >= THRESHOLDS["market_hz_min"],
        f"{ws['market_hz']} Hz >= {THRESHOLDS['market_hz_min']} Hz",
    ))
    checks.append((
        "Soak gap max",
        ws["gap_max_ms"] <= THRESHOLDS["soak_gap_max_ms"],
        f"{ws['gap_max_ms']}ms <= {THRESHOLDS['soak_gap_max_ms']}ms",
    ))
    checks.append((
        "HTTP /health p95",
        http["health"]["p95_ms"] <= THRESHOLDS["http_health_p95_ms"],
        f"{http['health']['p95_ms']}ms",
    ))
    checks.append((
        "HTTP /account p95",
        http["account"]["p95_ms"] <= THRESHOLDS["http_account_p95_ms"],
        f"{http['account']['p95_ms']}ms",
    ))
    burst = http["burst"]
    checks.append((
        "HTTP burst ok ratio",
        burst["ok_ratio"] >= THRESHOLDS["http_burst_ok_ratio"],
        f"{burst['ok_ratio']}",
    ))
    checks.append((
        "HTTP burst p95",
        burst["p95_ms"] <= THRESHOLDS["http_burst_p95_ms"],
        f"{burst['p95_ms']}ms",
    ))
    return checks


async def main() -> int:
    parser = argparse.ArgumentParser(description="Trading terminal perf/stress test")
    parser.add_argument("--clients", type=int, default=10, help="Concurrent WS clients")
    parser.add_argument("--duration", type=float, default=25, help="Soak seconds per client")
    parser.add_argument("--action-rounds", type=int, default=3, help="get_account RTT samples per client")
    parser.add_argument("--http-samples", type=int, default=20, help="HTTP latency samples per route")
    parser.add_argument("--http-burst", type=int, default=30, help="Concurrent HTTP requests")
    parser.add_argument("--subscribe-burst", type=int, default=8, help="Rapid symbol switches")
    parser.add_argument("--ws-url", default=WS_URL)
    parser.add_argument("--http-url", default=HTTP_URL)
    args = parser.parse_args()

    ws_url = args.ws_url
    http_url = args.http_url

    t0 = time.perf_counter()
    ws_result = await run_ws_stress(args.clients, args.duration, args.action_rounds, ws_url)
    http_health = await http_latency_probe("/health", args.http_samples, http_url)
    http_account = await http_latency_probe("/api/v1/account", args.http_samples, http_url)
    http_burst_result = await http_burst("/health", args.http_burst, http_url)
    subscribe_burst = await single_client_burst_subscribe(args.subscribe_burst, ws_url)

    payload = {
        "mode": "medium_perf_stress",
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "config": vars(args),
        "websocket": ws_result,
        "http": {
            "health": http_health,
            "account": http_account,
            "burst": http_burst_result,
        },
        "subscribe_burst": subscribe_burst,
        "thresholds": THRESHOLDS,
    }
    checks = evaluate(payload)
    payload["checks"] = [{"name": n, "pass": ok, "detail": d} for n, ok, d in checks]
    payload["passed"] = all(ok for _, ok, _ in checks)

    print(json.dumps(payload, indent=2))
    print("\n--- Summary ---")
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}: {detail}")
    print(f"\nOverall: {'PASS' if payload['passed'] else 'FAIL'} ({payload['elapsed_ms']}ms total)")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
