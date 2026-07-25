"""Alpaca market screener — top movers + most-actives (Benzinga/news companion)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import ALPACA_API_KEY, ALPACA_SECRET_KEY
from app.services.alpaca_data import alpaca_crypto_to_terminal

logger = logging.getLogger(__name__)

_SCREENER_BASE = "https://data.alpaca.markets/v1beta1/screener"


def _headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": ALPACA_API_KEY or "",
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY or "",
    }


def screener_symbol_to_news_ticker(symbol: str) -> str:
    """Map screener symbol (AAPL, WIF/USD, UNI/USDT) → Alpaca news ticker."""
    s = str(symbol or "").strip().upper().replace("-", "/").replace(" ", "")
    if not s:
        return ""
    if "/" in s:
        base, quote = s.split("/", 1)
        if quote in ("USD", "USDT", "USDC"):
            return f"{base}USD"
        return f"{base}{quote}"
    return s.replace("/", "")


def screener_symbol_to_terminal(symbol: str) -> str:
    """Map screener symbol → terminal watchlist form when possible."""
    s = str(symbol or "").strip().upper().replace("-", "/").replace(" ", "")
    if not s:
        return ""
    if "/" in s:
        return alpaca_crypto_to_terminal(s)
    return s


def _normalize_mover_row(row: dict[str, Any], *, side: str, market_type: str) -> dict[str, Any]:
    raw_sym = str(row.get("symbol") or "").strip().upper()
    return {
        "symbol": raw_sym,
        "terminal_symbol": screener_symbol_to_terminal(raw_sym),
        "news_symbol": screener_symbol_to_news_ticker(raw_sym),
        "side": side,
        "market_type": market_type,
        "price": row.get("price"),
        "change": row.get("change"),
        "percent_change": row.get("percent_change"),
    }


def fetch_alpaca_movers(
    market_type: str = "stocks",
    *,
    top: int = 10,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """GET /v1beta1/screener/{stocks|crypto}/movers."""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return {"gainers": [], "losers": [], "market_type": market_type, "last_updated": None}

    mt = "crypto" if str(market_type).lower().startswith("crypto") else "stocks"
    top_n = max(1, min(int(top), 50))
    # Over-fetch so warrant/unit filtering still leaves a full board.
    fetch_top = min(50, max(top_n * 2, top_n + 6))
    try:
        with httpx.Client(timeout=timeout, headers=_headers()) as client:
            resp = client.get(f"{_SCREENER_BASE}/{mt}/movers", params={"top": fetch_top})
            resp.raise_for_status()
            data = resp.json() if isinstance(resp.json(), dict) else {}
    except Exception as exc:
        logger.debug("Alpaca movers fetch failed (%s): %s", mt, exc)
        return {"gainers": [], "losers": [], "market_type": mt, "last_updated": None, "error": str(exc)}

    gainers = [
        _normalize_mover_row(r, side="gainer", market_type=mt)
        for r in (data.get("gainers") or [])
        if isinstance(r, dict) and not _is_noisy_screener_ticker(str(r.get("symbol") or ""))
    ][:top_n]
    losers = [
        _normalize_mover_row(r, side="loser", market_type=mt)
        for r in (data.get("losers") or [])
        if isinstance(r, dict) and not _is_noisy_screener_ticker(str(r.get("symbol") or ""))
    ][:top_n]
    return {
        "gainers": gainers,
        "losers": losers,
        "market_type": data.get("market_type") or mt,
        "last_updated": data.get("last_updated"),
    }


def fetch_alpaca_most_actives(
    *,
    by: str = "volume",
    top: int = 10,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """GET /v1beta1/screener/stocks/most-actives."""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return {"most_actives": [], "last_updated": None}

    metric = by if by in ("volume", "trades") else "volume"
    top_n = max(1, min(int(top), 50))
    try:
        with httpx.Client(timeout=timeout, headers=_headers()) as client:
            resp = client.get(
                f"{_SCREENER_BASE}/stocks/most-actives",
                params={"by": metric, "top": top_n},
            )
            resp.raise_for_status()
            data = resp.json() if isinstance(resp.json(), dict) else {}
    except Exception as exc:
        logger.debug("Alpaca most-actives fetch failed: %s", exc)
        return {"most_actives": [], "last_updated": None, "error": str(exc)}

    rows: list[dict[str, Any]] = []
    for r in data.get("most_actives") or []:
        if not isinstance(r, dict):
            continue
        raw_sym = str(r.get("symbol") or "").strip().upper()
        if not raw_sym or _is_noisy_screener_ticker(raw_sym):
            continue
        rows.append(
            {
                "symbol": raw_sym,
                "terminal_symbol": screener_symbol_to_terminal(raw_sym),
                "news_symbol": screener_symbol_to_news_ticker(raw_sym),
                "volume": r.get("volume"),
                "trade_count": r.get("trade_count"),
            }
        )
    return {
        "most_actives": rows,
        "last_updated": data.get("last_updated"),
        "by": metric,
    }


def fetch_alpaca_market_snapshot(*, top: int = 10) -> dict[str, Any]:
    """Stocks + crypto movers and equity most-actives in one snapshot."""
    top_n = max(1, min(int(top), 25))
    stocks = fetch_alpaca_movers("stocks", top=top_n)
    crypto = fetch_alpaca_movers("crypto", top=top_n)
    actives = fetch_alpaca_most_actives(top=top_n)
    return {
        "stocks": stocks,
        "crypto": crypto,
        "most_actives": actives.get("most_actives") or [],
        "most_actives_meta": {
            "last_updated": actives.get("last_updated"),
            "by": actives.get("by"),
            "error": actives.get("error"),
        },
    }


def _is_noisy_screener_ticker(symbol: str) -> bool:
    """Warrants/units rarely have useful Benzinga coverage for headlines."""
    s = str(symbol or "").strip().upper()
    if not s or "." in s:
        return True
    if s.endswith(("WW", "WS")):
        return True
    if len(s) >= 5 and s.endswith("W") and not s.endswith("USD"):
        return True
    return False


def collect_mover_news_tickers(snapshot: dict[str, Any], *, limit: int = 16) -> list[str]:
    """Unique Alpaca news tickers from movers/actives (prefer liquid names)."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(news_sym: str) -> None:
        s = str(news_sym or "").strip().upper()
        if not s or s in seen or _is_noisy_screener_ticker(s):
            return
        seen.add(s)
        out.append(s)

    for bucket in ("stocks", "crypto"):
        block = snapshot.get(bucket) or {}
        for side in ("gainers", "losers"):
            for row in block.get(side) or []:
                if isinstance(row, dict):
                    _add(row.get("news_symbol") or row.get("symbol"))
                if len(out) >= limit:
                    return out

    for row in snapshot.get("most_actives") or []:
        if isinstance(row, dict):
            _add(row.get("news_symbol") or row.get("symbol"))
        if len(out) >= limit:
            break
    return out
