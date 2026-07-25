"""Alpaca multi-asset live feed: equities (SIP/IEX) + crypto + options."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List

import httpx
import websockets

from app.api.outbound import history_update, publish, publish_market_update
from app.config import (
    ALPACA_API_KEY,
    ALPACA_BROADCAST_INTERVAL_SEC,
    ALPACA_CRYPTO_ENABLED,
    ALPACA_OPTIONS_ENABLED,
    ALPACA_SECRET_KEY,
    ALPACA_US_CRYPTO_BASES,
    CRYPTO_SYMBOLS,
    EQUITY_SYMBOLS,
    SYMBOLS,
)
from app.services.alpaca_data import (
    alpaca_crypto_to_terminal,
    env_flag,
    fallback_to_iex,
    get_alpaca_ws_url,
    get_crypto_ws_url,
    get_options_ws_url,
    is_option_symbol,
    is_sip_entitlement_error,
    resolve_option_watch_symbols,
    terminal_to_alpaca_crypto,
)
from app.services.base_feed import BaseFeedService
from app.services.feeds.bar_close import BarCloseEmitter

logger = logging.getLogger(__name__)

# Keep parity with Massive forming-bar depth (~25h of 1m bars).
_MAX_CANDLES = 1500
# Alpaca "latest/trades" can sit on a print for minutes while the book moves.
# Prefer quote mids once the last trade is older than this.
_CRYPTO_TRADE_FRESH_SEC = 5.0


def _alpaca_ts_age_sec(ts_val: Any) -> float | None:
    """Age in seconds for Alpaca RFC3339 timestamps (trade/quote ``t``)."""
    if ts_val is None:
        return None
    raw = str(ts_val).strip()
    if not raw:
        return None
    try:
        s = raw.replace("Z", "+00:00")
        # fromisoformat accepts up to microseconds; trim excess fractional digits.
        if "." in s:
            head, rest = s.split(".", 1)
            frac = ""
            tz = ""
            for i, ch in enumerate(rest):
                if ch.isdigit():
                    frac += ch
                else:
                    tz = rest[i:]
                    break
            s = f"{head}.{frac[:6]}{tz}"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, time.time() - dt.timestamp())
    except Exception:
        return None


def _option_meta(symbol: str) -> dict:
    return {
        "price": 1.0,
        "volatility": 0.002,
        "decimals": 2,
        "asset": symbol,
        "quote": "USD",
        "asset_class": "option",
    }


class AlpacaFeedService(BaseFeedService):
    def __init__(self):
        # Start from mode SYMBOLS (equities + crypto when enabled).
        self._symbols: Dict[str, dict] = {k: dict(v) for k, v in SYMBOLS.items()}
        for sym, info in self._symbols.items():
            info.setdefault(
                "asset_class",
                "crypto" if sym in CRYPTO_SYMBOLS or "USDT" in sym else "equity",
            )

        self.candles = {sym: self._generate_fallback_candles(sym) for sym in self._symbols}
        self.order_books: Dict[str, dict] = {}
        self.broadcast_callback = None
        self.active = False
        self._bar_close = BarCloseEmitter()
        self._pending_updates: set[str] = set()
        self._broadcast_task = None
        self._stream_tasks: list[asyncio.Task] = []
        self._equity_ws_url = ""
        self._crypto_to_terminal: Dict[str, str] = {}
        self._terminal_to_crypto: Dict[str, str] = {}
        self._last_quote_apply_ts: Dict[str, float] = {}
        # Last REST/WS trade event timestamp string per terminal symbol (dedupe stale prints).
        self._crypto_last_trade_event_ts: Dict[str, str] = {}
        self._ht_cache: dict[tuple, tuple[float, list]] = {}
        self._seed_done = False
        self._seed_expected = 0
        self._status: dict[str, Any] = {
            "equity": {"state": "idle", "ws": None, "symbols": 0},
            "crypto": {"state": "idle", "ws": None, "symbols": 0, "last_tick_ts": None},
            "options": {"state": "idle", "ws": None, "symbols": 0},
            "last_tick_ts": None,
        }

        for sym, info in self._symbols.items():
            self.order_books[sym] = self._generate_synthetic_book(sym, info["price"])

    @property
    def symbols(self) -> List[str]:
        return list(self._symbols.keys())

    @property
    def seed_done(self) -> bool:
        """True after REST history seed replaces synthetic startup candles."""
        return bool(self._seed_done)

    @property
    def watchlist_symbols(self) -> List[str]:
        """UI / terminal_config universe — hide OCC option contracts from the sidebar."""
        return [s for s in self._symbols if not is_option_symbol(s)]

    @property
    def alpaca_status(self) -> dict:
        """Ops snapshot for UI banners — shape mirrors MassiveFeedService.massive_status."""
        equity = dict(self._status.get("equity") or {})
        crypto = dict(self._status.get("crypto") or {})
        options = dict(self._status.get("options") or {})
        seeded = self._status.get("seeded") or {}

        equity_syms = [
            s
            for s, info in self._symbols.items()
            if info.get("asset_class") == "equity" and s in EQUITY_SYMBOLS
        ]
        crypto_syms = [
            s
            for s, info in self._symbols.items()
            if info.get("asset_class") == "crypto"
        ]

        equity_state = str(equity.get("state") or "idle")
        crypto_state = str(crypto.get("state") or "idle")
        stocks_connected = equity_state == "streaming"
        crypto_connected = crypto_state == "streaming"
        crypto_poll_ok = str(crypto.get("poll") or "") == "ok"

        if stocks_connected:
            stocks_mode = "websocket"
        elif equity_state == "simulated":
            stocks_mode = "simulated"
        else:
            stocks_mode = equity_state if equity_state not in ("idle",) else "idle"

        if crypto_connected:
            crypto_mode = "websocket"
        elif crypto_poll_ok:
            crypto_mode = "poll"
        else:
            crypto_mode = crypto_state if crypto_state not in ("idle",) else "idle"

        stocks_lag = self._asset_lag_sec(
            equity_syms,
            tick_ts=equity.get("last_tick_ts"),
        )
        crypto_lag = self._asset_lag_sec(
            crypto_syms,
            tick_ts=crypto.get("last_tick_ts"),
        )

        last_error = (
            equity.get("last_error")
            or crypto.get("last_error")
            or options.get("last_error")
            or None
        )

        seeded_n = len(seeded) if isinstance(seeded, dict) else 0
        seed_expected = int(self._seed_expected or 0)
        if seed_expected <= 0:
            seed_expected = len(
                [s for s in self._symbols if not is_option_symbol(s)]
            )

        return {
            "connected": stocks_connected or crypto_connected or crypto_poll_ok,
            "stocks_connected": stocks_connected,
            "crypto_connected": crypto_connected,
            "stocks_mode": stocks_mode,
            "crypto_mode": crypto_mode,
            # True when crypto is surviving on REST only (WS down / auth failed).
            "poll_fallback": bool(crypto_poll_ok and not crypto_connected),
            "equity": equity,
            "crypto": crypto,
            "options": options,
            "seeded": seeded if isinstance(seeded, dict) else {},
            "seeded_symbols": seeded_n,
            "seed_expected": seed_expected,
            "seeding": not self._seed_done,
            "equity_symbols": len(equity_syms),
            "crypto_symbols": len(crypto_syms),
            "subscriptions": int(equity.get("symbols") or 0)
            + int(crypto.get("symbols") or 0)
            + int(options.get("symbols") or 0),
            "stocks_lag_sec": round(stocks_lag, 2) if stocks_lag is not None else None,
            "crypto_lag_sec": round(crypto_lag, 2) if crypto_lag is not None else None,
            "last_tick_ts": self._status.get("last_tick_ts"),
            "last_error": last_error,
            "pending_broadcasts": len(self._pending_updates),
            "broadcast_interval_sec": max(0.5, float(ALPACA_BROADCAST_INTERVAL_SEC)),
        }

    def _asset_lag_sec(
        self,
        symbols: list[str],
        *,
        tick_ts: float | None = None,
    ) -> float | None:
        """Seconds since freshest tick or forming-bar time (Massive lag helper)."""
        candidates: list[float] = []
        if tick_ts is not None:
            try:
                candidates.append(float(tick_ts))
            except (TypeError, ValueError):
                pass
        latest_bar: int | None = None
        for sym in symbols:
            candles = self.candles.get(sym) or []
            if not candles:
                continue
            bar_time = int(candles[-1].get("time") or 0)
            if bar_time and (latest_bar is None or bar_time > latest_bar):
                latest_bar = bar_time
        if latest_bar is not None:
            candidates.append(float(latest_bar))
        if not candidates:
            return None
        return max(0.0, time.time() - max(candidates))

    def register_broadcast_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        self.broadcast_callback = callback

    def register_bar_close_callback(self, callback) -> None:
        self._bar_close.register(callback)

    def get_candles(self, symbol: str) -> List[dict]:
        return list(self.candles.get(symbol, []))

    def get_market_data(self, symbol: str) -> dict:
        if symbol not in self._symbols:
            return {}
        info = self._symbols[symbol]
        active_candles = self.candles.get(symbol, [])
        # Snapshot tracks live price between official minute bars (Massive parity).
        latest_candle = self._live_candle_snapshot(symbol)
        return {
            "symbol": symbol,
            "price": info["price"],
            "change_24h": (
                round(
                    (info["price"] - active_candles[0]["close"])
                    / active_candles[0]["close"]
                    * 100,
                    2,
                )
                if active_candles
                else 0.0
            ),
            "volume_24h": sum(c["volume"] for c in active_candles) if active_candles else 0.0,
            "high_24h": max(c["high"] for c in active_candles) if active_candles else info["price"],
            "low_24h": min(c["low"] for c in active_candles) if active_candles else info["price"],
            "orderbook": self.order_books.get(
                symbol, self._generate_synthetic_book(symbol, info["price"])
            ),
            "candle": latest_candle,
            "asset_class": info.get("asset_class", "equity"),
        }

    async def start(self) -> None:
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            logging.warning(
                "Alpaca API credentials missing. Feed will run in simulated mode."
            )
            self._start_simulated_fallback()
            return

        self.active = True
        self._merge_option_watchlist()
        self._build_crypto_maps()

        self._equity_ws_url = get_alpaca_ws_url()
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        self._stream_tasks = [
            asyncio.create_task(self._seed_history(), name="alpaca-seed"),
            asyncio.create_task(self._equity_ws_loop(), name="alpaca-equity-ws"),
        ]
        if ALPACA_CRYPTO_ENABLED and env_flag("ALPACA_CRYPTO_ENABLED", True):
            crypto_syms = [s for s in self._symbols if self._symbols[s].get("asset_class") == "crypto"]
            if crypto_syms:
                self._stream_tasks.append(
                    asyncio.create_task(self._crypto_ws_loop(), name="alpaca-crypto-ws")
                )
                self._stream_tasks.append(
                    asyncio.create_task(self._crypto_rest_poll_loop(), name="alpaca-crypto-poll")
                )
        if ALPACA_OPTIONS_ENABLED and env_flag("ALPACA_OPTIONS_ENABLED", True):
            opt_syms = [s for s in self._symbols if is_option_symbol(s)]
            if opt_syms:
                self._stream_tasks.append(
                    asyncio.create_task(self._options_ws_loop(), name="alpaca-options-ws")
                )

        logging.info(
            "Alpaca feed streams started (equity=%s, crypto=%s, options=%s).",
            self._equity_ws_url,
            bool(ALPACA_CRYPTO_ENABLED),
            bool(ALPACA_OPTIONS_ENABLED),
        )

    async def stop(self) -> None:
        self.active = False
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
            self._broadcast_task = None
        for task in self._stream_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._stream_tasks.clear()
        logging.info("Alpaca feed streams stopped.")

    async def subscribe(self, symbol: str) -> None:
        pass

    async def unsubscribe(self, symbol: str) -> None:
        pass

    def _merge_option_watchlist(self) -> None:
        if not (ALPACA_OPTIONS_ENABLED and env_flag("ALPACA_OPTIONS_ENABLED", True)):
            return
        try:
            opts = resolve_option_watch_symbols(limit_per_underlying=2)
        except Exception as exc:
            logger.warning("Option watchlist resolve error: %s", exc)
            opts = []
        for sym in opts:
            if sym in self._symbols:
                continue
            meta = _option_meta(sym)
            self._symbols[sym] = meta
            self.candles[sym] = self._generate_fallback_candles(sym)
            self.order_books[sym] = self._generate_synthetic_book(sym, meta["price"])
        if opts:
            logging.info("Alpaca options watchlist: %s", ", ".join(opts))

    def _build_crypto_maps(self) -> None:
        self._crypto_to_terminal.clear()
        self._terminal_to_crypto.clear()
        # Stick to liquid pairs known to trade on `v1beta3/crypto/us`.
        for sym, info in self._symbols.items():
            if info.get("asset_class") != "crypto" and sym not in CRYPTO_SYMBOLS:
                continue
            info["asset_class"] = "crypto"
            wire = terminal_to_alpaca_crypto(sym)
            if not wire:
                continue
            wire = wire.strip().upper()
            base = wire.split("/", 1)[0] if "/" in wire else wire
            if base not in ALPACA_US_CRYPTO_BASES:
                continue
            self._terminal_to_crypto[sym] = wire
            self._crypto_to_terminal[wire] = sym
            if "/" in wire:
                self._crypto_to_terminal[wire.replace("/", "")] = sym

    def _start_simulated_fallback(self):
        self.active = True
        self._status["equity"]["state"] = "simulated"

        async def fallback_loop():
            import random

            while self.active:
                for sym, info in self._symbols.items():
                    prev_price = info["price"]
                    change = prev_price * random.normalvariate(0, info["volatility"])
                    new_price = round(prev_price + change, info["decimals"])
                    info["price"] = new_price
                    self.order_books[sym] = self._generate_synthetic_book(sym, new_price)
                    active_candles = self.candles[sym]
                    curr_min = int(time.time() // 60) * 60
                    if not active_candles or active_candles[-1]["time"] < curr_min:
                        active_candles.append(
                            {
                                "time": curr_min,
                                "open": prev_price,
                                "high": max(prev_price, new_price),
                                "low": min(prev_price, new_price),
                                "close": new_price,
                                "volume": round(random.uniform(50, 500), 2),
                            }
                        )
                        if len(active_candles) > 500:
                            active_candles.pop(0)
                    else:
                        c = active_candles[-1]
                        c["high"] = max(c["high"], new_price)
                        c["low"] = min(c["low"], new_price)
                        c["close"] = new_price
                        c["volume"] = round(c["volume"] + random.uniform(5, 50), 2)
                    if self.broadcast_callback:
                        await publish_market_update(
                            self.broadcast_callback,
                            {sym: self.get_market_data(sym)},
                        )
                await asyncio.sleep(1.0)

        self._stream_tasks = [asyncio.create_task(fallback_loop())]

    async def _broadcast_loop(self) -> None:
        """Coalesce dirty symbols into slim market_update frames (IB/Massive parity).

        Full snapshots (with orderbooks) are expensive; broadcasting them on every
        tick starves the asyncio loop and makes HTTP session/candles time out.
        """
        interval = max(0.5, float(ALPACA_BROADCAST_INTERVAL_SEC))
        while self.active:
            try:
                await asyncio.sleep(interval)
                if not self.broadcast_callback or not self._pending_updates:
                    continue
                symbols = list(self._pending_updates)
                self._pending_updates.clear()
                batch: dict[str, dict] = {}
                for symbol in symbols:
                    md = self.get_market_data(symbol)
                    if not md:
                        continue
                    # Slim payload only — synthetic books are for REST/orderbook
                    # interest, not the coalesced tick path.
                    batch[symbol] = {
                        "symbol": symbol,
                        "price": md["price"],
                        "change_24h": md.get("change_24h"),
                        "volume_24h": md.get("volume_24h"),
                        "high_24h": md.get("high_24h"),
                        "low_24h": md.get("low_24h"),
                        "candle": md.get("candle"),
                        "asset_class": md.get("asset_class", "equity"),
                    }
                if batch:
                    await publish_market_update(self.broadcast_callback, batch)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logging.error("Alpaca broadcast loop error: %s", exc)

    def _queue_market_broadcast(self, symbol: str) -> None:
        if symbol in self._symbols:
            self._pending_updates.add(symbol)
            self._status["last_tick_ts"] = time.time()

    async def _yield_loop(self, counter: list[int], every: int = 16) -> None:
        """Let the coalesced broadcast task run between dense Alpaca frames."""
        counter[0] += 1
        if counter[0] % every == 0:
            await asyncio.sleep(0)

    def _patch_forming_candle(
        self,
        symbol: str,
        price: float,
        *,
        bid: float | None = None,
        ask: float | None = None,
    ) -> None:
        """Roll/update the forming 1m bar from a live trade/quote (Massive/IB parity).

        When quote mids sit still (common on Alpaca crypto), still expand high/low
        from bid/ask so the chart wick moves and the UI does not look dead.
        """
        buf = self.candles.get(symbol)
        if not buf:
            return
        decimals = int(self._symbols[symbol].get("decimals", 2) or 2)
        live = round(float(price), decimals)
        hi_extra = ask if ask is not None else live
        lo_extra = bid if bid is not None else live
        try:
            hi_extra = round(float(hi_extra), decimals)
            lo_extra = round(float(lo_extra), decimals)
        except (TypeError, ValueError):
            hi_extra = live
            lo_extra = live
        last = dict(buf[-1])
        curr_bucket = int(time.time() // 60) * 60
        bar_time = int(last.get("time") or 0)
        if bar_time < curr_bucket:
            buf.append(
                {
                    "time": curr_bucket,
                    "open": live,
                    "high": max(live, hi_extra),
                    "low": min(live, lo_extra),
                    "close": live,
                    "volume": 0.0,
                }
            )
            self._bar_close.notify(symbol)
        else:
            last["close"] = live
            last["high"] = round(max(float(last.get("high", live)), live, hi_extra), decimals)
            last["low"] = round(min(float(last.get("low", live)), live, lo_extra), decimals)
            buf[-1] = last
        if len(buf) > _MAX_CANDLES:
            self.candles[symbol] = buf[-_MAX_CANDLES:]
        else:
            self.candles[symbol] = buf

    def _live_candle_snapshot(self, symbol: str) -> dict:
        """Wire candle close tracks live price between official Alpaca minute bars."""
        buf = self.candles.get(symbol)
        if not buf:
            return {}
        price = float(self._symbols[symbol]["price"])
        decimals = int(self._symbols[symbol].get("decimals", 2) or 2)
        live = round(price, decimals)
        last = dict(buf[-1])
        bid = self._symbols[symbol].get("bid")
        ask = self._symbols[symbol].get("ask")
        hi = live
        lo = live
        try:
            if ask is not None:
                hi = max(hi, round(float(ask), decimals))
            if bid is not None:
                lo = min(lo, round(float(bid), decimals))
        except (TypeError, ValueError):
            pass
        last["close"] = live
        last["high"] = round(max(float(last.get("high", live)), hi), decimals)
        last["low"] = round(min(float(last.get("low", live)), lo), decimals)
        return last

    def _apply_trade(
        self,
        symbol: str,
        price: float,
        size: float = 0.0,
        *,
        from_quote: bool = False,
        bid: float | None = None,
        ask: float | None = None,
    ) -> None:
        if symbol not in self._symbols or price is None:
            return
        try:
            px = float(price)
        except (TypeError, ValueError):
            return
        if bid is not None:
            try:
                self._symbols[symbol]["bid"] = float(bid)
            except (TypeError, ValueError):
                pass
        if ask is not None:
            try:
                self._symbols[symbol]["ask"] = float(ask)
            except (TypeError, ValueError):
                pass
        if from_quote:
            # Quotes can arrive hundreds/sec; keep forming bar alive without
            # monopolizing the event loop.
            now = time.time()
            last = self._last_quote_apply_ts.get(symbol, 0.0)
            if now - last < 0.2:
                # Still coalesce a broadcast so the UI does not freeze between
                # full candle patches when quotes are the only tape.
                prev = self._symbols[symbol].get("price")
                prev_bar = None
                buf0 = self.candles.get(symbol) or []
                if buf0:
                    b0 = buf0[-1]
                    prev_bar = (b0.get("high"), b0.get("low"), b0.get("close"))
                self._symbols[symbol]["price"] = px
                self._patch_forming_candle(symbol, px, bid=bid, ask=ask)
                next_bar = prev_bar
                buf1 = self.candles.get(symbol) or []
                if buf1:
                    b1 = buf1[-1]
                    next_bar = (b1.get("high"), b1.get("low"), b1.get("close"))
                if prev != px or prev_bar != next_bar:
                    self._pending_updates.add(symbol)
                return
            self._last_quote_apply_ts[symbol] = now
        self._symbols[symbol]["price"] = px
        try:
            from app.services.archive.tick_writer import record_tick

            record_tick(symbol, px, volume=float(size or 0))
        except Exception:
            pass
        self._patch_forming_candle(symbol, px, bid=bid, ask=ask)
        self._queue_market_broadcast(symbol)

    def _apply_bar(self, symbol: str, msg: dict) -> None:
        if symbol not in self._symbols:
            return
        t_str = msg.get("t")
        try:
            t_epoch = int(datetime.fromisoformat(str(t_str).replace("Z", "+00:00")).timestamp())
        except Exception:
            t_epoch = int(time.time() // 60) * 60
        # Floor to minute bucket so chart comparisons stay stable.
        t_epoch = int(t_epoch // 60) * 60
        active_candles = self.candles[symbol]
        new_candle = {
            "time": t_epoch,
            "open": msg.get("o"),
            "high": msg.get("h"),
            "low": msg.get("l"),
            "close": msg.get("c"),
            "volume": round(float(msg.get("v", 0) or 0), 2),
        }
        if active_candles and int(active_candles[-1]["time"]) == t_epoch:
            active_candles[-1] = new_candle
        elif not active_candles or int(active_candles[-1]["time"]) < t_epoch:
            active_candles.append(new_candle)
            if len(active_candles) > _MAX_CANDLES:
                self.candles[symbol] = active_candles[-_MAX_CANDLES:]
            self._bar_close.notify(symbol)
        else:
            # Late/out-of-order closed bar — replace matching time, never append
            # behind a newer forming minute (that freezes the wrong last bar).
            replaced = False
            for i in range(len(active_candles) - 1, -1, -1):
                if int(active_candles[i]["time"]) == t_epoch:
                    active_candles[i] = new_candle
                    replaced = True
                    break
                if int(active_candles[i]["time"]) < t_epoch:
                    active_candles.insert(i + 1, new_candle)
                    replaced = True
                    break
            if not replaced:
                active_candles.insert(0, new_candle)
            if len(active_candles) > _MAX_CANDLES:
                self.candles[symbol] = active_candles[-_MAX_CANDLES:]
            self._queue_market_broadcast(symbol)
            return
        close = new_candle.get("close")
        if close is not None:
            try:
                self._symbols[symbol]["price"] = float(close)
            except (TypeError, ValueError):
                pass
        self._queue_market_broadcast(symbol)

    async def _auth_json_stream(self, ws) -> tuple[bool, dict]:
        welcome = await ws.recv()
        logging.info("Alpaca stream connected: %s", welcome)
        await ws.send(
            json.dumps(
                {
                    "action": "auth",
                    "key": ALPACA_API_KEY,
                    "secret": ALPACA_SECRET_KEY,
                }
            )
        )
        auth_resp = await ws.recv()
        logging.info("Alpaca auth response: %s", auth_resp)
        auth_data = json.loads(auth_resp)
        first = auth_data[0] if auth_data else {}
        return first.get("msg") == "authenticated", first

    async def _equity_ws_loop(self):
        equity_syms = [
            s
            for s, info in self._symbols.items()
            if info.get("asset_class") == "equity" and s in EQUITY_SYMBOLS
        ]
        self._status["equity"]["symbols"] = len(equity_syms)
        while self.active:
            try:
                self._status["equity"]["state"] = "connecting"
                self._status["equity"]["ws"] = self._equity_ws_url
                async with websockets.connect(self._equity_ws_url) as ws:
                    ok, first = await self._auth_json_stream(ws)
                    if not ok:
                        if first.get("T") == "error" and (
                            first.get("code") == 409
                            or is_sip_entitlement_error(0, first.get("msg", ""))
                        ):
                            _, self._equity_ws_url = fallback_to_iex()
                            await asyncio.sleep(1)
                            continue
                        self._status["equity"]["state"] = "auth_failed"
                        self._status["equity"]["last_error"] = str(
                            first.get("msg") or first
                        )[:200]
                        await asyncio.sleep(5)
                        continue
                    sub_msg = {
                        "action": "subscribe",
                        "bars": equity_syms,
                        "trades": equity_syms,
                        "quotes": equity_syms,
                    }
                    await ws.send(json.dumps(sub_msg))
                    sub_resp = await ws.recv()
                    logging.info("Alpaca equity subscription: %s", sub_resp)
                    self._status["equity"]["state"] = "streaming"
                    spun = [0]
                    async for msg_str in ws:
                        if not self.active:
                            break
                        for m in json.loads(msg_str):
                            stream_type = m.get("T")
                            symbol = m.get("S")
                            if symbol not in self._symbols:
                                continue
                            if stream_type == "t":
                                self._apply_trade(symbol, m.get("p"), m.get("s", 0))
                                self._status["equity"]["last_tick_ts"] = time.time()
                            elif stream_type == "q":
                                bp, ap = m.get("bp"), m.get("ap")
                                try:
                                    bp_f = float(bp) if bp is not None else None
                                    ap_f = float(ap) if ap is not None else None
                                    if bp_f is not None and ap_f is not None:
                                        mid = (bp_f + ap_f) / 2.0
                                        self._apply_trade(
                                            symbol, mid, 0, from_quote=True, bid=bp_f, ask=ap_f
                                        )
                                        self._status["equity"]["last_tick_ts"] = time.time()
                                    elif bp_f is not None:
                                        self._apply_trade(
                                            symbol, bp_f, 0, from_quote=True, bid=bp_f, ask=ap_f
                                        )
                                        self._status["equity"]["last_tick_ts"] = time.time()
                                    elif ap_f is not None:
                                        self._apply_trade(
                                            symbol, ap_f, 0, from_quote=True, bid=bp_f, ask=ap_f
                                        )
                                        self._status["equity"]["last_tick_ts"] = time.time()
                                except (TypeError, ValueError):
                                    pass
                            elif stream_type == "b":
                                self._apply_bar(symbol, m)
                                self._status["equity"]["last_tick_ts"] = time.time()
                        await self._yield_loop(spun)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._status["equity"]["state"] = "error"
                self._status["equity"]["last_error"] = str(exc)[:200]
                logging.error("Alpaca equity feed error: %s. Reconnecting in 5s.", exc)
                await asyncio.sleep(5)

    async def _crypto_ws_loop(self):
        # Prefer liquid majors first — some plans truncate large subscribe lists.
        # Keep the live socket small so BTC/ETH quotes are not starved; REST poll
        # covers the rest of the watchlist.
        preferred = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "DOGE/USD")
        wire_set = set(self._terminal_to_crypto.values())
        wire_syms = [w for w in preferred if w in wire_set]
        # Cap live WS subscriptions — Alpaca crypto allows one connection and
        # large quote fan-out stalls the asyncio loop (UI looks frozen).
        max_ws = 8
        if len(wire_syms) < max_ws:
            wire_syms.extend(
                sorted(wire_set - set(wire_syms))[: max(0, max_ws - len(wire_syms))]
            )
        self._status["crypto"]["symbols"] = len(wire_syms)
        self._status["crypto"]["wire"] = wire_syms[:8]
        url = get_crypto_ws_url()
        self._status["crypto"]["ws"] = url
        while self.active:
            try:
                self._status["crypto"]["state"] = "connecting"
                async with websockets.connect(url) as ws:
                    ok, first = await self._auth_json_stream(ws)
                    if not ok:
                        self._status["crypto"]["state"] = "auth_failed"
                        self._status["crypto"]["last_error"] = str(
                            first.get("msg") or first
                        )[:200]
                        logging.error("Alpaca crypto auth failed: %s", first)
                        await asyncio.sleep(5)
                        continue
                    sub_msg = {
                        "action": "subscribe",
                        "bars": wire_syms,
                        "trades": wire_syms,
                        "quotes": wire_syms,
                    }
                    await ws.send(json.dumps(sub_msg))
                    sub_resp = await ws.recv()
                    logging.info("Alpaca crypto subscription: %s", sub_resp)
                    self._status["crypto"]["sub"] = str(sub_resp)[:240]
                    self._status["crypto"]["state"] = "streaming"
                    self._status["crypto"]["ws_msgs"] = 0
                    self._status["crypto"]["ws_trades"] = 0
                    self._status["crypto"]["ws_quotes"] = 0
                    self._status["crypto"]["ws_bars"] = 0
                    spun = [0]
                    async for msg_str in ws:
                        if not self.active:
                            break
                        self._status["crypto"]["ws_msgs"] = int(
                            self._status["crypto"].get("ws_msgs") or 0
                        ) + 1
                        for m in json.loads(msg_str):
                            stream_type = m.get("T")
                            if stream_type in ("success", "subscription", "error"):
                                if stream_type == "error":
                                    logging.warning("Alpaca crypto stream msg: %s", m)
                                continue
                            wire = m.get("S")
                            if isinstance(wire, str):
                                wire = wire.strip().upper()
                            symbol = self._crypto_to_terminal.get(wire) or alpaca_crypto_to_terminal(
                                wire or ""
                            )
                            if symbol not in self._symbols:
                                if wire and "/" not in wire and wire.endswith("USD"):
                                    alt = f"{wire[:-3]}/USD"
                                    symbol = self._crypto_to_terminal.get(alt) or ""
                                if symbol not in self._symbols:
                                    continue
                            if stream_type == "t":
                                self._status["crypto"]["ws_trades"] = int(
                                    self._status["crypto"].get("ws_trades") or 0
                                ) + 1
                                evt = str(m.get("t") or "")
                                if evt:
                                    self._crypto_last_trade_event_ts[symbol] = evt
                                self._apply_trade(symbol, m.get("p"), m.get("s", 0))
                                self._status["crypto"]["last_tick_ts"] = time.time()
                            elif stream_type == "q":
                                self._status["crypto"]["ws_quotes"] = int(
                                    self._status["crypto"].get("ws_quotes") or 0
                                ) + 1
                                bp, ap = m.get("bp"), m.get("ap")
                                try:
                                    bp_f = float(bp) if bp is not None else None
                                    ap_f = float(ap) if ap is not None else None
                                    if bp_f is not None and ap_f is not None:
                                        mid = (bp_f + ap_f) / 2.0
                                        self._apply_trade(
                                            symbol, mid, 0, from_quote=True, bid=bp_f, ask=ap_f
                                        )
                                        self._status["crypto"]["last_tick_ts"] = time.time()
                                    elif bp_f is not None:
                                        self._apply_trade(
                                            symbol, bp_f, 0, from_quote=True, bid=bp_f, ask=ap_f
                                        )
                                        self._status["crypto"]["last_tick_ts"] = time.time()
                                    elif ap_f is not None:
                                        self._apply_trade(
                                            symbol, ap_f, 0, from_quote=True, bid=bp_f, ask=ap_f
                                        )
                                        self._status["crypto"]["last_tick_ts"] = time.time()
                                except (TypeError, ValueError):
                                    pass
                            elif stream_type == "b":
                                self._status["crypto"]["ws_bars"] = int(
                                    self._status["crypto"].get("ws_bars") or 0
                                ) + 1
                                self._apply_bar(symbol, m)
                                self._status["crypto"]["last_tick_ts"] = time.time()
                        await self._yield_loop(spun, every=8)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._status["crypto"]["state"] = "error"
                self._status["crypto"]["last_error"] = str(exc)[:200]
                logging.error("Alpaca crypto feed error: %s. Reconnecting in 5s.", exc)
                await asyncio.sleep(5)

    async def _crypto_rest_poll_loop(self) -> None:
        """REST heartbeat when crypto WS is sparse.

        Fresh last-trade prints win. Stale prints (common on quiet tape) must NOT
        overwrite live quote mids every poll — that freezes the UI on an old price.
        """
        import httpx

        trades_url = "https://data.alpaca.markets/v1beta3/crypto/us/latest/trades"
        quotes_url = "https://data.alpaca.markets/v1beta3/crypto/us/latest/quotes"
        headers = {
            "APCA-API-KEY-ID": ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
        }
        self._status["crypto"]["poll"] = "starting"
        while self.active:
            try:
                wires = list(dict.fromkeys(self._terminal_to_crypto.values()))
                if not wires:
                    self._status["crypto"]["poll"] = "no_symbols"
                    await asyncio.sleep(2.0)
                    continue
                preferred = [w for w in ("BTC/USD", "ETH/USD", "SOL/USD") if w in wires]
                rest = [w for w in wires if w not in preferred]
                batch = preferred + rest
                applied = 0
                used_quote = 0
                used_trade = 0
                async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
                    for i in range(0, len(batch), 8):
                        chunk = batch[i : i + 8]
                        sym_param = ",".join(chunk)
                        trades: dict = {}
                        quotes: dict = {}
                        tr = await client.get(trades_url, params={"symbols": sym_param})
                        if tr.status_code == 200:
                            body = tr.json() or {}
                            raw = body.get("trades") or {}
                            if isinstance(raw, dict):
                                trades = raw
                        else:
                            self._status["crypto"]["poll"] = f"trades_http_{tr.status_code}"
                        # Always fetch quotes — needed when last trade is stale.
                        qr = await client.get(quotes_url, params={"symbols": sym_param})
                        if qr.status_code == 200:
                            body = qr.json() or {}
                            raw = body.get("quotes") or {}
                            if isinstance(raw, dict):
                                quotes = raw
                        else:
                            self._status["crypto"]["poll"] = f"quotes_http_{qr.status_code}"

                        for wire in chunk:
                            w = str(wire).strip().upper()
                            symbol = self._crypto_to_terminal.get(w) or alpaca_crypto_to_terminal(w)
                            if symbol not in self._symbols:
                                continue
                            px = None
                            size = 0.0
                            from_quote = False
                            bp = ap = None
                            qrow = quotes.get(wire) or quotes.get(w)
                            if isinstance(qrow, dict):
                                try:
                                    if qrow.get("bp") is not None:
                                        bp = float(qrow.get("bp"))
                                    if qrow.get("ap") is not None:
                                        ap = float(qrow.get("ap"))
                                except (TypeError, ValueError):
                                    bp = ap = None
                            row = trades.get(wire) or trades.get(w)
                            trade_evt = ""
                            if isinstance(row, dict) and row.get("p") is not None:
                                trade_evt = str(row.get("t") or "")
                                age = _alpaca_ts_age_sec(row.get("t"))
                                fresh = age is not None and age <= _CRYPTO_TRADE_FRESH_SEC
                                # Skip duplicate stale prints that would pin the UI.
                                duplicate = (
                                    bool(trade_evt)
                                    and self._crypto_last_trade_event_ts.get(symbol) == trade_evt
                                    and not fresh
                                )
                                if fresh and not duplicate:
                                    try:
                                        px = float(row.get("p"))
                                        size = float(row.get("s") or 0)
                                    except (TypeError, ValueError):
                                        px = None
                                    if px is not None and trade_evt:
                                        self._crypto_last_trade_event_ts[symbol] = trade_evt
                            if px is None:
                                try:
                                    if bp is not None and ap is not None:
                                        px = (bp + ap) / 2.0
                                    elif ap is not None:
                                        px = ap
                                    elif bp is not None:
                                        px = bp
                                    else:
                                        continue
                                except (TypeError, ValueError):
                                    continue
                                from_quote = True
                            self._apply_trade(
                                symbol, px, size, from_quote=from_quote, bid=bp, ask=ap
                            )
                            self._status["crypto"]["last_tick_ts"] = time.time()
                            applied += 1
                            if from_quote:
                                used_quote += 1
                            else:
                                used_trade += 1
                        await asyncio.sleep(0)
                self._status["crypto"]["poll"] = "ok"
                self._status["crypto"]["poll_ts"] = time.time()
                self._status["crypto"]["poll_applied"] = applied
                self._status["crypto"]["poll_trades"] = used_trade
                self._status["crypto"]["poll_quotes"] = used_quote
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._status["crypto"]["poll"] = f"error:{exc}"[:120]
                logging.warning("Alpaca crypto REST poll failed: %s", exc)
            await asyncio.sleep(0.75)

    async def _options_ws_loop(self):
        """Options stream requires msgpack (Alpaca v1beta1 indicative/opra)."""
        import msgpack

        opt_syms = [s for s in self._symbols if is_option_symbol(s)]
        self._status["options"]["symbols"] = len(opt_syms)
        url = get_options_ws_url()
        self._status["options"]["ws"] = url
        while self.active:
            try:
                self._status["options"]["state"] = "connecting"
                async with websockets.connect(
                    url,
                    additional_headers={"Content-Type": "application/msgpack"},
                ) as ws:
                    # Welcome may be msgpack or json depending on feed.
                    welcome = await ws.recv()
                    logging.info(
                        "Alpaca options stream connected (%s): %s",
                        url,
                        welcome if isinstance(welcome, str) else "<msgpack>",
                    )
                    auth = msgpack.packb(
                        {
                            "action": "auth",
                            "key": ALPACA_API_KEY,
                            "secret": ALPACA_SECRET_KEY,
                        }
                    )
                    await ws.send(auth)
                    auth_raw = await ws.recv()
                    try:
                        auth_data = (
                            msgpack.unpackb(auth_raw, raw=False)
                            if isinstance(auth_raw, (bytes, bytearray))
                            else json.loads(auth_raw)
                        )
                    except Exception:
                        auth_data = []
                    first = auth_data[0] if isinstance(auth_data, list) and auth_data else {}
                    if first.get("msg") != "authenticated":
                        self._status["options"]["state"] = "auth_failed"
                        logging.error(
                            "Alpaca options auth failed (plan may lack options data): %s",
                            first or auth_raw,
                        )
                        # Soft-fail: do not spin aggressively when entitlement is missing.
                        await asyncio.sleep(60)
                        continue

                    sub = msgpack.packb(
                        {
                            "action": "subscribe",
                            # Trades only — indicative quote storms starve the
                            # asyncio loop and freeze market_update broadcasts.
                            "trades": opt_syms,
                        }
                    )
                    await ws.send(sub)
                    sub_raw = await ws.recv()
                    logging.info("Alpaca options subscription ack received")
                    self._status["options"]["state"] = "streaming"
                    spun = [0]
                    async for raw in ws:
                        if not self.active:
                            break
                        try:
                            msgs = (
                                msgpack.unpackb(raw, raw=False)
                                if isinstance(raw, (bytes, bytearray))
                                else json.loads(raw)
                            )
                        except Exception:
                            continue
                        if not isinstance(msgs, list):
                            msgs = [msgs]
                        for m in msgs:
                            if not isinstance(m, dict):
                                continue
                            stream_type = m.get("T")
                            symbol = str(m.get("S") or "").upper()
                            if symbol not in self._symbols:
                                continue
                            if stream_type == "t":
                                self._apply_trade(symbol, m.get("p"), m.get("s", 0))
                        await self._yield_loop(spun, every=8)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._status["options"]["state"] = "error"
                logging.error("Alpaca options feed error: %s. Reconnecting in 10s.", exc)
                await asyncio.sleep(10)

    # ── History seed + native HT (Massive chart parity) ─────────────────────

    _ALPACA_TF_WIRE = {
        "1m": "1Min",
        "5m": "5Min",
        "15m": "15Min",
        "1h": "1Hour",
        "4h": "4Hour",
        "1d": "1Day",
    }
    _HT_CACHE_TTL_SEC = 45.0

    def fetch_ht_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int | None = None,
        *,
        purpose: str = "chart",
    ) -> list[dict]:
        """Native higher-timeframe OHLCV from Alpaca REST (cached briefly)."""
        if symbol not in self._symbols or is_option_symbol(symbol):
            return []
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            return []

        from app.services.market.timeframes import normalize_timeframe, timeframe_to_secs

        try:
            tf = normalize_timeframe(timeframe)
        except ValueError:
            return []
        if tf == "1m":
            return self.get_candles(symbol)

        wire_tf = self._ALPACA_TF_WIRE.get(tf)
        if not wire_tf:
            return []

        # Analysis (bots / ML warm-up) needs deeper series than chart snapshots.
        try:
            from app.services.massive_ht_limits import MASSIVE_HT_FETCH_MAX, massive_ht_limit

            default_cap = massive_ht_limit(
                tf, purpose="analysis" if purpose == "analysis" else "chart",
            )
            hard_max = MASSIVE_HT_FETCH_MAX
        except Exception:
            default_cap = 2000 if purpose == "analysis" else 500
            hard_max = 10000
        cap = max(50, min(int(limit or default_cap), hard_max))
        # Analysis fetches skip the short chart cache so warm-up sees full depth.
        cache_key = (symbol, tf, purpose if purpose == "analysis" else "chart")
        now = time.time()
        cached = self._ht_cache.get(cache_key)
        if cached and cached[0] > now and cached[1]:
            bars = cached[1]
            return bars[-cap:] if len(bars) > cap else list(bars)

        lookback_days = 5 if tf in ("5m", "15m") else (30 if tf == "1h" else 120)
        # Size the window so newest-first pagination can fill `cap` bars.
        bar_secs = timeframe_to_secs(tf)
        need_days = max(2, int((max(cap, 500) * bar_secs) / 86400) + 2)
        lookback_days = max(lookback_days, need_days)
        to_ts = int(time.time())
        from_ts = to_ts - lookback_days * 86400
        try:
            bars = self._fetch_bars_rest(symbol, wire_tf, from_ts, to_ts, limit=max(cap, 500))
        except Exception as exc:
            logger.warning("Alpaca HT fetch failed for %s %s: %s", symbol, tf, exc)
            return []
        if not bars:
            return []
        self._ht_cache[cache_key] = (now + self._HT_CACHE_TTL_SEC, bars)
        if len(self._ht_cache) > 64:
            # Drop oldest entries.
            for k, _ in sorted(self._ht_cache.items(), key=lambda kv: kv[1][0])[:16]:
                self._ht_cache.pop(k, None)
        return bars[-cap:] if len(bars) > cap else bars

    async def _seed_history(self) -> None:
        """Replace synthetic fallback candles with real Alpaca REST 1m history."""
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            self._seed_done = True
            return
        sem = asyncio.Semaphore(3)
        symbols = [
            s
            for s in self._symbols
            if not is_option_symbol(s)
        ]
        self._seed_expected = len(symbols)
        self._seed_done = False

        async def _seed_one(symbol: str) -> None:
            async with sem:
                if not self.active:
                    return
                try:
                    bars = await asyncio.to_thread(self._fetch_seed_1m, symbol)
                    if not bars or len(bars) < 30:
                        return
                    # Preserve a live forming bar if WS already rolled past REST.
                    # Never stitch synthetic startup bars over cold REST history
                    # (weekend equities) — that fakes low stocks_lag forever.
                    live_tail = None
                    existing = self.candles.get(symbol) or []
                    rest_t = int(bars[-1].get("time") or 0)
                    rest_age = time.time() - rest_t if rest_t else 1e9
                    if existing and rest_age < 180:
                        last = existing[-1]
                        if int(last.get("time") or 0) > rest_t:
                            live_tail = dict(last)
                    merged = bars[-_MAX_CANDLES:]
                    if live_tail:
                        if int(merged[-1]["time"]) == int(live_tail["time"]):
                            merged[-1] = live_tail
                        else:
                            merged.append(live_tail)
                            if len(merged) > _MAX_CANDLES:
                                merged = merged[-_MAX_CANDLES:]
                    self.candles[symbol] = merged
                    self._symbols[symbol]["price"] = float(merged[-1]["close"])
                    self._status.setdefault("seeded", {})
                    if isinstance(self._status.get("seeded"), dict):
                        self._status["seeded"][symbol] = len(merged)
                    logging.info("Alpaca seeded %s (%d bars)", symbol, len(merged))
                    if self.broadcast_callback:
                        snap = merged[-min(len(merged), 800):]
                        await publish(
                            self.broadcast_callback,
                            history_update({symbol: snap}, meta={"interval": "1m", "symbol": symbol}),
                        )
                        self._queue_market_broadcast(symbol)
                except Exception as exc:
                    logger.warning("Alpaca seed failed for %s: %s", symbol, exc)

        # Prefer liquid chart symbols first so the default UI fills quickly.
        preferred = [
            s for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "SPY", "QQQ", "AAPL") if s in self._symbols
        ]
        rest = [s for s in symbols if s not in preferred]
        try:
            await asyncio.gather(*(_seed_one(s) for s in preferred + rest if self.active))
        finally:
            self._seed_done = True
            seeded_n = len(self._status.get("seeded") or {})
            logging.info(
                "Alpaca seed complete (%d/%d symbols).",
                seeded_n,
                self._seed_expected,
            )

    def _fetch_seed_1m(self, symbol: str) -> list[dict]:
        to_ts = int(time.time())
        # ~2 calendar days of crypto / ~7 days equities covers chart depth.
        info = self._symbols.get(symbol) or {}
        is_crypto = info.get("asset_class") == "crypto" or symbol in CRYPTO_SYMBOLS
        lookback = 2 * 86400 if is_crypto else 7 * 86400
        from_ts = to_ts - lookback
        if is_crypto:
            return self._fetch_bars_rest(symbol, "1Min", from_ts, to_ts, limit=3000)
        from app.services.archive.broker_fetch import fetch_alpaca_1m_bars

        rows = fetch_alpaca_1m_bars(symbol, from_ts, to_ts)
        out = []
        for row in rows or []:
            out.append(
                {
                    "time": int(row["time"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume") or 0),
                }
            )
        return out

    def _fetch_bars_rest(
        self,
        symbol: str,
        wire_tf: str,
        from_ts: int,
        to_ts: int,
        *,
        limit: int = 1000,
    ) -> list[dict]:
        """Fetch equity or crypto bars from Alpaca Market Data REST."""
        info = self._symbols.get(symbol) or {}
        is_crypto = info.get("asset_class") == "crypto" or symbol in CRYPTO_SYMBOLS
        start = datetime.fromtimestamp(from_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = datetime.fromtimestamp(to_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        headers = {
            "APCA-API-KEY-ID": ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
        }
        if is_crypto:
            wire = self._terminal_to_crypto.get(symbol) or terminal_to_alpaca_crypto(symbol)
            if not wire:
                return []
            url = "https://data.alpaca.markets/v1beta3/crypto/us/bars"
            params = {
                "symbols": wire,
                "timeframe": wire_tf,
                "start": start,
                "end": end,
                "limit": min(limit, 10000),
                # Newest-first — asc+limit returns the oldest page of a wide
                # window (5m charts looked days stale vs 1m/15m).
                "sort": "desc",
            }
            key = wire
        else:
            from app.services.alpaca_data import resolve_equity_data_feed

            url = "https://data.alpaca.markets/v2/stocks/bars"
            params = {
                "symbols": symbol.upper(),
                "timeframe": wire_tf,
                "start": start,
                "end": end,
                "limit": min(limit, 10000),
                "adjustment": "split",
                "sort": "desc",
                "feed": resolve_equity_data_feed(),
            }
            key = symbol.upper()

        out: list[dict] = []
        with httpx.Client(timeout=30.0, headers=headers) as client:
            pages = 0
            while pages < 10:
                pages += 1
                resp = client.get(url, params=params)
                if resp.status_code >= 400:
                    logger.debug(
                        "Alpaca bars %s %s → HTTP %s",
                        symbol,
                        wire_tf,
                        resp.status_code,
                    )
                    break
                payload = resp.json() or {}
                rows = (payload.get("bars") or {}).get(key) or []
                for bar in rows:
                    t_raw = bar.get("t")
                    try:
                        t_epoch = int(
                            datetime.fromisoformat(str(t_raw).replace("Z", "+00:00")).timestamp()
                        )
                    except Exception:
                        continue
                    # Floor to timeframe bucket for 1m; HT uses exchange bar open time.
                    if wire_tf == "1Min":
                        t_epoch = int(t_epoch // 60) * 60
                    out.append(
                        {
                            "time": t_epoch,
                            "open": float(bar.get("o") or 0),
                            "high": float(bar.get("h") or 0),
                            "low": float(bar.get("l") or 0),
                            "close": float(bar.get("c") or 0),
                            "volume": float(bar.get("v") or 0),
                        }
                    )
                token = payload.get("next_page_token")
                if not token or len(out) >= limit:
                    break
                params = {**params, "page_token": token}
        # Chart buffers expect ascending time.
        out.reverse()
        if len(out) > limit:
            out = out[-limit:]
        return out

    def _generate_fallback_candles(self, symbol) -> List[dict]:
        import random

        candles = []
        curr = int(time.time() // 60) * 60 - (100 * 60)
        p = self._symbols[symbol]["price"]
        for _ in range(100):
            ch = p * random.normalvariate(0, 0.001)
            candles.append(
                {
                    "time": curr,
                    "open": p,
                    "high": p + abs(ch),
                    "low": p - abs(ch),
                    "close": p + ch,
                    "volume": round(random.uniform(100, 1000), 2),
                }
            )
            p += ch
            curr += 60
        return candles

    def _generate_synthetic_book(self, symbol, price) -> dict:
        import random

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
            bids.append(
                [
                    round(best_bid * (1 - step), decimals),
                    round(100 * random.uniform(0.5, 2.0) * (10 - i) / 5, 2),
                ]
            )
            asks.append(
                [
                    round(best_ask * (1 + step), decimals),
                    round(100 * random.uniform(0.5, 2.0) * (10 - i) / 5, 2),
                ]
            )
        return {"bids": bids, "asks": asks}
