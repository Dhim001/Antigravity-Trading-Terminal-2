"""News/social sentiment ingestion — Finnhub, Polygon, yfinance."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import (
    FINNHUB_API_KEY,
    GNEWS_ENABLED,
    SENTIMENT_LOOKBACK_HOURS,
    SYMBOLS,
)
from app.services.altdata.finnhub_provider import fetch_finnhub_sentiment
from app.services.altdata.gnews_provider import fetch_gnews_news
from app.services.altdata.news_provider import (
    fetch_polygon_news,
    fetch_yfinance_news,
)
from app.services.altdata.store import upsert_sentiment_events

logger = logging.getLogger(__name__)


def fetch_symbol_sentiment(symbol: str) -> list[dict[str, Any]]:
    """Fetch fresh sentiment rows for one symbol (Finnhub + Polygon + yfinance)."""
    sym = str(symbol or "").upper().strip()
    if not sym:
        return []

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(batch: list[dict[str, Any]]) -> None:
        for row in batch:
            headline = str(row.get("headline") or "")
            dedupe = headline.lower().strip()
            if dedupe and dedupe in seen:
                continue
            if dedupe:
                seen.add(dedupe)
            rows.append(row)

    if FINNHUB_API_KEY:
        _add(fetch_finnhub_sentiment(sym))
    _add(fetch_polygon_news(sym))
    if GNEWS_ENABLED:
        _add(fetch_gnews_news(sym))
    if not rows:
        _add(fetch_yfinance_news(sym))
    return rows


def refresh_sentiment(symbols: list[str] | None = None) -> dict[str, Any]:
    """Poll news sources and persist scored sentiment events."""
    from app.config import SENTIMENT_ENABLED

    if not SENTIMENT_ENABLED:
        return {"enabled": False, "reason": "SENTIMENT_ENABLED=false"}

    syms = [str(s).upper() for s in (symbols or []) if str(s).strip()]
    if not syms:
        syms = list(SYMBOLS.keys())[:40]

    all_rows: list[dict[str, Any]] = []
    per_symbol: dict[str, int] = {}
    for sym in syms:
        try:
            rows = fetch_symbol_sentiment(sym)
        except Exception as exc:
            logger.warning("Sentiment fetch error for %s: %s", sym, exc)
            rows = []
        if rows:
            all_rows.extend(rows)
            per_symbol[sym] = len(rows)

    written = upsert_sentiment_events(all_rows) if all_rows else 0
    return {
        "enabled": True,
        "symbols_polled": len(syms),
        "events_fetched": len(all_rows),
        "events_written": written,
        "per_symbol": per_symbol,
        "lookback_hours": SENTIMENT_LOOKBACK_HOURS,
        "fetched_at": time.time(),
        "finnhub_enabled": bool(FINNHUB_API_KEY),
    }
