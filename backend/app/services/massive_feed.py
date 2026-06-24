"""Massive.com (formerly Polygon.io) live stocks + crypto feed via WebSocket + REST seed."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import date, timedelta
from typing import Awaitable, Callable, List, Literal

import httpx
import websockets

from app.api.outbound import publish_market_update
from app.config import (
    MASSIVE_API_KEY,
    MASSIVE_CRYPTO_WS_URL,
    MASSIVE_HIST_DAYS,
    MASSIVE_POLL_FALLBACK,
    MASSIVE_POLL_INTERVAL_SEC,
    MASSIVE_QUOTES_ENABLED,
    MASSIVE_REST_URL,
    MASSIVE_SEED_CONCURRENCY,
    MASSIVE_WS_ENABLED,
    MASSIVE_WS_RECONNECT_SEC,
    MASSIVE_WS_URL,
    SYMBOLS,
)
from app.observability.metrics import inc
from app.services.base_feed import BaseFeedService
from app.services.feeds.bar_close import BarCloseEmitter
from app.services.massive_bars import agg_to_candle, aggs_to_candles, aggs_to_candles_native, crypto_agg_to_candle, rest_agg_to_candle
from app.services.massive_bars import timeframe_to_massive_range
from app.services.massive_ht_limits import (
    massive_ht_limit,
    massive_ht_store_cap,
)
from app.services.market.timeframes import normalize_timeframe, timeframe_to_secs
from app.services.massive_symbols import (
    build_pair_to_terminal,
    is_crypto_terminal_symbol,
    terminal_to_massive_rest_ticker,
    terminal_to_massive_ws_pair,
)

logger = logging.getLogger(__name__)

MAX_CANDLES = 1500  # ~25h of 1m bars — enough for rolling 24h watchlist stats
HT_CACHE_TTL_SEC = 300.0
ROLLING_24H_SEC = 86400
MarketKind = Literal["stocks", "crypto"]
FeedMode = Literal["websocket", "poll", "off"]


def rolling_24h_stats(
    candles: list[dict],
    price: float,
    *,
    now: float | None = None,
) -> tuple[float, float, float, float]:
    """Rolling 24h change %, volume, high, low from 1m OHLCV bars."""
    if not candles:
        return 0.0, 0.0, float(price), float(price)

    ts = float(now if now is not None else time.time())
    cutoff = int(ts) - ROLLING_24H_SEC
    window = [c for c in candles if int(c.get("time") or 0) >= cutoff]
    if not window:
        window = candles[-1440:] if len(candles) > 1440 else candles

    ref_open = float(window[0].get("open") or window[0].get("close") or 0)
    change = round((price - ref_open) / ref_open * 100, 2) if ref_open else 0.0
    vol = sum(float(c.get("volume") or 0) for c in window)
    hi = max(max(float(c["high"]) for c in window), float(price))
    lo = min(min(float(c["low"]) for c in window), float(price))
    return change, round(vol, 2), hi, lo


class MassiveFeedService(BaseFeedService):
    """Live US equities (AM/T/Q) and crypto (XA/XT/XQ) from Massive WebSocket + REST poll fallback."""

    def __init__(self) -> None:
        self._symbols = dict(SYMBOLS)
        self._equity_symbols = [s for s in self._symbols if not is_crypto_terminal_symbol(s)]
        self._crypto_symbols = [s for s in self._symbols if is_crypto_terminal_symbol(s)]
        self._pair_to_terminal = build_pair_to_terminal(
            {s: self._symbols[s] for s in self._crypto_symbols}
        )
        self.candles: dict[str, list[dict]] = {sym: [] for sym in self._symbols}
        self._seeded: set[str] = set()
        self.order_books: dict[str, dict] = {}
        self.broadcast_callback: Callable[[dict], Awaitable[None]] | None = None
        self.active = False
        self._stocks_task: asyncio.Task | None = None
        self._crypto_task: asyncio.Task | None = None
        self._seed_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._bar_close = BarCloseEmitter()
        self._stocks_connected = False
        self._crypto_connected = False
        self._last_error: str | None = None
        self._stocks_subscriptions = 0
        self._crypto_subscriptions = 0
        self._reconnect_count = 0
        self._trades_received = 0
        self._bars_received = 0
        self._quotes_received = 0
        self._poll_updates = 0
        self._stocks_ws_give_up = False
        self._crypto_ws_give_up = False
        self._stocks_mode: FeedMode = "off"
        self._crypto_mode: FeedMode = "off"
        self._real_quotes: set[str] = set()
        self._ht_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}

        for sym, info in self._symbols.items():
            self.order_books[sym] = self._synthetic_book(sym, info["price"])

    @property
    def symbols(self) -> List[str]:
        return list(self._symbols.keys())

    @property
    def massive_status(self) -> dict:
        stocks_lag = self._feed_lag_for_symbols(self._equity_symbols)
        crypto_lag = self._feed_lag_for_symbols(self._crypto_symbols)
        return {
            "connected": self._stocks_connected or self._crypto_connected,
            "stocks_connected": self._stocks_connected,
            "crypto_connected": self._crypto_connected,
            "stocks_mode": self._stocks_mode,
            "crypto_mode": self._crypto_mode,
            "poll_fallback": MASSIVE_POLL_FALLBACK and (
                self._stocks_mode == "poll" or self._crypto_mode == "poll"
            ),
            "quotes_enabled": MASSIVE_QUOTES_ENABLED,
            "real_quote_symbols": len(self._real_quotes),
            "subscriptions": self._stocks_subscriptions + self._crypto_subscriptions,
            "stocks_subscriptions": self._stocks_subscriptions,
            "crypto_subscriptions": self._crypto_subscriptions,
            "equity_symbols": len(self._equity_symbols),
            "crypto_symbols": len(self._crypto_symbols),
            "last_error": self._last_error,
            "reconnects": self._reconnect_count,
            "bars_received": self._bars_received,
            "trades_received": self._trades_received,
            "quotes_received": self._quotes_received,
            "poll_updates": self._poll_updates,
            "seeded_symbols": len(self._seeded),
            "stocks_lag_sec": round(stocks_lag, 2) if stocks_lag is not None else None,
            "crypto_lag_sec": round(crypto_lag, 2) if crypto_lag is not None else None,
        }

    def register_broadcast_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        self.broadcast_callback = callback

    def register_bar_close_callback(self, callback) -> None:
        self._bar_close.register(callback)

    def get_candles(self, symbol: str) -> List[dict]:
        return list(self.candles.get(symbol, []))

    @staticmethod
    def _ht_lookback_days(symbol: str, bar_secs: int, cap: int) -> int:
        """Calendar days to request so sort=desc&limit=cap covers enough trading bars."""
        if is_crypto_terminal_symbol(symbol):
            return max(2, int((cap * bar_secs) / 86400) + 3)
        trading_day_secs = 6.5 * 3600
        trading_days_needed = (cap * bar_secs) / trading_day_secs
        return max(5, int(trading_days_needed * 1.5) + 5)

    def fetch_ht_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int | None = None,
        *,
        purpose: str = "chart",
    ) -> list[dict]:
        """Fetch native higher-timeframe OHLCV from Massive REST (cached briefly)."""
        if symbol not in self._symbols:
            return []
        if not MASSIVE_API_KEY:
            return []

        tf = normalize_timeframe(timeframe)
        if tf == "1m":
            return self.get_candles(symbol)

        purpose_key = purpose if purpose in ("chart", "analysis") else "chart"
        cap = massive_ht_limit(tf, purpose=purpose_key, explicit=limit)
        store_cap = massive_ht_store_cap(tf)
        cache_key = (symbol, tf)
        now = time.time()
        cached = self._ht_cache.get(cache_key)
        if cached and cached[0] > now:
            bars = cached[1]
            fetched_for = cached[2] if len(cached) > 2 else len(bars)
            if len(bars) >= cap:
                return bars[-cap:] if len(bars) > cap else bars
            if fetched_for >= cap:
                return bars

        multiplier, timespan = timeframe_to_massive_range(tf)
        bar_secs = timeframe_to_secs(tf)
        fetch_cap = max(cap, store_cap)
        lookback_days = self._ht_lookback_days(symbol, bar_secs, fetch_cap)
        to_d = date.today()
        from_d = to_d - timedelta(days=lookback_days)

        ticker = terminal_to_massive_rest_ticker(symbol, self._symbols.get(symbol))
        url = (
            f"{MASSIVE_REST_URL.rstrip('/')}/v2/aggs/ticker/{ticker}/range/"
            f"{multiplier}/{timespan}/{from_d.isoformat()}/{to_d.isoformat()}"
        )
        params = {
            "adjusted": "true",
            "sort": "desc",
            "limit": 50000,
            "apiKey": MASSIVE_API_KEY,
        }
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            logger.warning("Massive HT fetch failed for %s %s: %s", symbol, tf, exc)
            return []

        results = payload.get("results") or []
        candles = aggs_to_candles_native(results if isinstance(results, list) else [])
        candles.reverse()
        if len(candles) > store_cap:
            candles = candles[-store_cap:]
        self._ht_cache[cache_key] = (now + HT_CACHE_TTL_SEC, candles, fetch_cap)
        return candles[-cap:] if len(candles) > cap else candles

    def sync_bar(self, symbol: str, candles: list) -> None:
        if not candles:
            return
        sym = symbol.upper()
        if sym not in self._symbols:
            return
        buf = list(self.candles.get(sym, []))
        for bar in candles:
            if isinstance(bar, dict) and bar.get("time"):
                if buf and buf[-1].get("time") == bar["time"]:
                    buf[-1] = bar
                elif not buf or buf[-1].get("time", 0) < bar["time"]:
                    buf.append(bar)
        if len(buf) > MAX_CANDLES:
            buf = buf[-MAX_CANDLES:]
        self.candles[sym] = buf
        if buf:
            self._symbols[sym]["price"] = float(buf[-1]["close"])

    def feed_lag_sec(self) -> float | None:
        return self._feed_lag_for_symbols(self.symbols)

    def _feed_lag_for_symbols(self, symbols: list[str]) -> float | None:
        latest: int | None = None
        for sym in symbols:
            candles = self.candles.get(sym) or []
            if not candles:
                continue
            bar_time = int(candles[-1].get("time") or 0)
            if bar_time and (latest is None or bar_time > latest):
                latest = bar_time
        if latest is None:
            return None
        return max(0.0, time.time() - float(latest))

    def get_market_data(self, symbol: str) -> dict:
        if symbol not in self._symbols:
            return {}
        info = self._symbols[symbol]
        active_candles = self.candles.get(symbol, [])
        price = float(info["price"])
        seeded = symbol in self._seeded and bool(active_candles)
        latest = self._live_candle_snapshot(symbol) if seeded else {}
        change, vol, hi, lo = rolling_24h_stats(active_candles, price) if seeded else (0.0, 0.0, price, price)
        return {
            "symbol": symbol,
            "price": price,
            "change_24h": change,
            "volume_24h": round(vol, 2),
            "high_24h": hi,
            "low_24h": lo,
            "orderbook": self.order_books.get(symbol, self._synthetic_book(symbol, price)),
            "candle": latest,
        }

    async def start(self) -> None:
        if not MASSIVE_API_KEY:
            logger.warning("MASSIVE_API_KEY missing - feed will not connect.")
            return
        self.active = True
        self._seed_task = asyncio.create_task(self._seed_history())

        if self._equity_symbols:
            self._stocks_mode = "poll" if not MASSIVE_WS_ENABLED else "websocket"
        if self._crypto_symbols:
            self._crypto_mode = "poll" if not MASSIVE_WS_ENABLED else "websocket"

        if MASSIVE_WS_ENABLED:
            if self._equity_symbols and self._stocks_mode == "websocket":
                self._stocks_task = asyncio.create_task(self._ws_loop("stocks", MASSIVE_WS_URL))
            if self._crypto_symbols and self._crypto_mode == "websocket":
                self._crypto_task = asyncio.create_task(self._ws_loop("crypto", MASSIVE_CRYPTO_WS_URL))

        if self._needs_poll():
            self._poll_task = asyncio.create_task(self._poll_loop())

        logger.info(
            "Massive feed starting (%d equities [%s], %d crypto [%s]; quotes=%s)",
            len(self._equity_symbols),
            self._stocks_mode,
            len(self._crypto_symbols),
            self._crypto_mode,
            MASSIVE_QUOTES_ENABLED,
        )

    async def stop(self) -> None:
        self.active = False
        self._stocks_connected = False
        self._crypto_connected = False
        for task in (self._stocks_task, self._crypto_task, self._seed_task, self._poll_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("Massive feed stopped.")

    async def subscribe(self, symbol: str) -> None:
        pass

    async def unsubscribe(self, symbol: str) -> None:
        pass

    def _needs_poll(self) -> bool:
        return self._stocks_mode == "poll" or self._crypto_mode == "poll"

    def _channels_per_symbol(self, market: MarketKind) -> int:
        n = 2  # AM/T or XA/XT
        if MASSIVE_QUOTES_ENABLED:
            n += 1  # Q or XQ
        return n

    async def _seed_history(self) -> None:
        if not MASSIVE_API_KEY:
            return
        to_d = date.today()
        from_d = to_d - timedelta(days=max(1, int(MASSIVE_HIST_DAYS)))
        from_str = from_d.isoformat()
        to_str = to_d.isoformat()
        sem = asyncio.Semaphore(max(1, int(MASSIVE_SEED_CONCURRENCY)))

        async def _seed_one(symbol: str) -> None:
            async with sem:
                if not self.active:
                    return
                try:
                    bars = await asyncio.to_thread(
                        self._fetch_rest_aggs, symbol, from_str, to_str
                    )
                    if bars:
                        merged = aggs_to_candles(bars)[-MAX_CANDLES:]
                        self.candles[symbol] = merged
                        last = merged[-1]
                        self._symbols[symbol]["price"] = float(last["close"])
                        self.order_books[symbol] = self._synthetic_book(
                            symbol, float(last["close"])
                        )
                        self._seeded.add(symbol)
                        logger.info("Massive seeded %s (%d bars)", symbol, len(merged))
                except Exception as exc:
                    logger.warning("Massive seed failed for %s: %s", symbol, exc)

        await asyncio.gather(*(_seed_one(sym) for sym in self.symbols if self.active))

    def _fetch_rest_aggs(self, symbol: str, from_str: str, to_str: str) -> list[dict]:
        ticker = terminal_to_massive_rest_ticker(symbol, self._symbols.get(symbol))
        url = (
            f"{MASSIVE_REST_URL.rstrip('/')}/v2/aggs/ticker/{ticker}/range/"
            f"1/minute/{from_str}/{to_str}"
        )
        params = {
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
            "apiKey": MASSIVE_API_KEY,
        }
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
        results = payload.get("results") or []
        return results if isinstance(results, list) else []

    def _fetch_rest_latest_bar(self, symbol: str) -> dict | None:
        to_d = date.today()
        from_d = to_d - timedelta(days=1)
        bars = self._fetch_rest_aggs(symbol, from_d.isoformat(), to_d.isoformat())
        return bars[-1] if bars else None

    def _fetch_rest_nbbo(self, symbol: str) -> dict | None:
        ticker = terminal_to_massive_rest_ticker(symbol, self._symbols.get(symbol))
        url = f"{MASSIVE_REST_URL.rstrip('/')}/v2/last/nbbo/{ticker}"
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, params={"apiKey": MASSIVE_API_KEY})
            if resp.status_code == 403:
                return None
            resp.raise_for_status()
            payload = resp.json()
        results = payload.get("results")
        return results if isinstance(results, dict) else None

    def _fetch_rest_crypto_quote(self, symbol: str) -> dict | None:
        info = self._symbols.get(symbol) or {}
        asset = info.get("asset") or symbol.replace("USDT", "")
        url = f"{MASSIVE_REST_URL.rstrip('/')}/v1/last/crypto/{asset}/USD"
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, params={"apiKey": MASSIVE_API_KEY})
            if resp.status_code in (403, 404):
                return None
            resp.raise_for_status()
            payload = resp.json()
        last = payload.get("last") or payload.get("results")
        return last if isinstance(last, dict) else None

    async def _poll_loop(self) -> None:
        while self.active:
            if not self._needs_poll():
                await asyncio.sleep(5.0)
                continue
            updates: dict[str, dict] = {}
            symbols = []
            if self._stocks_mode == "poll":
                symbols.extend(self._equity_symbols)
            if self._crypto_mode == "poll":
                symbols.extend(self._crypto_symbols)
            for symbol in symbols:
                if not self.active:
                    break
                try:
                    bar = await asyncio.to_thread(self._fetch_rest_latest_bar, symbol)
                    if bar:
                        candle = rest_agg_to_candle(bar)
                        self._apply_minute_bar(symbol, candle, synthetic_book=not MASSIVE_QUOTES_ENABLED)
                    if MASSIVE_QUOTES_ENABLED:
                        if is_crypto_terminal_symbol(symbol):
                            quote = await asyncio.to_thread(self._fetch_rest_crypto_quote, symbol)
                            if quote:
                                bp = float(quote.get("bid") or quote.get("bp") or 0)
                                ap = float(quote.get("ask") or quote.get("ap") or 0)
                                bs = float(quote.get("bid_size") or quote.get("bs") or 1)
                                a_s = float(quote.get("ask_size") or quote.get("as") or 1)
                                if bp > 0 and ap > 0:
                                    self._apply_nbbo(symbol, bp, bs, ap, a_s)
                        else:
                            nbbo = await asyncio.to_thread(self._fetch_rest_nbbo, symbol)
                            if nbbo:
                                bp = float(nbbo.get("p") or nbbo.get("bp") or 0)
                                ap = float(nbbo.get("P") or nbbo.get("ap") or 0)
                                bs = float(nbbo.get("s") or nbbo.get("bs") or 1)
                                a_s = float(nbbo.get("S") or nbbo.get("as") or 1)
                                if bp > 0 and ap > 0:
                                    self._apply_nbbo(symbol, bp, bs, ap, a_s)
                    self._poll_updates += 1
                    inc("massive_poll_updates_total")
                    updates[symbol] = self.get_market_data(symbol)
                except Exception as exc:
                    logger.debug("Massive poll failed for %s: %s", symbol, exc)
                await asyncio.sleep(0.15)
            if updates and self.broadcast_callback:
                await publish_market_update(self.broadcast_callback, updates)
            await asyncio.sleep(max(5.0, MASSIVE_POLL_INTERVAL_SEC))

    def _subscription_tiers(self, market: MarketKind) -> list[tuple[str, str]]:
        """Subscribe tiers separately — bundling AM+T+Q fails when T/Q are not on plan."""
        if market == "crypto":
            parts: list[str] = []
            for sym in self._crypto_symbols:
                pair = terminal_to_massive_ws_pair(sym, self._symbols[sym])
                parts.append(f"XA.{pair}")
                parts.append(f"XT.{pair}")
                if MASSIVE_QUOTES_ENABLED:
                    parts.append(f"XQ.{pair}")
            return [("all", ",".join(parts))]

        tiers: list[tuple[str, str]] = []
        bar_parts = [f"AM.{sym}" for sym in self._equity_symbols]
        if bar_parts:
            tiers.append(("bars", ",".join(bar_parts)))
        trade_parts = [f"T.{sym}" for sym in self._equity_symbols]
        if trade_parts:
            tiers.append(("trades", ",".join(trade_parts)))
        if MASSIVE_QUOTES_ENABLED:
            quote_parts = [f"Q.{sym}" for sym in self._equity_symbols]
            if quote_parts:
                tiers.append(("quotes", ",".join(quote_parts)))
        return tiers

    def _subscription_params(self, market: MarketKind) -> str:
        """Legacy single-string params (crypto / tests). Stocks use _subscription_tiers."""
        return ",".join(params for _, params in self._subscription_tiers(market))

    async def _ws_loop(self, market: MarketKind, ws_url: str) -> None:
        give_up_attr = f"_{market}_ws_give_up"
        while self.active and not getattr(self, give_up_attr):
            try:
                await self._connect_and_stream(market, ws_url)
            except asyncio.CancelledError:
                break
            except RuntimeError as exc:
                if MASSIVE_POLL_FALLBACK and getattr(self, give_up_attr):
                    mode_attr = f"_{market}_mode"
                    setattr(self, mode_attr, "poll")
                    if not self._poll_task or self._poll_task.done():
                        self._poll_task = asyncio.create_task(self._poll_loop())
                    logger.warning("Massive %s WS unavailable — REST poll fallback active", market)
                    break
                self._last_error = str(exc)
                if market == "stocks":
                    self._stocks_connected = False
                else:
                    self._crypto_connected = False
                self._reconnect_count += 1
                inc("massive_reconnects_total", labels={"market": market})
                logger.error(
                    "Massive %s feed error: %s - reconnecting in %ss",
                    market,
                    exc,
                    MASSIVE_WS_RECONNECT_SEC,
                )
                await asyncio.sleep(MASSIVE_WS_RECONNECT_SEC)
            except Exception as exc:
                self._last_error = str(exc)
                if market == "stocks":
                    self._stocks_connected = False
                else:
                    self._crypto_connected = False
                self._reconnect_count += 1
                inc("massive_reconnects_total", labels={"market": market})
                logger.error(
                    "Massive %s feed error: %s - reconnecting in %ss",
                    market,
                    exc,
                    MASSIVE_WS_RECONNECT_SEC,
                )
                await asyncio.sleep(MASSIVE_WS_RECONNECT_SEC)

    def _resolve_terminal_symbol(self, msg: dict, market: MarketKind) -> str | None:
        if market == "stocks":
            sym = str(msg.get("sym") or msg.get("symbol") or "").upper()
            return sym if sym in self._symbols else None
        pair = str(msg.get("pair") or "")
        return self._pair_to_terminal.get(pair) or self._pair_to_terminal.get(pair.upper())

    def _activate_poll_fallback(self, market: MarketKind, reason: str) -> None:
        setattr(self, f"_{market}_ws_give_up", True)
        setattr(self, f"_{market}_mode", "poll")
        if market == "stocks":
            self._stocks_connected = False
        else:
            self._crypto_connected = False
        self._last_error = reason
        inc("massive_stream_errors_total", labels={"code": "auth_failed", "market": market})

    async def _send_subscription_tiers(self, ws, market: MarketKind) -> int:
        tiers = self._subscription_tiers(market)
        channels = 0
        for tier_name, params in tiers:
            if not params:
                continue
            await ws.send(json.dumps({"action": "subscribe", "params": params}))
            sym_count = (
                len(self._equity_symbols) if market == "stocks" else len(self._crypto_symbols)
            )
            if tier_name == "all":
                channels += sym_count * self._channels_per_symbol(market)
            elif tier_name == "bars":
                channels += sym_count
            elif tier_name in ("trades", "quotes"):
                channels += sym_count
            if market == "stocks" and tier_name == "bars":
                await asyncio.sleep(0.05)
        return channels

    async def _connect_and_stream(self, market: MarketKind, ws_url: str) -> None:
        async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
            authed = False
            subscribed = False
            bars_subscribed = False
            optional_denied_logged: set[str] = set()

            while self.active:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                except asyncio.TimeoutError:
                    continue

                try:
                    msgs = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msgs, list):
                    msgs = [msgs]

                updates: dict[str, dict] = {}
                for msg in msgs:
                    if not isinstance(msg, dict):
                        continue
                    ev = msg.get("ev") or msg.get("event")
                    status = msg.get("status")

                    if ev == "status":
                        if status == "connected":
                            await ws.send(json.dumps({"action": "auth", "params": MASSIVE_API_KEY}))
                        elif status == "auth_success":
                            authed = True
                            self._last_error = None
                            if market == "stocks":
                                self._stocks_connected = True
                            else:
                                self._crypto_connected = True
                            sub_count = await self._send_subscription_tiers(ws, market)
                            if market == "stocks":
                                self._stocks_subscriptions = sub_count
                                bars_subscribed = True
                            else:
                                self._crypto_subscriptions = sub_count
                            subscribed = True
                            logger.info(
                                "Massive %s WS authenticated - %d channels on %s",
                                market,
                                sub_count,
                                ws_url,
                            )
                        elif status in ("auth_failed", "error"):
                            reason = msg.get("message") or status
                            if status == "auth_failed" or not authed:
                                if MASSIVE_POLL_FALLBACK:
                                    self._activate_poll_fallback(market, f"{market}: {reason}")
                                    raise RuntimeError(f"{market}: {reason}")
                                inc(
                                    "massive_stream_errors_total",
                                    labels={"code": status or "auth", "market": market},
                                )
                                raise RuntimeError(f"{market}: {reason}")
                            if authed and subscribed and bars_subscribed:
                                deny_key = f"{market}:{reason}"
                                if deny_key not in optional_denied_logged:
                                    optional_denied_logged.add(deny_key)
                                    logger.warning(
                                        "Massive %s WS optional channel denied (continuing): %s",
                                        market,
                                        reason,
                                    )
                                inc(
                                    "massive_stream_errors_total",
                                    labels={"code": "sub_denied", "market": market},
                                )
                                continue
                            if MASSIVE_POLL_FALLBACK:
                                self._activate_poll_fallback(market, f"{market}: {reason}")
                                raise RuntimeError(f"{market}: {reason}")
                            inc(
                                "massive_stream_errors_total",
                                labels={"code": status or "auth", "market": market},
                            )
                            raise RuntimeError(f"{market}: {reason}")
                        continue

                    if not authed or not subscribed:
                        continue

                    sym = self._resolve_terminal_symbol(msg, market)
                    if not sym:
                        continue

                    bar_events = ("AM",) if market == "stocks" else ("XA",)
                    trade_events = ("T",) if market == "stocks" else ("XT",)
                    quote_events = ("Q",) if market == "stocks" else ("XQ",)

                    if ev in bar_events:
                        candle_fn = crypto_agg_to_candle if market == "crypto" else agg_to_candle
                        self._apply_minute_bar(
                            sym,
                            candle_fn(msg),
                            synthetic_book=not MASSIVE_QUOTES_ENABLED or sym not in self._real_quotes,
                        )
                        self._bars_received += 1
                        inc("massive_bars_received_total", labels={"market": market})
                        updates[sym] = self.get_market_data(sym)
                    elif ev in trade_events:
                        price = float(msg.get("p") or 0)
                        if price <= 0:
                            continue
                        self._symbols[sym]["price"] = price
                        self._patch_forming_candle(sym, price)
                        if sym not in self._real_quotes:
                            self.order_books[sym] = self._synthetic_book(sym, price)
                        self._trades_received += 1
                        inc("massive_trades_received_total", labels={"market": market})
                        try:
                            from app.config import ARCHIVE_TICKS_ENABLED

                            if ARCHIVE_TICKS_ENABLED:
                                from app.services.archive.tick_writer import record_tick

                                record_tick(sym, price, volume=float(msg.get("s") or 0))
                        except Exception:
                            pass
                        updates[sym] = self.get_market_data(sym)
                    elif ev in quote_events and MASSIVE_QUOTES_ENABLED:
                        bp = float(msg.get("bp") or 0)
                        ap = float(msg.get("ap") or 0)
                        bs = float(msg.get("bs") or msg.get("s") or 1)
                        a_s = float(msg.get("as") or 1)
                        if bp <= 0 or ap <= 0:
                            continue
                        self._apply_nbbo(sym, bp, bs, ap, a_s)
                        mid = (bp + ap) / 2
                        self._symbols[sym]["price"] = mid
                        self._patch_forming_candle(sym, mid)
                        self._quotes_received += 1
                        inc("massive_quotes_received_total", labels={"market": market})
                        updates[sym] = self.get_market_data(sym)

                if updates and self.broadcast_callback:
                    await publish_market_update(self.broadcast_callback, updates)

    def _apply_nbbo(
        self,
        symbol: str,
        bid: float,
        bid_size: float,
        ask: float,
        ask_size: float,
    ) -> None:
        self._real_quotes.add(symbol)
        self.order_books[symbol] = self._nbbo_book(symbol, bid, bid_size, ask, ask_size)

    def _apply_minute_bar(self, symbol: str, candle: dict, *, synthetic_book: bool = True) -> None:
        if not candle.get("time"):
            return
        self._seeded.add(symbol)
        buf = list(self.candles.get(symbol, []))
        prev_len = len(buf)
        prev_last_time = buf[-1]["time"] if buf else None

        if buf and buf[-1]["time"] == candle["time"]:
            buf[-1] = candle
        else:
            buf.append(candle)

        if len(buf) > MAX_CANDLES:
            buf = buf[-MAX_CANDLES:]
        self.candles[symbol] = buf

        close = float(candle["close"])
        self._symbols[symbol]["price"] = close
        if synthetic_book:
            self.order_books[symbol] = self._synthetic_book(symbol, close)

        if prev_last_time is not None and candle["time"] != prev_last_time:
            self._bar_close.notify(symbol)
        elif prev_last_time is None and len(buf) > prev_len:
            pass

    def _patch_forming_candle(self, symbol: str, price: float) -> None:
        buf = self.candles.get(symbol)
        if not buf:
            return
        decimals = self._symbols[symbol]["decimals"]
        live = round(float(price), decimals)
        last = dict(buf[-1])
        curr_bucket = int(time.time() // 60) * 60
        bar_time = int(last.get("time") or 0)
        if bar_time < curr_bucket:
            buf.append({
                "time": curr_bucket,
                "open": live,
                "high": live,
                "low": live,
                "close": live,
                "volume": 0.0,
            })
        else:
            last["close"] = live
            last["high"] = round(max(float(last.get("high", live)), live), decimals)
            last["low"] = round(min(float(last.get("low", live)), live), decimals)
            buf[-1] = last
        if len(buf) > MAX_CANDLES:
            self.candles[symbol] = buf[-MAX_CANDLES:]
        else:
            self.candles[symbol] = buf

    def _live_candle_snapshot(self, symbol: str) -> dict:
        buf = self.candles.get(symbol)
        if not buf:
            return {}
        price = float(self._symbols[symbol]["price"])
        decimals = self._symbols[symbol]["decimals"]
        live = round(price, decimals)
        last = dict(buf[-1])
        if live != float(last.get("close", 0)):
            last["close"] = live
            last["high"] = round(max(float(last.get("high", live)), live), decimals)
            last["low"] = round(min(float(last.get("low", live)), live), decimals)
        return last

    def _nbbo_book(
        self,
        symbol: str,
        bid: float,
        bid_size: float,
        ask: float,
        ask_size: float,
    ) -> dict:
        decimals = self._symbols[symbol]["decimals"]
        best_bid = round(float(bid), decimals)
        best_ask = round(float(ask), decimals)
        mid = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        if spread <= 0:
            spread = round(mid * 0.0005, decimals) or 10 ** (-decimals)
        bids = [[best_bid, round(float(bid_size), 2)]]
        asks = [[best_ask, round(float(ask_size), 2)]]
        for i in range(1, 10):
            step = spread * (i + 1) * 0.5
            bids.append([round(best_bid - step, decimals), round(bid_size * (10 - i) / 5, 2)])
            asks.append([round(best_ask + step, decimals), round(ask_size * (10 - i) / 5, 2)])
        return {"bids": bids, "asks": asks}

    def _synthetic_book(self, symbol: str, price: float) -> dict:
        decimals = self._symbols[symbol]["decimals"]
        spread = round(price * 0.0005, decimals)
        if spread <= 0:
            spread = 10 ** (-decimals)
        best_bid = price - spread / 2
        best_ask = price + spread / 2
        bids = []
        asks = []
        for i in range(10):
            step = 0.0003 * (i + 1)
            bids.append([
                round(best_bid * (1 - step), decimals),
                round(100 * random.uniform(0.5, 2.0) * (10 - i) / 5, 2),
            ])
            asks.append([
                round(best_ask * (1 + step), decimals),
                round(100 * random.uniform(0.5, 2.0) * (10 - i) / 5, 2),
            ])
        return {"bids": bids, "asks": asks}
