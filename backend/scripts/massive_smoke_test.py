#!/usr/bin/env python3
"""Smoke-test Massive REST + stocks/crypto WebSocket connectivity.

Exit codes:
  0  success (REST ok; WS ok or benign timeout when US market closed)
  1  REST failure
  2  stocks WebSocket auth_failed (plan/key)
  3  crypto WebSocket auth_failed
  4  stocks WebSocket timeout while US market open (no AM/T)
  5  stocks WebSocket timeout while US market closed (informational)
  6  crypto WebSocket timeout (24/7 feed — no XA/XT within probe window)
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import (
    MASSIVE_API_KEY,
    MASSIVE_CRYPTO_WS_URL,
    MASSIVE_REST_URL,
    MASSIVE_WS_URL,
)
from app.services.massive_bars import rest_agg_to_candle
from app.services.massive_symbols import terminal_to_massive_rest_ticker

# Exit code constants (documented in module docstring)
EXIT_OK = 0
EXIT_REST_FAIL = 1
EXIT_STOCKS_AUTH = 2
EXIT_CRYPTO_AUTH = 3
EXIT_STOCKS_TIMEOUT_OPEN = 4
EXIT_STOCKS_TIMEOUT_CLOSED = 5
EXIT_CRYPTO_TIMEOUT = 6


def _us_equity_session_open() -> bool:
    """Regular US equity session Mon–Fri 9:30–16:00 ET."""
    try:
        now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return True
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


def _fetch_rest_bar(symbol: str = "AAPL") -> dict | None:
    import httpx

    to_d = date.today()
    from_d = to_d - timedelta(days=1)
    ticker = terminal_to_massive_rest_ticker(
        symbol,
        {"asset": symbol.replace("USDT", "")} if "USDT" in symbol else None,
    )
    url = (
        f"{MASSIVE_REST_URL.rstrip('/')}/v2/aggs/ticker/{ticker}/range/"
        f"1/minute/{from_d.isoformat()}/{to_d.isoformat()}"
    )
    params = {"adjusted": "true", "sort": "desc", "limit": 5, "apiKey": MASSIVE_API_KEY}
    resp = httpx.get(url, params=params, timeout=30.0)
    resp.raise_for_status()
    results = resp.json().get("results") or []
    return results[-1] if results else None


async def _ws_probe(
    ws_url: str,
    subscribe: str,
    expect_events: tuple[str, ...],
    label: str,
) -> str:
    """Returns 'ok' | 'auth_failed' | 'timeout' | 'unreachable'."""
    import websockets

    try:
        async with websockets.connect(ws_url, ping_interval=20) as ws:
            authed = False
            for _ in range(40):
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
                except asyncio.TimeoutError:
                    continue
                msgs = json.loads(raw)
                if not isinstance(msgs, list):
                    msgs = [msgs]
                for msg in msgs:
                    ev = msg.get("ev")
                    status = msg.get("status")
                    if ev == "status" and status == "connected" and not authed:
                        await ws.send(json.dumps({"action": "auth", "params": MASSIVE_API_KEY}))
                    elif ev == "status" and status == "auth_success":
                        authed = True
                        await ws.send(json.dumps({"action": "subscribe", "params": subscribe}))
                    elif ev in expect_events:
                        sym = msg.get("sym") or msg.get("pair")
                        print(f"OK: {label} WS event {ev} {sym}")
                        return "ok"
                    elif status == "auth_failed":
                        print(f"FAIL: {label} auth_failed - {msg.get('message')}")
                        return "auth_failed"
                    elif status == "error":
                        print(f"FAIL: {label} error - {msg.get('message') or status}")
                        return "auth_failed"
        return "timeout"
    except Exception as exc:
        print(f"SKIP: {label} WebSocket unreachable ({ws_url}) - {exc}")
        return "unreachable"


def _resolve_exit_code(
    stocks_result: str,
    crypto_result: str,
    stocks_quotes: str,
    crypto_quotes: str,
) -> int:
    """Pick the most severe exit code (lower number = higher priority)."""
    codes: list[int] = [EXIT_OK]

    if stocks_result == "auth_failed":
        codes.append(EXIT_STOCKS_AUTH)
    elif stocks_result == "timeout":
        codes.append(
            EXIT_STOCKS_TIMEOUT_CLOSED if not _us_equity_session_open() else EXIT_STOCKS_TIMEOUT_OPEN
        )

    if crypto_result == "auth_failed":
        codes.append(EXIT_CRYPTO_AUTH)
    elif crypto_result == "timeout":
        codes.append(EXIT_CRYPTO_TIMEOUT)

    worst = min(codes)
    if worst != EXIT_OK:
        return worst

    # Quote probes are informational only (plan may exclude Q/XQ while AM/T works)
    if stocks_quotes == "auth_failed":
        print("INFO: stocks quote (Q) auth_failed — NBBO stream not on plan; agg/trade may still work")
    if crypto_quotes == "auth_failed":
        print("INFO: crypto quote (XQ) auth_failed — quote stream not on plan; agg/trade may still work")
    if stocks_quotes == "timeout" and _us_equity_session_open():
        print("INFO: stocks quote (Q) timeout — no NBBO events in probe window")
    if crypto_quotes == "timeout":
        print("INFO: crypto quote (XQ) timeout — no quote events in probe window")

    return EXIT_OK


def _print_timeout_hint(stocks_result: str, crypto_result: str) -> None:
    if stocks_result == "timeout":
        if _us_equity_session_open():
            print("FAIL: stocks WS - no AM/T events within probe window (market is open)")
        else:
            print("INFO: stocks WS - no AM/T events (US market likely closed; exit 5)")
    elif stocks_result == "unreachable":
        print("SKIP: stocks WS unreachable — not counted as failure")
    if crypto_result == "timeout":
        print("FAIL: crypto WS - no XA/XT events within probe window (crypto is 24/7)")
    elif crypto_result == "unreachable":
        print("SKIP: crypto WS unreachable — not counted as failure")


async def main() -> int:
    if not MASSIVE_API_KEY:
        print("SKIP: MASSIVE_API_KEY not set")
        return EXIT_OK

    try:
        bar = _fetch_rest_bar("AAPL")
    except Exception as exc:
        print(f"FAIL: REST unreachable ({MASSIVE_REST_URL}) - {exc}")
        return EXIT_REST_FAIL

    if not bar:
        print("FAIL: no REST bars returned for AAPL")
        return EXIT_REST_FAIL

    candle = rest_agg_to_candle(bar)
    print(f"OK: REST AAPL last 1m close={candle['close']} time={candle['time']}")

    try:
        crypto_bar = _fetch_rest_bar("BTCUSDT")
        if crypto_bar:
            cb = rest_agg_to_candle(crypto_bar)
            print(f"OK: REST BTC-USD last 1m close={cb['close']} time={cb['time']}")
    except Exception as exc:
        print(f"WARN: crypto REST seed failed - {exc}")

    stocks_result = await _ws_probe(
        MASSIVE_WS_URL,
        "AM.AAPL",
        ("AM", "A"),
        "stocks",
    )
    crypto_result = await _ws_probe(
        MASSIVE_CRYPTO_WS_URL,
        "XA.BTC-USD,XT.BTC-USD",
        ("XA", "XT"),
        "crypto",
    )
    stocks_quotes = await _ws_probe(
        MASSIVE_WS_URL,
        "Q.AAPL",
        ("Q",),
        "stocks-quotes",
    )
    crypto_quotes = await _ws_probe(
        MASSIVE_CRYPTO_WS_URL,
        "XQ.BTC-USD",
        ("XQ",),
        "crypto-quotes",
    )

    _print_timeout_hint(stocks_result, crypto_result)
    code = _resolve_exit_code(stocks_result, crypto_result, stocks_quotes, crypto_quotes)
    if code == EXIT_OK:
        print("OK: Massive smoke passed")
    else:
        print(f"EXIT: {code} (see codes in script docstring)")
    return code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
