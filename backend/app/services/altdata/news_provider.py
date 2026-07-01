"""Financial news feed — Finnhub, Polygon/Massive, yfinance (free tiers)."""

from __future__ import annotations

import hashlib
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from app.config import (
    FINNHUB_API_KEY,
    MASSIVE_API_KEY,
    MASSIVE_REST_URL,
    SENTIMENT_ENABLED,
    SENTIMENT_LOOKBACK_HOURS,
    SYMBOLS,
)
from app.services.altdata.finnhub_provider import fetch_finnhub_company_news
from app.services.altdata.sentiment_lexicon import score_text_sentiment
from app.services.altdata.store import get_aggregate_sentiment, get_sentiment_events, upsert_sentiment_events
from app.services.massive_symbols import is_crypto_terminal_symbol, terminal_to_massive_rest_ticker
from app.services.synthetic_data import YF_SYMBOL_MAP

logger = logging.getLogger(__name__)

SOURCE_FINNHUB = "finnhub_news"
SOURCE_YFINANCE = "yfinance_news"
SOURCE_POLYGON = "news"

HEADLINE_SOURCES: frozenset[str] = frozenset({
    SOURCE_FINNHUB,
    SOURCE_YFINANCE,
    SOURCE_POLYGON,
})

SOURCE_LABELS: dict[str, str] = {
    SOURCE_FINNHUB: "Finnhub",
    SOURCE_YFINANCE: "Yahoo Finance",
    SOURCE_POLYGON: "Polygon",
}


def _event_id(source: str, symbol: str, published: str, headline: str) -> str:
    digest = hashlib.sha1(f"{source}:{symbol}:{published}:{headline}".encode()).hexdigest()[:16]
    return f"{source}:{symbol.upper()}:{digest}"


def _parse_published(val: Any) -> str:
    if val is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(val, (int, float)):
        ts = float(val)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    text = str(val).strip()
    if text.isdigit():
        ts = float(text)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return text


def _published_sort_key(val: str | None) -> float:
    if not val:
        return 0.0
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        n = float(val)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(n):
        return default
    return n


def _yfinance_url_from_block(raw: dict[str, Any]) -> str | None:
    content = raw.get("content")
    if not isinstance(content, dict):
        return str(raw.get("link") or raw.get("url") or "").strip() or None
    for key in ("clickThroughUrl", "canonicalUrl", "previewUrl"):
        block = content.get(key)
        if isinstance(block, dict):
            url = str(block.get("url") or "").strip()
            if url:
                return url
    return str(raw.get("link") or raw.get("url") or "").strip() or None


def _flatten_yfinance_item(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy and nested Yahoo Finance news payloads."""
    content = item.get("content")
    if not isinstance(content, dict):
        return item
    title = str(content.get("title") or item.get("title") or "").strip()
    summary = str(content.get("summary") or content.get("description") or "").strip()
    link = _yfinance_url_from_block(item)
    pub = content.get("pubDate") or content.get("displayTime") or item.get("providerPublishTime")
    return {
        **item,
        "title": title,
        "summary": summary,
        "description": summary,
        "link": link,
        "pubDate": pub,
        "providerPublishTime": pub,
    }


def _extract_url(source: str, raw: dict[str, Any]) -> str | None:
    if not isinstance(raw, dict):
        return None
    if source == SOURCE_FINNHUB:
        return str(raw.get("url") or "").strip() or None
    if source == SOURCE_YFINANCE:
        return _yfinance_url_from_block(raw)
    if source == SOURCE_POLYGON:
        return str(raw.get("article_url") or raw.get("amp_url") or raw.get("url") or "").strip() or None
    return str(raw.get("url") or raw.get("link") or "").strip() or None


def _extract_summary(source: str, raw: dict[str, Any], headline: str) -> str | None:
    if not isinstance(raw, dict):
        return None
    if source == SOURCE_FINNHUB:
        text = str(raw.get("summary") or "").strip()
    elif source == SOURCE_POLYGON:
        text = str(raw.get("description") or raw.get("summary") or "").strip()
    else:
        text = str(raw.get("summary") or raw.get("description") or "").strip()
    if not text or text == headline:
        return None
    return text[:600]


def normalize_news_row(row: dict[str, Any]) -> dict[str, Any]:
    """Public news item for API/UI."""
    source = str(row.get("source") or "")
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    headline = str(row.get("headline") or "").strip()
    return {
        "id": row.get("id"),
        "symbol": str(row.get("symbol") or "").upper(),
        "headline": headline,
        "summary": _extract_summary(source, raw, headline),
        "url": _extract_url(source, raw),
        "source": source,
        "source_label": SOURCE_LABELS.get(source, source.replace("_", " ").title()),
        "score": _safe_float(row.get("score")),
        "published_at": row.get("published_at"),
    }


def fetch_yfinance_news(symbol: str) -> list[dict[str, Any]]:
    yf_sym = YF_SYMBOL_MAP.get(symbol.upper(), symbol.upper())
    if is_crypto_terminal_symbol(symbol) and yf_sym == symbol.upper():
        yf_sym = symbol.upper().replace("USDT", "-USD")
    try:
        import yfinance as yf

        items = yf.Ticker(yf_sym).news or []
    except Exception as exc:
        logger.debug("yfinance news fetch failed for %s: %s", symbol, exc)
        return []

    rows: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        flat = _flatten_yfinance_item(item)
        title = str(flat.get("title") or "").strip()
        if not title:
            continue
        summary = str(flat.get("summary") or flat.get("description") or "").strip()
        text = f"{title}. {summary}".strip() if summary else title
        published = _parse_published(
            flat.get("providerPublishTime") or flat.get("pubDate") or flat.get("displayTime")
        )
        score = score_text_sentiment(text)
        rows.append({
            "id": _event_id(SOURCE_YFINANCE, symbol, published, title),
            "symbol": symbol.upper(),
            "source": SOURCE_YFINANCE,
            "score": score,
            "mention_count": 1,
            "headline": title[:500],
            "published_at": published,
            "raw": flat,
        })
    return rows


def fetch_polygon_news(symbol: str) -> list[dict[str, Any]]:
    if not MASSIVE_API_KEY:
        return []
    info = SYMBOLS.get(symbol, {})
    ticker = terminal_to_massive_rest_ticker(symbol, info)
    url = f"{MASSIVE_REST_URL.rstrip('/')}/v2/reference/news"
    params = {"ticker": ticker, "limit": 30, "order": "desc", "apiKey": MASSIVE_API_KEY}
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.debug("Polygon news fetch failed for %s: %s", symbol, exc)
        return []

    items = data.get("results") if isinstance(data, dict) else []
    rows: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        desc = str(item.get("description") or item.get("summary") or "").strip()
        text = f"{title}. {desc}".strip()
        if not text:
            continue
        published = _parse_published(
            item.get("published_utc") or item.get("published") or item.get("created_at")
        )
        score = score_text_sentiment(text)
        rows.append({
            "id": _event_id(SOURCE_POLYGON, symbol, published, title),
            "symbol": symbol.upper(),
            "source": SOURCE_POLYGON,
            "score": score,
            "mention_count": 1,
            "headline": title[:500],
            "published_at": published,
            "raw": item,
        })
    return rows


def available_news_sources() -> list[str]:
    sources: list[str] = [SOURCE_YFINANCE]
    if FINNHUB_API_KEY:
        sources.insert(0, SOURCE_FINNHUB)
    if MASSIVE_API_KEY:
        sources.append(SOURCE_POLYGON)
    return sources


def _source_fetchers() -> dict[str, Callable[[str], list[dict[str, Any]]]]:
    fetchers: dict[str, Callable[[str], list[dict[str, Any]]]] = {
        SOURCE_YFINANCE: fetch_yfinance_news,
        SOURCE_POLYGON: fetch_polygon_news,
    }
    if FINNHUB_API_KEY:
        fetchers[SOURCE_FINNHUB] = fetch_finnhub_company_news
    return fetchers


def fetch_symbol_news(
    symbol: str,
    *,
    sources: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch headline news from configured free providers (deduped by headline)."""
    sym = str(symbol or "").upper().strip()
    if not sym:
        return []

    fetchers = _source_fetchers()
    wanted = [s for s in (sources or available_news_sources()) if s in fetchers]
    if not wanted:
        wanted = [SOURCE_YFINANCE]

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for source in wanted:
        try:
            batch = fetchers[source](sym)
        except Exception as exc:
            logger.warning("News fetch error (%s) for %s: %s", source, sym, exc)
            batch = []
        for row in batch:
            if str(row.get("source") or "") not in HEADLINE_SOURCES:
                continue
            headline = str(row.get("headline") or "").lower().strip()
            if headline and headline in seen:
                continue
            if headline:
                seen.add(headline)
            rows.append(row)

    rows.sort(key=lambda r: _published_sort_key(r.get("published_at")), reverse=True)
    return rows


def _rows_from_store(symbol: str, *, lookback_hours: float, limit: int) -> list[dict[str, Any]]:
    stored = get_sentiment_events(symbol, lookback_hours=lookback_hours, limit=limit * 2)
    rows = [r for r in stored if str(r.get("source") or "") in HEADLINE_SOURCES]
    rows.sort(key=lambda r: _published_sort_key(r.get("published_at")), reverse=True)
    return rows[:limit]


def get_symbol_news_feed(
    symbol: str,
    *,
    refresh: bool = False,
    lookback_hours: float | None = None,
    limit: int = 40,
    sources: list[str] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Cached or live news feed with aggregate sentiment summary."""
    sym = str(symbol or "").upper().strip()
    lookback = float(lookback_hours if lookback_hours is not None else SENTIMENT_LOOKBACK_HOURS)
    limit = max(1, min(int(limit), 100))
    sources_avail = available_news_sources()

    rows: list[dict[str, Any]] = []
    if refresh and SENTIMENT_ENABLED:
        rows = fetch_symbol_news(sym, sources=sources)
        if persist and rows:
            try:
                upsert_sentiment_events(rows)
            except Exception as exc:
                logger.warning("News persist failed for %s: %s", sym, exc)
    if not rows:
        rows = _rows_from_store(sym, lookback_hours=lookback, limit=limit)
    if not rows and SENTIMENT_ENABLED:
        rows = fetch_symbol_news(sym, sources=sources)
        if persist and rows:
            try:
                upsert_sentiment_events(rows)
            except Exception as exc:
                logger.warning("News persist failed for %s: %s", sym, exc)

    items = [normalize_news_row(r) for r in rows[:limit]]
    aggregate = get_aggregate_sentiment(sym, lookback_hours=lookback)
    if isinstance(aggregate, dict):
        aggregate = {
            **aggregate,
            "aggregate_score": _safe_float(aggregate.get("aggregate_score")),
        }
    headline_scores = [float(i["score"]) for i in items if i.get("score") is not None]
    if headline_scores and not aggregate.get("mention_count"):
        aggregate = {
            **aggregate,
            "aggregate_score": round(sum(headline_scores) / len(headline_scores), 4),
            "mention_count": len(headline_scores),
            "sources": sorted({i["source"] for i in items}),
        }

    return {
        "symbol": sym,
        "items": items,
        "aggregate": aggregate,
        "sources_available": sources_avail,
        "lookback_hours": lookback,
        "fetched_at": time.time(),
        "refresh": refresh,
    }
