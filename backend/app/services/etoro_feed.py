"""Live market-data feed backed by the eToro Public API.

eToro is the only data source wired into this terminal that covers BOTH
equities and crypto through a single API, so it can serve the full merged
symbol pool. eToro exposes no public market-data WebSocket, so this feed
polls `/market-data/instruments/rates` on a fixed interval and aggregates
the live bid/ask into 1-minute candles.

Conventions enforced here come from the eToro plugin's `etoro-api-conventions`
rule and `resolving-etoro-instruments` skill:
  * Auth is EITHER a Bearer token OR an x-api-key/x-user-key pair — never both.
  * Every request carries a freshly generated `x-request-id` (UUID v4).
  * Comma-separated id lists use a literal `,` (URLSearchParams would encode it).
  * Retry strategy is keyed off the HTTP error class: 429/5xx back off and retry
    at the same payload size; 413/414 shrink the batch; 401 is a dead/auth error.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Awaitable, Callable, Dict, List, Optional

import requests

from app.config import (
    ETORO_ACCESS_TOKEN,
    ETORO_API_BASE,
    ETORO_API_KEY,
    ETORO_POLL_INTERVAL,
    ETORO_USER_KEY,
    SYMBOLS,
)
from app.api.outbound import publish_market_update
from app.services.base_feed import BaseFeedService
from app.services.feeds.bar_close import BarCloseEmitter

logger = logging.getLogger(__name__)

# Resolved symbol -> instrumentId mappings are persisted here so we don't
# re-resolve on every restart (eToro instrument ids are stable).
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
RESOLVE_CACHE_PATH = os.path.join(DATA_DIR, "etoro_instrument_ids.json")

# 413/414 batch-size ladder for the rates endpoint (payload-too-large recovery).
BATCH_SIZE_LADDER = [50, 25, 10]
MAX_CANDLES = 500


class EtoroFeedService(BaseFeedService):
    def __init__(self):
        self._symbols = dict(SYMBOLS)
        self.candles: Dict[str, List[dict]] = {}
        self.order_books: Dict[str, dict] = {}
        self.broadcast_callback: Optional[Callable[[dict], Awaitable[None]]] = None
        self.poll_interval = ETORO_POLL_INTERVAL
        self.active = False
        self.poll_task: Optional[asyncio.Task] = None
        self._bar_close = BarCloseEmitter()

        # symbol -> instrumentId and the reverse lookup for parsing responses
        self._instrument_ids: Dict[str, int] = {}
        self._id_to_symbol: Dict[int, str] = {}

        self._session = requests.Session()

        for sym, info in self._symbols.items():
            self.candles[sym] = self._seed_candles(sym, info["price"])
            self.order_books[sym] = self._synthetic_book(sym, info["price"])

    # ------------------------------------------------------------------ #
    # BaseFeedService interface
    # ------------------------------------------------------------------ #
    @property
    def symbols(self) -> List[str]:
        return list(self._symbols.keys())

    def register_broadcast_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        self.broadcast_callback = callback

    def register_bar_close_callback(self, callback) -> None:
        self._bar_close.register(callback)

    def get_candles(self, symbol: str) -> List[dict]:
        return self.candles.get(symbol, [])

    def get_market_data(self, symbol: str) -> dict:
        if symbol not in self._symbols:
            return {}
        info = self._symbols[symbol]
        active = self.candles.get(symbol, [])
        latest = active[-1] if active else {}
        first_close = active[0]["close"] if active else info["price"]
        return {
            "symbol": symbol,
            "price": info["price"],
            "change_24h": round((info["price"] - first_close) / first_close * 100, 2) if first_close else 0.0,
            "volume_24h": sum(c["volume"] for c in active) if active else 0.0,
            "high_24h": max(c["high"] for c in active) if active else info["price"],
            "low_24h": min(c["low"] for c in active) if active else info["price"],
            "orderbook": self.order_books.get(symbol, self._synthetic_book(symbol, info["price"])),
            "candle": latest,
        }

    async def start(self) -> None:
        self.active = True
        if not self._has_credentials():
            logger.warning(
                "eToro feed started WITHOUT credentials. Set ETORO_ACCESS_TOKEN "
                "(SSO Bearer) or ETORO_API_KEY + ETORO_USER_KEY to stream live "
                "rates. Serving seeded fallback candles until then."
            )
            return
        self.poll_task = asyncio.create_task(self._poll_loop())
        logger.info("eToro feed poll task started.")

    async def stop(self) -> None:
        self.active = False
        if self.poll_task:
            self.poll_task.cancel()
            try:
                await self.poll_task
            except asyncio.CancelledError:
                pass
        logger.info("eToro feed stopped.")

    async def subscribe(self, symbol: str) -> None:
        pass

    async def unsubscribe(self, symbol: str) -> None:
        pass

    # ------------------------------------------------------------------ #
    # eToro HTTP plumbing (see etoro-api-conventions rule)
    # ------------------------------------------------------------------ #
    def _has_credentials(self) -> bool:
        return bool(ETORO_ACCESS_TOKEN) or bool(ETORO_API_KEY and ETORO_USER_KEY)

    def _headers(self) -> Dict[str, str]:
        # x-request-id is generated PER REQUEST for tracing on eToro's side.
        headers = {
            "Accept": "application/json",
            "x-request-id": str(uuid.uuid4()),
        }
        # Bearer OR API-key pair, never both — sending both is rejected.
        if ETORO_ACCESS_TOKEN:
            headers["Authorization"] = f"Bearer {ETORO_ACCESS_TOKEN}"
        elif ETORO_API_KEY and ETORO_USER_KEY:
            headers["x-api-key"] = ETORO_API_KEY
            headers["x-user-key"] = ETORO_USER_KEY
        return headers

    def _get(self, path: str, params: Optional[Dict[str, str]] = None) -> requests.Response:
        url = f"{ETORO_API_BASE}{path}"
        # NOTE: pass pre-serialized comma lists in `path`; requests would
        # percent-encode commas if placed in params, which eToro rejects.
        return self._session.get(url, headers=self._headers(), params=params, timeout=10)

    # ------------------------------------------------------------------ #
    # Step 1 — resolve symbols -> instrumentId (persisted)
    # ------------------------------------------------------------------ #
    def _load_resolve_cache(self) -> Dict[str, int]:
        try:
            if os.path.exists(RESOLVE_CACHE_PATH):
                with open(RESOLVE_CACHE_PATH, "r") as f:
                    return {k: int(v) for k, v in json.load(f).items()}
        except Exception as e:
            logger.warning(f"Could not read eToro id cache: {e}")
        return {}

    def _save_resolve_cache(self) -> None:
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(RESOLVE_CACHE_PATH, "w") as f:
                json.dump(self._instrument_ids, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not persist eToro id cache: {e}")

    def _resolve_instrument_ids(self) -> None:
        """Resolve each app symbol to an eToro instrumentId, caching results.

        The eToro listing symbol is the asset code, e.g. BTCUSDT -> "BTC",
        AAPL -> "AAPL" (taken from the config `asset` field).
        """
        cached = self._load_resolve_cache()
        for sym, info in self._symbols.items():
            if sym in cached:
                self._instrument_ids[sym] = cached[sym]
                continue
            etoro_symbol = info.get("asset", sym)
            try:
                resp = self._get(
                    "/market-data/search",
                    params={"internalSymbolFull": etoro_symbol},
                )
                if resp.status_code != 200:
                    logger.warning(
                        f"eToro search for {etoro_symbol} returned {resp.status_code}"
                    )
                    continue
                # /market-data/search uses lowercase `instrumentId`.
                items = resp.json().get("items", [])
                exact = next(
                    (it for it in items if it.get("symbolFull") == etoro_symbol),
                    None,
                )
                chosen = exact or (items[0] if items else None)
                if chosen and chosen.get("instrumentId") is not None:
                    self._instrument_ids[sym] = int(chosen["instrumentId"])
            except Exception as e:
                logger.warning(f"eToro resolve failed for {etoro_symbol}: {e}")

        self._id_to_symbol = {v: k for k, v in self._instrument_ids.items()}
        self._save_resolve_cache()
        logger.info(
            f"Resolved {len(self._instrument_ids)}/{len(self._symbols)} eToro instrument ids."
        )

    # ------------------------------------------------------------------ #
    # Step 2 — poll live rates and aggregate into candles
    # ------------------------------------------------------------------ #
    async def _poll_loop(self) -> None:
        # Resolution does blocking I/O; keep it off the event loop.
        await asyncio.to_thread(self._resolve_instrument_ids)
        if not self._instrument_ids:
            logger.error("No eToro instruments resolved; rates polling disabled.")
            return

        backoff = self.poll_interval
        while self.active:
            try:
                rates = await asyncio.to_thread(self._fetch_all_rates)
                updates = self._apply_rates(rates)
                if updates and self.broadcast_callback:
                    await publish_market_update(self.broadcast_callback, updates)
                backoff = self.poll_interval  # success resets backoff
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except _RateLimited:
                backoff = min(backoff * 2, 60)
                logger.warning(f"eToro rate-limited (429); backing off {backoff:.1f}s.")
                await asyncio.sleep(backoff)
            except _AuthDead:
                logger.error(
                    "eToro returned 401 — credentials invalid or session expired. "
                    "Stopping rates polling; reconnect required."
                )
                break
            except Exception as e:
                backoff = min(backoff * 2, 60)
                logger.error(f"eToro rates poll error: {e}; retrying in {backoff:.1f}s.")
                await asyncio.sleep(backoff)

    def _fetch_all_rates(self) -> Dict[int, dict]:
        """Fetch rates for all resolved ids using adaptive 413/414 batching."""
        ids = list(self._instrument_ids.values())
        out: Dict[int, dict] = {}
        batch_size = BATCH_SIZE_LADDER[0]
        i = 0
        while i < len(ids):
            chunk = ids[i : i + batch_size]
            # Literal comma join — DO NOT let a URL builder encode it as %2C.
            joined = ",".join(str(x) for x in chunk)
            resp = self._get(f"/market-data/instruments/rates?instrumentIds={joined}")

            if resp.status_code == 200:
                for entry in self._extract_rate_entries(resp.json()):
                    iid = entry.get("instrumentId") or entry.get("instrumentID")
                    if iid is not None:
                        out[int(iid)] = entry
                i += batch_size
            elif resp.status_code in (413, 414):
                # Payload too large: shrink the batch and retry the SAME chunk.
                idx = BATCH_SIZE_LADDER.index(batch_size) if batch_size in BATCH_SIZE_LADDER else 0
                if idx + 1 < len(BATCH_SIZE_LADDER):
                    batch_size = BATCH_SIZE_LADDER[idx + 1]
                    continue
                logger.warning(f"eToro rates batch failed at min size: {chunk}")
                i += batch_size
            elif resp.status_code == 429:
                raise _RateLimited()
            elif resp.status_code == 401:
                raise _AuthDead()
            else:
                resp.raise_for_status()
        return out

    @staticmethod
    def _extract_rate_entries(payload) -> List[dict]:
        # eToro responses have varied envelopes across versions; accept the
        # common shapes (bare list, or wrapped under rates/prices/data).
        if isinstance(payload, list):
            return payload
        for key in ("rates", "prices", "data", "instrumentRates"):
            val = payload.get(key) if isinstance(payload, dict) else None
            if isinstance(val, list):
                return val
        return []

    def _apply_rates(self, rates: Dict[int, dict]) -> Dict[str, dict]:
        updates: Dict[str, dict] = {}
        now_minute = int(time.time() // 60) * 60
        for iid, entry in rates.items():
            symbol = self._id_to_symbol.get(iid)
            if not symbol:
                continue
            price = self._price_from_entry(entry)
            if price is None:
                continue

            info = self._symbols[symbol]
            decimals = info["decimals"]
            price = round(price, decimals)
            info["price"] = price

            try:
                from app.config import ARCHIVE_TICKS_ENABLED
                if ARCHIVE_TICKS_ENABLED:
                    from app.services.archive.tick_writer import record_tick
                    record_tick(symbol, price)
            except Exception:
                pass

            # Real bid/ask if eToro provided them; otherwise a tight synthetic book.
            bid = entry.get("bid")
            ask = entry.get("ask")
            if bid is not None and ask is not None:
                self.order_books[symbol] = self._book_from_quote(symbol, float(bid), float(ask))
            else:
                self.order_books[symbol] = self._synthetic_book(symbol, price)

            self._aggregate_candle(symbol, price, now_minute)
            updates[symbol] = self.get_market_data(symbol)
        return updates

    @staticmethod
    def _price_from_entry(entry: dict) -> Optional[float]:
        for key in ("last", "lastExecution", "price", "close"):
            if entry.get(key) is not None:
                try:
                    return float(entry[key])
                except (TypeError, ValueError):
                    pass
        bid, ask = entry.get("bid"), entry.get("ask")
        if bid is not None and ask is not None:
            try:
                return (float(bid) + float(ask)) / 2.0
            except (TypeError, ValueError):
                return None
        return None

    def _aggregate_candle(self, symbol: str, price: float, minute: int) -> None:
        candles = self.candles[symbol]
        decimals = self._symbols[symbol]["decimals"]
        if candles and candles[-1]["time"] == minute:
            c = candles[-1]
            c["high"] = round(max(c["high"], price), decimals)
            c["low"] = round(min(c["low"], price), decimals)
            c["close"] = price
            c["volume"] = round(c["volume"] + 1, 2)
        else:
            candles.append(
                {"time": minute, "open": price, "high": price, "low": price, "close": price, "volume": 1.0}
            )
            if len(candles) > MAX_CANDLES:
                candles.pop(0)
            self._bar_close.notify(symbol)

    # ------------------------------------------------------------------ #
    # Fallback candle/book generators (used before first live tick)
    # ------------------------------------------------------------------ #
    def _seed_candles(self, symbol: str, price: float, count: int = 100) -> List[dict]:
        import random

        candles = []
        t = int(time.time() // 60) * 60 - (count * 60)
        p = price
        decimals = self._symbols[symbol]["decimals"]
        for _ in range(count):
            ch = p * random.normalvariate(0, 0.001)
            candles.append(
                {
                    "time": t,
                    "open": round(p, decimals),
                    "high": round(p + abs(ch), decimals),
                    "low": round(p - abs(ch), decimals),
                    "close": round(p + ch, decimals),
                    "volume": round(random.uniform(1, 15), 2),
                }
            )
            p += ch
            t += 60
        return candles

    def _book_from_quote(self, symbol: str, bid: float, ask: float) -> dict:
        import random

        decimals = self._symbols[symbol]["decimals"]
        base_qty = 0.2 if "BTC" in symbol else 2.0
        bids, asks = [], []
        for i in range(10):
            step = 0.0002 * (i + 1)
            bids.append([round(bid * (1 - step), decimals), round(base_qty * random.uniform(0.5, 2.0) * (10 - i) / 5, 4)])
            asks.append([round(ask * (1 + step), decimals), round(base_qty * random.uniform(0.5, 2.0) * (10 - i) / 5, 4)])
        return {"bids": bids, "asks": asks}

    def _synthetic_book(self, symbol: str, price: float) -> dict:
        decimals = self._symbols[symbol]["decimals"]
        spread = round(price * 0.0004, decimals) or 10 ** (-decimals)
        return self._book_from_quote(symbol, price - spread / 2, price + spread / 2)


class _RateLimited(Exception):
    """Raised on HTTP 429 so the poll loop can back off without shrinking."""


class _AuthDead(Exception):
    """Raised on HTTP 401 — credentials invalid or SSO session expired."""
