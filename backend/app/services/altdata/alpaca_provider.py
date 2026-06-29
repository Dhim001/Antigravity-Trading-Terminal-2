"""Alpaca corporate-actions fallback when Massive key is unavailable."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import httpx

from app.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, SYMBOLS
from app.services.altdata.store import upsert_corporate_events
from app.services.massive_symbols import is_crypto_terminal_symbol

logger = logging.getLogger(__name__)

_ALPACA_DATA = "https://data.alpaca.markets"
_SOURCE = "ALPACA"


def refresh_altdata(symbols: list[str] | None = None) -> dict[str, Any]:
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return {"enabled": False, "corporate_written": 0}

    syms = symbols or [s for s, info in SYMBOLS.items() if info.get("asset") == "USD"]
    syms = [s for s in syms if not is_crypto_terminal_symbol(s)]
    start = (date.today() - timedelta(days=365)).isoformat()
    end = (date.today() + timedelta(days=90)).isoformat()
    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    corporate: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=25.0, headers=headers) as client:
            for sym in syms:
                resp = client.get(
                    f"{_ALPACA_DATA}/v1/corporate-actions",
                    params={"symbols": sym, "types": "dividend,split", "start": start, "end": end},
                )
                if resp.status_code != 200:
                    continue
                payload = resp.json()
                for div in payload.get("corporate_actions", {}).get("cash_dividends") or []:
                    ex = div.get("ex_date") or ""
                    corporate.append({
                        "id": f"div:{sym}:{ex}:{div.get('rate')}",
                        "symbol": sym,
                        "event_type": "dividend",
                        "event_date": str(ex),
                        "title": f"Dividend ${div.get('rate')}",
                        "metadata": div,
                        "source": _SOURCE,
                    })
                for split in payload.get("corporate_actions", {}).get("splits") or []:
                    ex = split.get("ex_date") or ""
                    corporate.append({
                        "id": f"split:{sym}:{ex}:{split.get('new_rate')}:{split.get('old_rate')}",
                        "symbol": sym,
                        "event_type": "split",
                        "event_date": str(ex),
                        "title": "Split",
                        "metadata": split,
                        "source": _SOURCE,
                    })
    except Exception as exc:
        logger.warning("Alpaca alt-data refresh failed: %s", exc)
        return {"enabled": True, "error": str(exc), "corporate_written": 0}

    written = upsert_corporate_events(corporate)
    return {
        "enabled": True,
        "corporate_fetched": len(corporate),
        "corporate_written": written,
        "symbols": len(syms),
    }
