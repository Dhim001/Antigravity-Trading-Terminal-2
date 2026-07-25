"""Live WebSocket + HTTP probes against recycled Alpaca backend (:8795/:8796)."""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request

import msgpack
import websockets

WS = "ws://127.0.0.1:8795"
HTTP = "http://127.0.0.1:8796"
MSGPACK_MARKER = b"\x01"


def http_json(path: str, timeout: float = 30.0) -> dict:
    with urllib.request.urlopen(HTTP + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def decode_ws_frame(raw) -> dict:
    if isinstance(raw, bytes):
        if raw.startswith(MSGPACK_MARKER):
            payload = msgpack.unpackb(raw[1:], raw=False, strict_map_key=False)
            return payload if isinstance(payload, dict) else {"type": "unknown", "data": payload}
        raw = raw.decode("utf-8", errors="replace")
    return json.loads(raw)


async def ws_roundtrip(action: str, payload: dict, *, want_types: set[str], timeout: float = 45.0) -> list[dict]:
    out: list[dict] = []
    async with websockets.connect(WS, open_timeout=10, max_size=16 * 1024 * 1024) as ws:
        # Drain initial terminal_config / account snapshot briefly
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
                out.append(decode_ws_frame(raw))
            except asyncio.TimeoutError:
                break
        req = {"action": action, **payload}
        await ws.send(json.dumps(req))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(5.0, end - time.monotonic()))
            except asyncio.TimeoutError:
                if any(m.get("type") in want_types for m in out):
                    break
                continue
            msg = decode_ws_frame(raw)
            out.append(msg)
            t = msg.get("type")
            if t in want_types:
                if "backtest_result" in want_types and t != "backtest_result" and t != "error":
                    continue
                if t in ("backtest_result", "history_update", "error", "system_stats", "order_result"):
                    break
                if t in want_types and t not in ("backtest_progress", "market_update", "account_update"):
                    break
        return out


async def main() -> int:
    fails: list[str] = []
    oks: list[str] = []

    def ok(m: str) -> None:
        oks.append(m)
        print(f"OK  {m}")

    def fail(m: str) -> None:
        fails.append(m)
        print(f"FAIL {m}")

    # HTTP health
    h = http_json("/health/alpaca")
    if h.get("terminal_mode") == "LIVE_ALPACA" and h.get("alpaca", {}).get("connected"):
        ok(
            f"health connected stocks={h['alpaca'].get('stocks_mode')} "
            f"crypto={h['alpaca'].get('crypto_mode')} ht_limits={bool(h.get('alpaca_ht_limits'))}"
        )
    else:
        fail(f"health bad: {h.get('terminal_mode')} connected={h.get('alpaca', {}).get('connected')}")

    if h.get("massive") is not None:
        fail("health/alpaca unexpectedly includes massive blob")
    else:
        ok("health/alpaca has no massive blob")

    # News sources via HTTP
    news = http_json("/api/v1/news/AAPL", timeout=45)
    items = (news.get("news") or {}).get("items") or []
    srcs = sorted({i.get("source") for i in items})
    if items and "alpaca_news" in srcs and "news" not in srcs and "yfinance_news" not in srcs:
        ok(f"HTTP news AAPL n={len(items)} sources={srcs}")
    else:
        fail(f"HTTP news AAPL n={len(items)} sources={srcs}")

    # WS: HT subscribe BTCUSDT 15m (crypto live weekend)
    msgs = await ws_roundtrip(
        "subscribe_symbol",
        {"symbol": "BTCUSDT", "interval": "15m"},
        want_types={"history_update", "error"},
        timeout=60.0,
    )
    hist = next((m for m in msgs if m.get("type") == "history_update"), None)
    if hist:
        bars = (hist.get("data") or hist.get("candles") or {}).get("BTCUSDT") or []
        # payload shape may be {symbol: [...]} under different keys
        if not bars and isinstance(hist.get("data"), dict):
            bars = hist["data"].get("BTCUSDT") or []
        meta = hist.get("meta") or {}
        n = meta.get("count") or len(bars)
        if n >= 50 and meta.get("interval") == "15m":
            ok(f"WS HT BTCUSDT 15m count={n}")
        else:
            # dump keys for debug
            fail(f"WS HT BTCUSDT weak count={n} meta={meta} keys={list(hist.keys())}")
    else:
        err = next((m for m in msgs if m.get("type") == "error"), None)
        fail(f"WS HT no history_update err={err} types={[m.get('type') for m in msgs[-8:]]}")

    # WS: HT equity AAPL 1h (should still work via REST even if session closed)
    msgs = await ws_roundtrip(
        "subscribe_symbol",
        {"symbol": "AAPL", "interval": "1h"},
        want_types={"history_update", "error"},
        timeout=60.0,
    )
    hist = next((m for m in msgs if m.get("type") == "history_update"), None)
    if hist:
        meta = hist.get("meta") or {}
        data = hist.get("data") or {}
        bars = data.get("AAPL") or []
        n = meta.get("count") or len(bars)
        if n >= 20 and meta.get("interval") == "1h":
            ok(f"WS HT AAPL 1h count={n}")
        else:
            fail(f"WS HT AAPL weak count={n} meta={meta}")
    else:
        fail("WS HT AAPL no history_update")

    # WS: short backtest MACD on BTCUSDT 1d window — must resolve via Alpaca
    msgs = await ws_roundtrip(
        "run_backtest",
        {
            "strategy": "MACD_RSI",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "days": 3,
            "config": {},
        },
        want_types={"backtest_result", "error"},
        timeout=120.0,
    )
    result = next((m for m in msgs if m.get("type") == "backtest_result"), None)
    err = next((m for m in msgs if m.get("type") == "error"), None)
    if result:
        status = result.get("status") or (result.get("data") or {}).get("status")
        data = result.get("data") or result
        # Look for Massive leak in notes
        blob = json.dumps(data)[:4000]
        if "MASSIVE" in blob.upper() and "ALPACA" not in blob.upper():
            fail(f"backtest result mentions MASSIVE only: {blob[:300]}")
        elif status == "error":
            fail(f"backtest error status message={data.get('message') or result.get('message')}")
        else:
            ok(f"WS backtest finished status={status} keys={list(data.keys())[:12]}")
    elif err:
        fail(f"WS backtest error={err.get('message') or err}")
    else:
        types = [m.get("type") for m in msgs]
        fail(f"WS backtest no result types={types[-15:]}")

    # WS: archive ingest stats / admin_get_stats for broker_source
    msgs = await ws_roundtrip(
        "admin_get_stats",
        {},
        want_types={"order_result", "error", "system_stats"},
        timeout=30.0,
    )
    stats_msg = next(
        (
            m
            for m in msgs
            if m.get("type") in ("order_result", "system_stats")
            or "archive" in json.dumps(m).lower()
        ),
        None,
    )
    if stats_msg:
        blob = json.dumps(stats_msg)
        if "alpaca" in blob.lower() or "broker_source" in blob.lower() or stats_msg.get("status") == "success":
            # extract broker_source if present
            src = None
            try:
                src = (
                    (stats_msg.get("data") or {})
                    .get("archive", {})
                    .get("ingestion", {})
                    .get("broker_source")
                )
            except Exception:
                src = None
            if src is None:
                # try nested
                import re

                m = re.search(r'"broker_source"\s*:\s*"([^"]+)"', blob)
                src = m.group(1) if m else None
            if src == "alpaca":
                ok(f"admin stats broker_source={src}")
            elif src is None:
                ok(f"admin_get_stats responded type={stats_msg.get('type')} (no broker_source field)")
            else:
                fail(f"admin stats broker_source={src} expected alpaca")
        else:
            fail(f"admin_get_stats unexpected: {blob[:400]}")
    else:
        fail(f"admin_get_stats no response types={[m.get('type') for m in msgs]}")

    # WS: risk basket correlation (Alpaca daily)
    msgs = await ws_roundtrip(
        "risk_basket_correlation",
        {"symbols": ["AAPL", "MSFT", "SPY"]},
        want_types={"order_result", "error", "risk_basket_correlation"},
        timeout=60.0,
    )
    corr = next(
        (m for m in msgs if "correlation" in json.dumps(m).lower() or m.get("type") not in (None, "terminal_config", "account_update", "market_update")),
        None,
    )
    # Prefer non-bootstrap messages after request
    candidates = [m for m in msgs if m.get("type") not in ("terminal_config", "account_update", "market_update", None)]
    hit = candidates[-1] if candidates else None
    if hit:
        blob = json.dumps(hit)
        if "alpaca" in blob.lower() or "matrix" in blob.lower() or hit.get("status") == "success" or "pairs" in blob.lower():
            ok(f"correlation response type={hit.get('type')} snippet={blob[:180]}")
        elif hit.get("type") == "error":
            fail(f"correlation error={hit.get('message')}")
        else:
            ok(f"correlation got type={hit.get('type')} (inspect) {blob[:200]}")
    else:
        fail("correlation no response")

    print()
    print(f"Passed {len(oks)}  Failed {len(fails)}")
    for f in fails:
        print(f"  - {f}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
