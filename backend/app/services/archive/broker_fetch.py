"""Fetch historical 1m OHLCV from broker/vendor REST APIs for archive ingestion."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from app.config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    BINANCE_BASE_URL,
    MASSIVE_API_KEY,
    MASSIVE_REST_URL,
    SYMBOLS,
    TERMINAL_MODE,
)
from app.services.archive.writer import align_bar_time
from app.services.massive_bars import aggs_to_candles
from app.services.massive_symbols import is_crypto_terminal_symbol, terminal_to_massive_rest_ticker

logger = logging.getLogger(__name__)

_SOURCE_MASSIVE = "MASSIVE_REST"
_SOURCE_BINANCE = "BINANCE_REST"
_SOURCE_ALPACA = "ALPACA_REST"
_ALPACA_DATA_REST = "https://data.alpaca.markets"


def _alpaca_headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }


def _parse_alpaca_bar_time(value: str | None) -> int:
    if not value:
        return 0
    try:
        if value.endswith("Z"):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return align_bar_time(int(dt.timestamp()))
    except (TypeError, ValueError):
        return 0


def fetch_alpaca_1m_bars(symbol: str, from_ts: int, to_ts: int) -> list[dict[str, Any]]:
    """Fetch 1m equity bars from Alpaca Market Data v2."""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return []
    if is_crypto_terminal_symbol(symbol):
        return []

    start = datetime.fromtimestamp(from_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = datetime.fromtimestamp(to_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{_ALPACA_DATA_REST}/v2/stocks/bars"
    params: dict[str, Any] = {
        "symbols": symbol.upper(),
        "timeframe": "1Min",
        "start": start,
        "end": end,
        "limit": 10000,
        "adjustment": "split",
        "sort": "asc",
    }
    all_bars: list[dict] = []
    max_pages = 20

    try:
        with httpx.Client(timeout=45.0, headers=_alpaca_headers()) as client:
            pages = 0
            while pages < max_pages:
                pages += 1
                resp = client.get(url, params=params)
                resp.raise_for_status()
                payload = resp.json()
                sym_bars = (payload.get("bars") or {}).get(symbol.upper()) or []
                for bar in sym_bars:
                    t = _parse_alpaca_bar_time(bar.get("t"))
                    if not t or t < from_ts or t > to_ts:
                        continue
                    all_bars.append({
                        "time": t,
                        "open": float(bar.get("o") or 0),
                        "high": float(bar.get("h") or 0),
                        "low": float(bar.get("l") or 0),
                        "close": float(bar.get("c") or 0),
                        "volume": float(bar.get("v") or 0),
                    })
                token = payload.get("next_page_token")
                if not token:
                    break
                params = {
                    "symbols": symbol.upper(),
                    "timeframe": "1Min",
                    "start": start,
                    "end": end,
                    "limit": 10000,
                    "adjustment": "split",
                    "sort": "asc",
                    "page_token": token,
                }
                time.sleep(0.05)
    except Exception as exc:
        logger.warning("Alpaca 1m fetch failed for %s: %s", symbol, exc)
        return []

    return _rows_to_db_format(symbol, all_bars, _SOURCE_ALPACA)


def _rows_to_db_format(symbol: str, candles: list[dict], source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bar in candles or []:
        t = bar.get("time")
        if t is None:
            continue
        rows.append({
            "symbol": symbol,
            "time": align_bar_time(int(t)),
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": float(bar.get("volume") or 0),
            "source": source,
        })
    return rows


def fetch_massive_1m_bars(
    symbol: str,
    from_ts: int,
    to_ts: int,
    *,
    symbol_info: dict | None = None,
) -> list[dict[str, Any]]:
    """Fetch 1m bars from Massive/Polygon REST with pagination."""
    if not MASSIVE_API_KEY:
        return []

    from_d = datetime.fromtimestamp(from_ts, tz=timezone.utc).date().isoformat()
    to_d = datetime.fromtimestamp(to_ts, tz=timezone.utc).date().isoformat()
    ticker = terminal_to_massive_rest_ticker(symbol, symbol_info or SYMBOLS.get(symbol))
    base_url = (
        f"{MASSIVE_REST_URL.rstrip('/')}/v2/aggs/ticker/{ticker}/range/"
        f"1/minute/{from_d}/{to_d}"
    )

    all_aggs: list[dict] = []
    url: str | None = base_url
    params: dict[str, Any] | None = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000,
        "apiKey": MASSIVE_API_KEY,
    }
    max_pages = 20

    try:
        with httpx.Client(timeout=45.0) as client:
            pages = 0
            while url and pages < max_pages:
                pages += 1
                resp = client.get(url, params=params)
                resp.raise_for_status()
                payload = resp.json()
                chunk = payload.get("results") or []
                if isinstance(chunk, list):
                    all_aggs.extend(chunk)
                next_url = payload.get("next_url")
                if next_url:
                    parsed = urlparse(next_url)
                    qs = parse_qs(parsed.query)
                    if "apiKey" not in qs and MASSIVE_API_KEY:
                        sep = "&" if parsed.query else ""
                        url = f"{next_url}{sep}apiKey={MASSIVE_API_KEY}"
                    else:
                        url = next_url
                    params = None
                else:
                    url = None
            if pages >= max_pages and url:
                logger.warning(
                    "Massive 1m fetch for %s stopped at pagination cap (%d pages)",
                    symbol,
                    max_pages,
                )
    except Exception as exc:
        logger.warning("Massive 1m fetch failed for %s: %s", symbol, exc)
        return []

    candles = aggs_to_candles(all_aggs)
    filtered = [c for c in candles if from_ts <= int(c["time"]) <= to_ts]
    return _rows_to_db_format(symbol, filtered, _SOURCE_MASSIVE)


def fetch_binance_1m_bars(symbol: str, from_ts: int, to_ts: int) -> list[dict[str, Any]]:
    """Fetch 1m klines from Binance public REST (crypto symbols only)."""
    if not is_crypto_terminal_symbol(symbol):
        return []

    start_ms = int(from_ts) * 1000
    end_ms = int(to_ts) * 1000
    all_candles: list[dict] = []
    url = f"{BINANCE_BASE_URL.rstrip('/')}/api/v3/klines"

    try:
        with httpx.Client(timeout=30.0) as client:
            while start_ms < end_ms:
                resp = client.get(
                    url,
                    params={
                        "symbol": symbol.upper(),
                        "interval": "1m",
                        "startTime": start_ms,
                        "endTime": end_ms,
                        "limit": 1000,
                    },
                )
                resp.raise_for_status()
                rows = resp.json()
                if not rows:
                    break
                for row in rows:
                    open_ms = int(row[0])
                    t = align_bar_time(open_ms // 1000)
                    if t < from_ts or t > to_ts:
                        continue
                    all_candles.append({
                        "time": t,
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]),
                    })
                start_ms = int(rows[-1][0]) + 60_000
                if len(rows) < 1000:
                    break
                time.sleep(0.05)
    except Exception as exc:
        logger.warning("Binance 1m fetch failed for %s: %s", symbol, exc)
        return []

    return _rows_to_db_format(symbol, all_candles, _SOURCE_BINANCE)


def resolve_broker_source() -> str:
    mode = (TERMINAL_MODE or "SIMULATED").upper()
    if mode == "LIVE_BINANCE":
        return "binance"
    if MASSIVE_API_KEY:
        return "massive"
    if mode == "LIVE_ALPACA" and ALPACA_API_KEY and ALPACA_SECRET_KEY:
        return "alpaca"
    if ALPACA_API_KEY and ALPACA_SECRET_KEY:
        return "alpaca"
    return "none"


def fetch_broker_1m_bars(
    symbol: str,
    from_ts: int,
    to_ts: int,
    *,
    symbol_info: dict | None = None,
    prefer: str | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch 1m bars using the best available provider for the symbol/mode.
    """
    if to_ts <= from_ts:
        return []

    source = (prefer or resolve_broker_source()).lower()

    if source == "binance" or (
        source == "none" and is_crypto_terminal_symbol(symbol) and TERMINAL_MODE == "LIVE_BINANCE"
    ):
        rows = fetch_binance_1m_bars(symbol, from_ts, to_ts)
        if rows:
            return rows

    if source == "alpaca" or (
        source == "none"
        and not is_crypto_terminal_symbol(symbol)
        and TERMINAL_MODE == "LIVE_ALPACA"
        and ALPACA_API_KEY
        and ALPACA_SECRET_KEY
    ):
        rows = fetch_alpaca_1m_bars(symbol, from_ts, to_ts)
        if rows:
            return rows

    if MASSIVE_API_KEY or source == "massive":
        rows = fetch_massive_1m_bars(symbol, from_ts, to_ts, symbol_info=symbol_info)
        if rows:
            return rows

    if is_crypto_terminal_symbol(symbol):
        return fetch_binance_1m_bars(symbol, from_ts, to_ts)

    if ALPACA_API_KEY and ALPACA_SECRET_KEY and not is_crypto_terminal_symbol(symbol):
        return fetch_alpaca_1m_bars(symbol, from_ts, to_ts)

    return []


def chunk_date_ranges(from_ts: int, to_ts: int, *, chunk_days: int = 30) -> list[tuple[int, int]]:
    """Split a unix range into calendar chunks for rate-limited broker APIs."""
    if to_ts <= from_ts:
        return []
    chunks: list[tuple[int, int]] = []
    cursor = datetime.fromtimestamp(from_ts, tz=timezone.utc)
    end = datetime.fromtimestamp(to_ts, tz=timezone.utc)
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=chunk_days), end)
        chunks.append((int(cursor.timestamp()), int(chunk_end.timestamp())))
        cursor = chunk_end
    return chunks
