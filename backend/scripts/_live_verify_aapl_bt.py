"""Verify AAPL backtest buy-hold curve no longer cliffs after archive purge."""
from __future__ import annotations

import asyncio
import json
import time

import msgpack
import websockets

WS = "ws://127.0.0.1:8795"
MARK = b"\x01"


def dec(raw):
    if isinstance(raw, bytes):
        if raw.startswith(MARK):
            return msgpack.unpackb(raw[1:], raw=False, strict_map_key=False)
        raw = raw.decode("utf-8", "replace")
    return json.loads(raw)


async def main() -> int:
    out = []
    async with websockets.connect(WS, open_timeout=10, max_size=32 * 1024 * 1024) as ws:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            try:
                out.append(dec(await asyncio.wait_for(ws.recv(), timeout=0.2)))
            except asyncio.TimeoutError:
                break
        await ws.send(
            json.dumps(
                {
                    "action": "run_backtest",
                    "strategy": "MACD_RSI",
                    "symbol": "AAPL",
                    "timeframe": "1h",
                    "days": 5,
                    "config": {},
                }
            )
        )
        end = time.monotonic() + 120
        hit = None
        while time.monotonic() < end:
            try:
                m = dec(await asyncio.wait_for(ws.recv(), timeout=5))
            except asyncio.TimeoutError:
                continue
            out.append(m)
            if m.get("type") in ("backtest_result", "error"):
                hit = m
                break

    if not hit or hit.get("type") != "backtest_result":
        print("FAIL no backtest_result", hit)
        return 1

    data = hit.get("data") or {}
    res = data.get("results") or {}
    summary = res.get("summary") or {}
    bh = (summary.get("benchmark") or {})
    entry = float(bh.get("entry_price") or 0)
    exit_px = float(bh.get("exit_price") or 0)
    ret = float(bh.get("return_pct") or 0)
    curve = ((summary.get("benchmark_overlays") or {}).get("symbol_bh_curve")) or []
    max_jump = 0.0
    for a, b in zip(curve, curve[1:]):
        ea, eb = float(a.get("equity") or 0), float(b.get("equity") or 0)
        if ea > 0:
            max_jump = max(max_jump, abs(eb / ea - 1.0))

    print(
        f"status={data.get('status')} entry={entry} exit={exit_px} "
        f"bh_ret={ret}% max_equity_jump={max_jump*100:.2f}% curve_n={len(curve)}"
    )
    if max_jump > 0.20:
        print("FAIL equity cliff still present")
        return 1
    if entry > 0 and exit_px > 0 and abs(exit_px / entry - 1.0) > 0.35:
        print("FAIL buy-hold price still absurd")
        return 1
    print("OK AAPL backtest buy-hold looks sane")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
