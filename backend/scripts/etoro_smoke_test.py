#!/usr/bin/env python3
"""Smoke-test eToro API connectivity. Skips when credentials are absent.

Usage:
  ETORO_API_KEY=... ETORO_USER_KEY=... python scripts/etoro_smoke_test.py
  ETORO_ACCESS_TOKEN=... python scripts/etoro_smoke_test.py
"""

from __future__ import annotations

import json
import sys
import uuid

import requests

from app.config import (
    ETORO_ACCESS_TOKEN,
    ETORO_API_BASE,
    ETORO_API_KEY,
    ETORO_USER_KEY,
)


def _has_credentials() -> bool:
    if ETORO_ACCESS_TOKEN:
        return True
    return bool(ETORO_API_KEY and ETORO_USER_KEY)


def _auth_headers() -> dict[str, str]:
    headers = {"x-request-id": str(uuid.uuid4())}
    if ETORO_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {ETORO_ACCESS_TOKEN}"
        return headers
    headers["x-api-key"] = ETORO_API_KEY
    headers["x-user-key"] = ETORO_USER_KEY
    return headers


def probe_environment(session: requests.Session) -> str:
    """Return 'real' or 'demo' per eToro key-probe convention."""
    resp = session.get(
        f"{ETORO_API_BASE}/trading/info/real/pnl",
        headers=_auth_headers(),
        timeout=30,
    )
    if resp.status_code == 200:
        return "real"
    if resp.status_code == 403:
        try:
            body = resp.json()
        except json.JSONDecodeError:
            body = {}
        if "InsufficientPermissions" in str(body.get("error", "")):
            return "demo"
    resp.raise_for_status()
    return "demo"


def search_instrument(session: requests.Session, symbol: str) -> int | None:
    resp = session.get(
        f"{ETORO_API_BASE}/market-data/search",
        params={"searchText": symbol, "limit": 5},
        headers=_auth_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    items = resp.json().get("items") or resp.json().get("Items") or []
    for item in items:
        if str(item.get("symbol", "")).upper() == symbol.upper():
            return item.get("instrumentId") or item.get("InstrumentID")
    return None


def fetch_rate(session: requests.Session, instrument_id: int) -> dict:
    url = f"{ETORO_API_BASE}/market-data/instruments/rates?instrumentIds={instrument_id}"
    resp = session.get(url, headers=_auth_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    rates = data.get("rates") or data.get("Rates") or []
    if not rates:
        raise RuntimeError(f"No rates returned for instrument {instrument_id}")
    return rates[0]


def main() -> int:
    if not _has_credentials():
        print("SKIP: eToro credentials not configured (set ETORO_API_KEY+ETORO_USER_KEY or ETORO_ACCESS_TOKEN)")
        return 0

    session = requests.Session()
    print("Probing eToro account environment…")
    env = probe_environment(session)
    print(f"  environment: {env}")

    print("Resolving AAPL instrument ID…")
    instrument_id = search_instrument(session, "AAPL")
    if not instrument_id:
        print("FAIL: could not resolve AAPL instrumentId")
        return 1
    print(f"  instrumentId: {instrument_id}")

    print("Fetching live rate…")
    rate = fetch_rate(session, instrument_id)
    bid = rate.get("bid") or rate.get("Bid")
    ask = rate.get("ask") or rate.get("Ask")
    print(f"  bid={bid} ask={ask}")

    pnl_path = f"{ETORO_API_BASE}/trading/info/{env}/pnl"
    pnl_resp = session.get(pnl_path, headers=_auth_headers(), timeout=30)
    pnl_resp.raise_for_status()
    portfolio = pnl_resp.json().get("clientPortfolio") or {}
    credit = portfolio.get("credit")
    print(f"  account credit: {credit}")

    print("OK: eToro smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
