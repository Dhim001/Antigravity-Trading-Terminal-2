"""Interactive Brokers market-data feed (requires TWS or IB Gateway).

Uses ``ib_async`` for 1-minute ``keepUpToDate`` bars and optional L1 ``reqMktData`` ticks.
Order execution is handled by ``ib_oms`` when ``IB_OMS_ENABLED=true``.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Awaitable, Callable, List

from app.api.outbound import publish_market_update
from app.config import (
    IB_AUTO_DELAYED_FALLBACK,
    IB_CLIENT_ID,
    IB_HIST_DURATION,
    IB_HOST,
    IB_L1_TICKS_ENABLED,
    IB_MARKET_DATA_TYPE,
    IB_PACING_PAUSE_SEC,
    IB_PORT,
    IB_STREAM_STAGGER_SEC,
    IB_USE_RTH,
    SYMBOLS,
)
from app.observability.metrics import inc
from app.services.base_feed import BaseFeedService
from app.services.feeds.bar_close import BarCloseEmitter
from app.services.ib_bars import bars_to_candles
from app.services.ib_contracts import cache_contract, stock_contract

logger = logging.getLogger(__name__)

MAX_CANDLES = 600
# IB error codes that trigger delayed-data fallback or pacing pause.
_PACING_ERROR_CODES = {162}
_DELAYED_FALLBACK_CODES = {10167, 354, 10089, 10213}
_RECONNECT_ERROR_CODES = {1100, 1300}


class IbFeedService(BaseFeedService):
    """Live US equity candles from IB Gateway / TWS."""

    def __init__(self) -> None:
        self._symbols = {k: v for k, v in SYMBOLS.items() if "USDT" not in k}
        self.candles: dict[str, list[dict]] = {
            sym: self._seed_candles(sym, info["price"]) for sym, info in self._symbols.items()
        }
        self.order_books: dict[str, dict] = {}
        self.broadcast_callback: Callable[[dict], Awaitable[None]] | None = None
        self.active = False
        self._stream_task: asyncio.Task | None = None
        self._ib = None
        self._bar_close = BarCloseEmitter()
        self._connected = False
        self._last_error: str | None = None
        self._streams_active = 0
        self._market_data_type = IB_MARKET_DATA_TYPE
        self._market_data_delayed = IB_MARKET_DATA_TYPE != 1
        self._pacing_paused_until = 0.0
        self._l1_tickers: dict[str, object] = {}
        self._reconnect_count = 0
        self._event_loop: asyncio.AbstractEventLoop | None = None

        for sym, info in self._symbols.items():
            self.order_books[sym] = self._synthetic_book(sym, info["price"])

    @property
    def symbols(self) -> List[str]:
        return list(self._symbols.keys())

    @property
    def ib_status(self) -> dict:
        return {
            "connected": self._connected,
            "streams_active": self._streams_active,
            "last_error": self._last_error,
            "market_data_type": self._market_data_type,
            "market_data_delayed": self._market_data_delayed,
            "l1_ticks_enabled": IB_L1_TICKS_ENABLED,
            "pacing_paused": self._pacing_paused_until > time.time(),
            "pacing_paused_until": (
                round(self._pacing_paused_until, 1) if self._pacing_paused_until > time.time() else None
            ),
            "reconnects": self._reconnect_count,
        }

    def register_broadcast_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        self.broadcast_callback = callback

    def register_bar_close_callback(self, callback) -> None:
        self._bar_close.register(callback)

    def get_candles(self, symbol: str) -> List[dict]:
        return list(self.candles.get(symbol, []))

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
        latest: int | None = None
        for sym in self.symbols:
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
        latest = self._live_candle_snapshot(symbol) if active_candles else {}
        if active_candles:
            first = active_candles[0]["close"]
            change = round((price - first) / first * 100, 2) if first else 0.0
            vol = sum(float(c.get("volume") or 0) for c in active_candles)
            hi = max(float(c["high"]) for c in active_candles)
            lo = min(float(c["low"]) for c in active_candles)
        else:
            change, vol, hi, lo = 0.0, 0.0, price, price
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
        self.active = True
        self._event_loop = asyncio.get_running_loop()
        self._stream_task = asyncio.create_task(self._run_loop())
        logger.info(
            "IB feed starting (host=%s port=%s clientId=%s, %d symbols)",
            IB_HOST,
            IB_PORT,
            IB_CLIENT_ID,
            len(self._symbols),
        )

    async def stop(self) -> None:
        self.active = False
        self._cancel_l1_tickers()
        if self._ib is not None:
            try:
                self._ib.disconnect()
            except Exception:
                pass
            self._ib = None
        self._connected = False
        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
        logger.info("IB feed stopped.")

    async def subscribe(self, symbol: str) -> None:
        pass

    async def unsubscribe(self, symbol: str) -> None:
        pass

    async def _run_loop(self) -> None:
        while self.active:
            try:
                await self._connect_and_stream()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._last_error = str(exc)
                self._connected = False
                self._reconnect_count += 1
                inc("ib_reconnects_total")
                logger.error("IB feed error: %s - reconnecting in 15s", exc)
                await asyncio.sleep(15)

    def _cancel_l1_tickers(self) -> None:
        ib = self._ib
        if ib is None:
            self._l1_tickers.clear()
            return
        for sym, ticker in list(self._l1_tickers.items()):
            try:
                contract = getattr(ticker, "contract", None)
                if contract is not None:
                    ib.cancelMktData(contract)
            except Exception:
                pass
        self._l1_tickers.clear()

    def _on_ib_error(self, req_id: int, error_code: int, error_string: str, contract) -> None:
        code = int(error_code)
        if code in _PACING_ERROR_CODES:
            self._pacing_paused_until = time.time() + IB_PACING_PAUSE_SEC
            self._last_error = f"pacing violation ({code}): {error_string}"
            inc("ib_stream_errors_total", labels={"code": str(code)})
            logger.warning(
                "IB pacing violation - pausing new subscriptions for %ss",
                int(IB_PACING_PAUSE_SEC),
            )
            return
        if code in _DELAYED_FALLBACK_CODES and IB_AUTO_DELAYED_FALLBACK and self._market_data_type == 1:
            self._apply_delayed_fallback()
            return
        if code in _RECONNECT_ERROR_CODES:
            inc("ib_stream_errors_total", labels={"code": str(code)})
            logger.warning("IB connectivity error %s: %s", code, error_string)
            return
        if code >= 2000:
            return
        if code not in (2104, 2106, 2158, 321):
            inc("ib_stream_errors_total", labels={"code": str(code)})

    def _apply_delayed_fallback(self) -> None:
        if self._market_data_type == 3:
            return
        ib = self._ib
        if ib is None:
            return
        try:
            ib.reqMarketDataType(3)
            self._market_data_type = 3
            self._market_data_delayed = True
            logger.warning("IB live market data unavailable - switched to delayed (type 3)")
        except Exception as exc:
            logger.warning("IB delayed fallback failed: %s", exc)

    async def _connect_and_stream(self) -> None:
        from ib_async import IB

        ib = IB()
        self._ib = ib
        ib.errorEvent += self._on_ib_error
        await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID, timeout=20)
        self._connected = True
        self._last_error = None
        self._market_data_type = IB_MARKET_DATA_TYPE
        self._market_data_delayed = IB_MARKET_DATA_TYPE != 1
        ib.reqMarketDataType(self._market_data_type)
        logger.info(
            "IB connected - subscribing to %d symbols (md_type=%s)",
            len(self.symbols),
            self._market_data_type,
        )

        self._streams_active = 0
        for symbol in self.symbols:
            if not self.active:
                break
            if self._pacing_paused_until > time.time():
                wait = self._pacing_paused_until - time.time()
                logger.info("IB pacing pause active - waiting %.0fs", wait)
                await asyncio.sleep(min(wait, 60))
                continue
            try:
                await self._subscribe_symbol(ib, symbol)
                self._streams_active += 1
            except Exception as exc:
                logger.warning("IB subscribe failed for %s: %s", symbol, exc)
                inc("ib_stream_errors_total", labels={"code": "subscribe"})
            await asyncio.sleep(IB_STREAM_STAGGER_SEC)

        while self.active and ib.isConnected():
            try:
                await asyncio.wait_for(ib.updateEvent, timeout=1.0)
            except asyncio.TimeoutError:
                pass

        self._cancel_l1_tickers()
        if ib.isConnected():
            ib.disconnect()
        self._connected = False
        self._ib = None

    async def _subscribe_symbol(self, ib, symbol: str) -> None:
        contract = stock_contract(symbol)
        qualified = await ib.qualifyContractsAsync(contract)
        if not qualified:
            raise ValueError(f"Could not qualify contract for {symbol}")
        resolved = qualified[0]
        cache_contract(symbol, resolved)

        bars = await ib.reqHistoricalDataAsync(
            resolved,
            endDateTime="",
            durationStr=IB_HIST_DURATION,
            barSizeSetting="1 min",
            whatToShow="TRADES",
            useRTH=IB_USE_RTH,
            formatDate=1,
            keepUpToDate=True,
        )
        if bars:
            self._apply_bar_list(symbol, bars, has_new_bar=False)

        def _on_update(bars_list, has_new_bar: bool, sym=symbol) -> None:
            try:
                self._apply_bar_list(sym, bars_list, has_new_bar=has_new_bar)
            except Exception as exc:
                logger.debug("IB bar update handler error for %s: %s", sym, exc)

        bars.updateEvent += _on_update

        if IB_L1_TICKS_ENABLED:
            await self._subscribe_l1(ib, symbol, resolved)

    async def _subscribe_l1(self, ib, symbol: str, contract) -> None:
        try:
            ticker = ib.reqMktData(contract, "", False, False)
            self._l1_tickers[symbol] = ticker

            def _on_tick(ticker_obj, sym=symbol) -> None:
                try:
                    last = float(getattr(ticker_obj, "last", 0) or 0)
                    if last <= 0:
                        bid = float(getattr(ticker_obj, "bid", 0) or 0)
                        ask = float(getattr(ticker_obj, "ask", 0) or 0)
                        if bid > 0 and ask > 0:
                            last = (bid + ask) / 2.0
                    if last <= 0:
                        return
                    if sym not in self._symbols:
                        return
                    prev = float(self._symbols[sym]["price"])
                    if abs(last - prev) < 1e-9:
                        return
                    self._symbols[sym]["price"] = last
                    self._patch_forming_candle(sym, last)
                    bid = float(getattr(ticker_obj, "bid", 0) or 0)
                    ask = float(getattr(ticker_obj, "ask", 0) or 0)
                    if bid > 0 and ask > 0:
                        self.order_books[sym] = self._book_from_bid_ask(sym, bid, ask)
                    else:
                        self.order_books[sym] = self._synthetic_book(sym, last)
                    inc("ib_l1_ticks_total")
                    self._schedule_broadcast(sym)
                except Exception as exc:
                    logger.debug("IB L1 tick handler error for %s: %s", sym, exc)

            ticker.updateEvent += _on_tick
        except Exception as exc:
            logger.debug("IB L1 subscribe failed for %s: %s", symbol, exc)

    def _apply_bar_list(self, symbol: str, bars, *, has_new_bar: bool) -> None:
        if not bars:
            return
        normalized = bars_to_candles(bars)
        if not normalized:
            return

        inc("ib_bars_received_total")

        prev_len = len(self.candles.get(symbol, []))
        prev_last_time = self.candles[symbol][-1]["time"] if self.candles.get(symbol) else None

        merged = normalized[-MAX_CANDLES:]
        self.candles[symbol] = merged

        last = merged[-1]
        close = float(last["close"])
        self._symbols[symbol]["price"] = close
        self.order_books[symbol] = self._synthetic_book(symbol, close)

        try:
            from app.config import ARCHIVE_TICKS_ENABLED

            if ARCHIVE_TICKS_ENABLED:
                from app.services.archive.tick_writer import record_tick

                record_tick(symbol, close, volume=float(last.get("volume") or 0))
        except Exception:
            pass

        new_bar_closed = has_new_bar
        if not new_bar_closed and prev_last_time is not None and last["time"] != prev_last_time:
            new_bar_closed = True
        if not new_bar_closed and len(merged) > prev_len:
            new_bar_closed = True

        if new_bar_closed:
            self._bar_close.notify(symbol)

        self._schedule_broadcast(symbol)

    def _patch_forming_candle(self, symbol: str, price: float) -> None:
        """Merge a live L1 price into the current 1m bar buffer."""
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
        """Return the forming bar with the latest L1 price applied (read-only)."""
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

    def _schedule_broadcast(self, symbol: str) -> None:
        if not self.broadcast_callback:
            return
        loop = self._event_loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
        loop.call_soon_threadsafe(
            lambda s=symbol, lp=loop: lp.create_task(self._broadcast_symbol(s))
        )

    async def _broadcast_symbol(self, symbol: str) -> None:
        if not self.broadcast_callback:
            return
        await publish_market_update(
            self.broadcast_callback,
            {symbol: self.get_market_data(symbol)},
        )

    def _seed_candles(self, symbol: str, start_price: float) -> list[dict]:
        candles = []
        curr = int(time.time() // 60) * 60 - (100 * 60)
        price = float(start_price)
        decimals = self._symbols[symbol]["decimals"]
        for _ in range(100):
            ch = price * random.normalvariate(0, 0.001)
            candles.append({
                "time": curr,
                "open": round(price, decimals),
                "high": round(price + abs(ch), decimals),
                "low": round(price - abs(ch), decimals),
                "close": round(price + ch, decimals),
                "volume": round(random.uniform(100, 1000), 2),
            })
            price += ch
            curr += 60
        return candles

    def _book_from_bid_ask(self, symbol: str, bid: float, ask: float) -> dict:
        decimals = self._symbols[symbol]["decimals"]
        spread = max(ask - bid, 10 ** (-decimals))
        bids = []
        asks = []
        for i in range(10):
            step = spread * 0.15 * (i + 1)
            bids.append([round(bid - step, decimals), round(100 * (10 - i) / 10, 2)])
            asks.append([round(ask + step, decimals), round(100 * (10 - i) / 10, 2)])
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
