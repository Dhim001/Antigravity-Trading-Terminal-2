"""Finnhub.io news and news-sentiment feed."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import FINNHUB_API_KEY, FINNHUB_API_URL, SENTIMENT_LOOKBACK_HOURS
from app.services.altdata.sentiment_lexicon import score_text_sentiment
from app.services.massive_symbols import is_crypto_terminal_symbol

logger = logging.getLogger(__name__)

_SOURCE_NEWS = "finnhub_news"
_SOURCE_SENTIMENT = "finnhub_sentiment"


def _finnhub_symbol(symbol: str) -> str | None:
    sym = str(symbol or "").upper().strip()
    if not sym or is_crypto_terminal_symbol(sym):
        return None
    if sym.endswith("USDT"):
        return None
    return sym


def _get_json(path: str, params: dict | None = None) -> dict | list | None:
    if not FINNHUB_API_KEY:
        return None
    url = f"{FINNHUB_API_URL}{path}"
    q = dict(params or {})
    q["token"] = FINNHUB_API_KEY
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, params=q)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.debug("Finnhub %s failed: %s", path, exc)
        return None


def fetch_finnhub_company_news(symbol: str) -> list[dict[str, Any]]:
    """Company news headlines for the last SENTIMENT_LOOKBACK_HOURS."""
    finn_sym = _finnhub_symbol(symbol)
    if not finn_sym:
        return []

    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=max(24.0, float(SENTIMENT_LOOKBACK_HOURS)))
    data = _get_json(
        "/company-news",
        {
            "symbol": finn_sym,
            "from": start.strftime("%Y-%m-%d"),
            "to": now.strftime("%Y-%m-%d"),
        },
    )
    if not isinstance(data, list):
        return []

    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("headline") or item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        text = f"{title}. {summary}".strip()
        if not text:
            continue
        published_ts = item.get("datetime")
        if isinstance(published_ts, (int, float)):
            published = datetime.fromtimestamp(int(published_ts), tz=timezone.utc).isoformat()
        else:
            published = str(item.get("datetime") or now.isoformat())
        score = score_text_sentiment(text)
        item_id = str(item.get("id") or item.get("url") or title[:40])
        rows.append({
            "id": f"{_SOURCE_NEWS}:{finn_sym}:{item_id}",
            "symbol": finn_sym,
            "source": _SOURCE_NEWS,
            "score": score,
            "mention_count": 1,
            "headline": title[:500],
            "published_at": published,
            "raw": item,
        })
    return rows


def fetch_finnhub_news_sentiment(symbol: str) -> list[dict[str, Any]]:
    """Aggregate news-sentiment score from Finnhub (when API returns data)."""
    finn_sym = _finnhub_symbol(symbol)
    if not finn_sym:
        return []

    data = _get_json("/news-sentiment", {"symbol": finn_sym})
    if not isinstance(data, dict):
        return []

    score = 0.0
    sentiment = data.get("sentiment") if isinstance(data.get("sentiment"), dict) else {}
    bullish = float(sentiment.get("bullishPercent") or 0.0)
    bearish = float(sentiment.get("bearishPercent") or 0.0)
    if bullish or bearish:
        score = round(max(-1.0, min(1.0, (bullish - bearish) / 100.0)), 4)
    elif data.get("companyNewsScore") is not None:
        raw = float(data["companyNewsScore"])
        score = round(max(-1.0, min(1.0, (raw - 0.5) * 2.0)), 4)

    buzz = data.get("buzz") if isinstance(data.get("buzz"), dict) else {}
    mentions = int(buzz.get("articlesInLastWeek") or buzz.get("weeklyAverage") or 1)
    now = datetime.now(timezone.utc).isoformat()
    return [{
        "id": f"{_SOURCE_SENTIMENT}:{finn_sym}:aggregate",
        "symbol": finn_sym,
        "source": _SOURCE_SENTIMENT,
        "score": score,
        "mention_count": max(1, mentions),
        "headline": f"Finnhub news sentiment ({bullish:.0f}% bull / {bearish:.0f}% bear)",
        "published_at": now,
        "raw": data,
    }]


def fetch_finnhub_sentiment(symbol: str) -> list[dict[str, Any]]:
    rows = fetch_finnhub_company_news(symbol)
    agg = fetch_finnhub_news_sentiment(symbol)
    if agg:
        rows.extend(agg)
    return rows
