"""Google News RSS headlines via the gnews package (keyword search per symbol)."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from app.config import GNEWS_ENABLED, GNEWS_MAX_RESULTS, GNEWS_PERIOD, SYMBOLS
from app.services.altdata.sentiment_lexicon import score_text_sentiment
from app.services.massive_symbols import is_crypto_terminal_symbol

logger = logging.getLogger(__name__)

SOURCE_GNEWS = "gnews"

_CRYPTO_QUERY_OVERRIDES: dict[str, str] = {
    "BTC": "Bitcoin cryptocurrency",
    "ETH": "Ethereum cryptocurrency",
    "SOL": "Solana cryptocurrency",
    "ADA": "Cardano cryptocurrency",
    "XRP": "Ripple XRP cryptocurrency",
    "DOGE": "Dogecoin cryptocurrency",
}


def _event_id(symbol: str, published: str, headline: str) -> str:
    digest = hashlib.sha1(f"{SOURCE_GNEWS}:{symbol}:{published}:{headline}".encode()).hexdigest()[:16]
    return f"{SOURCE_GNEWS}:{symbol.upper()}:{digest}"


def gnews_search_query(symbol: str) -> str:
    """Map terminal symbol to a Google News keyword query."""
    sym = str(symbol or "").upper().strip()
    if not sym:
        return ""

    if is_crypto_terminal_symbol(sym):
        info = SYMBOLS.get(sym) or {}
        asset = str(info.get("asset") or sym.replace("USDT", "")).upper()
        return _CRYPTO_QUERY_OVERRIDES.get(asset, f"{asset} cryptocurrency")

    if sym in ("SPY",):
        return "S&P 500 ETF"
    if sym in ("QQQ",):
        return "Nasdaq 100 ETF"
    return f"{sym} stock"


def _parse_gnews_published(article: dict[str, Any]) -> str:
    raw = (
        article.get("published date")
        or article.get("published_date")
        or article.get("published")
    )
    if raw is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    text = str(raw).strip()
    if not text:
        return datetime.now(timezone.utc).isoformat()
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return text


def fetch_gnews_news(symbol: str) -> list[dict[str, Any]]:
    """Fetch symbol-related headlines from Google News RSS (no API key)."""
    if not GNEWS_ENABLED:
        return []

    sym = str(symbol or "").upper().strip()
    if not sym:
        return []

    query = gnews_search_query(sym)
    if not query:
        return []

    try:
        from gnews import GNews
    except ImportError:
        logger.debug("gnews package not installed")
        return []

    client = GNews(
        language="en",
        country="US",
        max_results=max(1, min(int(GNEWS_MAX_RESULTS), 30)),
        period=GNEWS_PERIOD or "7d",
    )

    try:
        articles = client.get_news(query) or []
    except Exception as exc:
        logger.debug("GNews fetch failed for %s (%s): %s", sym, query, exc)
        return []

    rows: list[dict[str, Any]] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        title = str(article.get("title") or "").strip()
        if not title:
            continue
        summary = str(article.get("description") or "").strip()
        text = f"{title}. {summary}".strip() if summary else title
        published = _parse_gnews_published(article)
        score = score_text_sentiment(text)
        rows.append({
            "id": _event_id(sym, published, title),
            "symbol": sym,
            "source": SOURCE_GNEWS,
            "score": score,
            "mention_count": 1,
            "headline": title[:500],
            "published_at": published,
            "raw": article,
        })
    return rows
