"""Financial news feed — Finnhub, Polygon/Massive, Alpaca, yfinance, Google News."""

from __future__ import annotations

import hashlib
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from app.config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    FINNHUB_API_KEY,
    GNEWS_ENABLED,
    MASSIVE_API_KEY,
    MASSIVE_REST_URL,
    SENTIMENT_ENABLED,
    SENTIMENT_LOOKBACK_HOURS,
    SYMBOLS,
)
from app.services.altdata.finnhub_provider import fetch_finnhub_company_news
from app.services.altdata.gnews_provider import SOURCE_GNEWS, fetch_gnews_news
from app.services.altdata.sentiment_lexicon import score_text_sentiment
from app.services.altdata.store import get_aggregate_sentiment, get_sentiment_events, upsert_sentiment_events
from app.services.massive_symbols import is_crypto_terminal_symbol, terminal_to_massive_rest_ticker
from app.services.synthetic_data import YF_SYMBOL_MAP

logger = logging.getLogger(__name__)

SOURCE_FINNHUB = "finnhub_news"
SOURCE_YFINANCE = "yfinance_news"
SOURCE_POLYGON = "news"
SOURCE_ALPACA = "alpaca_news"

HEADLINE_SOURCES: frozenset[str] = frozenset({
    SOURCE_FINNHUB,
    SOURCE_YFINANCE,
    SOURCE_POLYGON,
    SOURCE_ALPACA,
    SOURCE_GNEWS,
})

SOURCE_LABELS: dict[str, str] = {
    SOURCE_FINNHUB: "Finnhub",
    SOURCE_YFINANCE: "Yahoo Finance",
    SOURCE_POLYGON: "Polygon",
    SOURCE_ALPACA: "Alpaca",
    SOURCE_GNEWS: "Google News",
}

_ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"

# Single-entry TTL cache for market movers+headlines (bounded RAM).
_MARKET_NEWS_CACHE: dict[tuple, tuple[float, dict[str, Any]]] = {}
_MARKET_NEWS_CACHE_TTL_SEC = 45.0
_MARKET_NEWS_CACHE_MAX_KEYS = 4


def terminal_to_alpaca_news_symbol(symbol: str) -> str:
    """Map terminal symbol → Alpaca/Benzinga news ticker (AAPL, BTCUSD)."""
    s = str(symbol or "").strip().upper().replace("-", "").replace("/", "").replace(" ", "")
    if not s:
        return ""
    if is_crypto_terminal_symbol(s) or s.endswith("USDT") or s.endswith("USD"):
        if s.endswith("USDT"):
            return f"{s[:-4]}USD"
        if s.endswith("USD") and not s.endswith("USDT"):
            return s
        info = SYMBOLS.get(s) or {}
        asset = str(info.get("asset") or "").upper()
        if asset and asset not in ("USD", "USDT"):
            return f"{asset}USD"
    return s


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
    if source == SOURCE_ALPACA:
        return str(raw.get("url") or "").strip() or None
    if source == SOURCE_GNEWS:
        return str(raw.get("url") or "").strip() or None
    return str(raw.get("url") or raw.get("link") or "").strip() or None


def _extract_summary(source: str, raw: dict[str, Any], headline: str) -> str | None:
    if not isinstance(raw, dict):
        return None
    if source == SOURCE_FINNHUB:
        text = str(raw.get("summary") or "").strip()
    elif source == SOURCE_POLYGON:
        text = str(raw.get("description") or raw.get("summary") or "").strip()
    elif source == SOURCE_ALPACA:
        text = str(raw.get("summary") or "").strip()
    elif source == SOURCE_GNEWS:
        text = str(raw.get("description") or "").strip()
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
    related = row.get("related_symbols")
    if not isinstance(related, list):
        related = raw.get("symbols") if isinstance(raw.get("symbols"), list) else []
    related_syms = [str(s).strip().upper() for s in related if str(s).strip()]
    return {
        "id": row.get("id"),
        "symbol": str(row.get("symbol") or "").upper(),
        "related_symbols": related_syms,
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


def _alpaca_news_headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": ALPACA_API_KEY or "",
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY or "",
    }


def _rows_from_alpaca_news_payload(
    items: list[Any],
    *,
    default_symbol: str,
    prefer_article_symbols: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("headline") or item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        text = f"{title}. {summary}".strip() if summary else title
        if not text:
            continue
        published = _parse_published(
            item.get("created_at") or item.get("updated_at") or item.get("published_at")
        )
        art_syms = [
            str(s).strip().upper()
            for s in (item.get("symbols") or [])
            if str(s).strip()
        ]
        if prefer_article_symbols and art_syms:
            sym = art_syms[0]
        else:
            sym = str(default_symbol or "MARKET").upper()
        art_id = item.get("id")
        if art_id is not None:
            eid = f"{SOURCE_ALPACA}:{sym}:{art_id}"
        else:
            eid = _event_id(SOURCE_ALPACA, sym, published, title)
        score = score_text_sentiment(text)
        rows.append({
            "id": eid,
            "symbol": sym,
            "source": SOURCE_ALPACA,
            "score": score,
            "mention_count": 1,
            "headline": title[:500],
            "published_at": published,
            "raw": item,
            "related_symbols": art_syms,
        })
    return rows


def fetch_alpaca_news(symbol: str) -> list[dict[str, Any]]:
    """Alpaca/Benzinga news REST — same shape as Polygon for sentiment + News tab."""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return []
    ticker = terminal_to_alpaca_news_symbol(symbol)
    if not ticker:
        return []

    lookback_h = max(1.0, float(SENTIMENT_LOOKBACK_HOURS or 24))
    start = (datetime.now(timezone.utc) - timedelta(hours=lookback_h)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    params = {
        "symbols": ticker,
        "limit": 30,
        "sort": "desc",
        "start": start,
        "include_content": "false",
    }
    try:
        with httpx.Client(timeout=20.0, headers=_alpaca_news_headers()) as client:
            resp = client.get(_ALPACA_NEWS_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.debug("Alpaca news fetch failed for %s (%s): %s", symbol, ticker, exc)
        return []

    items = data.get("news") if isinstance(data, dict) else []
    return _rows_from_alpaca_news_payload(items or [], default_symbol=symbol)


def fetch_alpaca_news_for_tickers(
    tickers: list[str],
    *,
    limit: int = 30,
    lookback_hours: float | None = None,
) -> list[dict[str, Any]]:
    """Batch Alpaca news for comma-joined tickers (movers / market headlines)."""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return []
    cleaned = []
    seen: set[str] = set()
    for t in tickers or []:
        s = str(t or "").strip().upper()
        if not s or s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
    if not cleaned:
        return []

    lookback_h = max(1.0, float(lookback_hours if lookback_hours is not None else SENTIMENT_LOOKBACK_HOURS or 24))
    start = (datetime.now(timezone.utc) - timedelta(hours=lookback_h)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    params = {
        "symbols": ",".join(cleaned[:40]),
        "limit": max(1, min(int(limit), 50)),
        "sort": "desc",
        "start": start,
        "include_content": "false",
    }
    try:
        with httpx.Client(timeout=25.0, headers=_alpaca_news_headers()) as client:
            resp = client.get(_ALPACA_NEWS_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.debug("Alpaca batch news fetch failed: %s", exc)
        return []

    items = data.get("news") if isinstance(data, dict) else []
    return _rows_from_alpaca_news_payload(
        items or [],
        default_symbol="MARKET",
        prefer_article_symbols=True,
    )


def fetch_alpaca_top_headlines(
    *,
    limit: int = 20,
    lookback_hours: float | None = None,
) -> list[dict[str, Any]]:
    """Global Alpaca/Benzinga top headlines (no symbol filter)."""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return []
    lookback_h = max(1.0, float(lookback_hours if lookback_hours is not None else SENTIMENT_LOOKBACK_HOURS or 24))
    start = (datetime.now(timezone.utc) - timedelta(hours=lookback_h)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    params = {
        "limit": max(1, min(int(limit), 50)),
        "sort": "desc",
        "start": start,
        "include_content": "false",
    }
    try:
        with httpx.Client(timeout=20.0, headers=_alpaca_news_headers()) as client:
            resp = client.get(_ALPACA_NEWS_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.debug("Alpaca top headlines fetch failed: %s", exc)
        return []

    items = data.get("news") if isinstance(data, dict) else []
    return _rows_from_alpaca_news_payload(
        items or [],
        default_symbol="MARKET",
        prefer_article_symbols=True,
    )


def available_news_sources() -> list[str]:
    from app.config import TERMINAL_MODE

    sources: list[str] = []
    # LIVE_ALPACA: prefer Benzinga/Alpaca headlines (parity with Polygon on Massive).
    if ALPACA_API_KEY and ALPACA_SECRET_KEY and TERMINAL_MODE == "LIVE_ALPACA":
        sources.append(SOURCE_ALPACA)
        if FINNHUB_API_KEY:
            sources.append(SOURCE_FINNHUB)
        # Skip Yahoo/Polygon/GNews as primary providers on Alpaca UI.
        return sources
    if FINNHUB_API_KEY:
        sources.append(SOURCE_FINNHUB)
    sources.append(SOURCE_YFINANCE)
    if ALPACA_API_KEY and ALPACA_SECRET_KEY:
        sources.append(SOURCE_ALPACA)
    if MASSIVE_API_KEY:
        sources.append(SOURCE_POLYGON)
    if GNEWS_ENABLED:
        sources.append(SOURCE_GNEWS)
    return sources


def _source_fetchers() -> dict[str, Callable[[str], list[dict[str, Any]]]]:
    fetchers: dict[str, Callable[[str], list[dict[str, Any]]]] = {
        SOURCE_YFINANCE: fetch_yfinance_news,
        SOURCE_POLYGON: fetch_polygon_news,
        SOURCE_ALPACA: fetch_alpaca_news,
    }
    if FINNHUB_API_KEY:
        fetchers[SOURCE_FINNHUB] = fetch_finnhub_company_news
    if GNEWS_ENABLED:
        fetchers[SOURCE_GNEWS] = fetch_gnews_news
    return fetchers


def fetch_symbol_news(
    symbol: str,
    *,
    sources: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch headline news from configured providers (deduped by headline)."""
    from app.config import TERMINAL_MODE

    sym = str(symbol or "").upper().strip()
    if not sym:
        return []

    fetchers = _source_fetchers()
    wanted = [s for s in (sources or available_news_sources()) if s in fetchers]
    if not wanted:
        # Last resort — never default Yahoo on LIVE_ALPACA.
        wanted = [SOURCE_ALPACA] if TERMINAL_MODE == "LIVE_ALPACA" and SOURCE_ALPACA in fetchers else [SOURCE_YFINANCE]

    batches: dict[str, list[dict[str, Any]]] = {}
    # Parallelize providers — sequential Finnhub+Yahoo+GNews+Polygon was ~9s live.
    workers = max(1, min(4, len(wanted)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetchers[source], sym): source for source in wanted}
        for fut in as_completed(futures):
            source = futures[fut]
            try:
                batches[source] = fut.result() or []
            except Exception as exc:
                logger.warning("News fetch error (%s) for %s: %s", source, sym, exc)
                batches[source] = []

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Merge in preferred source order so Alpaca wins duplicates on LIVE_ALPACA.
    for source in wanted:
        for row in batches.get(source) or []:
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


def get_market_news_feed(
    *,
    top: int = 10,
    limit: int = 40,
    lookback_hours: float | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Top movers + most-actives snapshot with Alpaca/Benzinga headlines.

    Combines screener gainers/losers with news for those tickers plus global
    top headlines — Alpaca's market-mover / headline surface for LIVE_ALPACA.

    Result is TTL-cached as a single in-memory payload (bounded RAM).
    """
    from app.services.altdata.alpaca_screener import (
        collect_mover_news_tickers,
        fetch_alpaca_market_snapshot,
    )

    lookback = float(lookback_hours if lookback_hours is not None else SENTIMENT_LOOKBACK_HOURS)
    limit = max(1, min(int(limit), 100))
    top_n = max(1, min(int(top), 25))
    cache_key = (top_n, limit, round(lookback, 2), bool(persist))
    cached = _MARKET_NEWS_CACHE.get(cache_key)
    if cached and (time.time() - float(cached[0])) < _MARKET_NEWS_CACHE_TTL_SEC:
        return cached[1]

    snapshot = fetch_alpaca_market_snapshot(top=top_n)
    mover_tickers = collect_mover_news_tickers(snapshot, limit=16)

    mover_rows: list[dict[str, Any]] = []
    top_rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_movers = pool.submit(
            fetch_alpaca_news_for_tickers,
            mover_tickers,
            limit=min(40, limit),
            lookback_hours=lookback,
        )
        fut_top = pool.submit(
            fetch_alpaca_top_headlines,
            limit=min(25, limit),
            lookback_hours=lookback,
        )
        try:
            mover_rows = fut_movers.result() or []
        except Exception as exc:
            logger.warning("Market mover news fetch failed: %s", exc)
        try:
            top_rows = fut_top.result() or []
        except Exception as exc:
            logger.warning("Market top headlines fetch failed: %s", exc)

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Prefer mover-linked headlines, then global top headlines.
    for row in mover_rows + top_rows:
        headline = str(row.get("headline") or "").lower().strip()
        if headline and headline in seen:
            continue
        if headline:
            seen.add(headline)
        rows.append(row)

    mover_set = {str(s).upper() for s in mover_tickers}

    def _market_rank(row: dict[str, Any]) -> tuple:
        related = {
            str(s).strip().upper()
            for s in (row.get("related_symbols") or [])
            if str(s).strip()
        }
        has_mover = 1 if (related & mover_set) else 0
        has_symbol = 1 if related else 0
        return (has_mover, has_symbol, _published_sort_key(row.get("published_at")))

    rows.sort(key=_market_rank, reverse=True)

    if persist and rows and SENTIMENT_ENABLED:
        # Persist only symbol-tagged articles (skip pure MARKET politics with no tickers).
        persistable = [
            r for r in rows
            if str(r.get("symbol") or "").upper() not in ("", "MARKET")
            or (r.get("related_symbols") or [])
        ]
        if persistable:
            try:
                upsert_sentiment_events(persistable[:80])
            except Exception as exc:
                logger.warning("Market news persist failed: %s", exc)

    items = [normalize_news_row(r) for r in rows[:limit]]
    scores = [float(i["score"]) for i in items if i.get("score") is not None]
    aggregate = {
        "aggregate_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "mention_count": len(scores),
        "sources": sorted({i["source"] for i in items}),
        "sample_headlines": [i["headline"][:120] for i in items[:3] if i.get("headline")],
    }

    sources_avail = available_news_sources()
    feed = {
        "symbol": "MARKET",
        "scope": "market",
        "items": items,
        "aggregate": aggregate,
        "movers": {
            "stocks": snapshot.get("stocks") or {},
            "crypto": snapshot.get("crypto") or {},
            "most_actives": snapshot.get("most_actives") or [],
        },
        "mover_news_symbols": mover_tickers,
        "sources_available": sources_avail,
        "lookback_hours": lookback,
        "fetched_at": time.time(),
        "refresh": True,
    }
    _MARKET_NEWS_CACHE[cache_key] = (time.time(), feed)
    while len(_MARKET_NEWS_CACHE) > _MARKET_NEWS_CACHE_MAX_KEYS:
        oldest_key = min(_MARKET_NEWS_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _MARKET_NEWS_CACHE.pop(oldest_key, None)
    return feed
