"""Interactive Brokers market-data feed (feed-only — requires TWS or IB Gateway).

Uses ``ib_async`` to stream 1-minute bars via ``reqHistoricalData(..., keepUpToDate=True)``.
Execution remains on ``SimulatedOMSService`` until ``ib_oms`` is implemented.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Awaitable, Callable, List

from app.api.outbound import publish_market_update
from app.config import (
    IB_CLIENT_ID,
    IB_HIST_DURATION,
    IB_HOST,
    IB_MARKET_DATA_TYPE,
    IB_PORT,
    IB_STREAM_STAGGER_SEC,
    IB_USE_RTH,
    SYMBOLS,
)
from app.services.base_feed import BaseFeedService
from app.services.feeds.bar_close import BarCloseEmitter
from app.services.ib_bars import bars_to_candles
from app.services.ib_contracts import cache_contract, stock_contract

logger = logging.getLogger(__name__)

MAX_CANDLES = 600


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
        }

    def register_broadcast_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        self.broadcast_callback = callback

    def register_bar_close_callback(self, callback) -> None:
        self._bar_close.register(callback)

    def get_candles(self, symbol: str) -> List[dict]:
        return list(self.candles.get(symbol, []))

    def sync_bar(self, symbol: str, candles: list) -> None:
        """Hydrate worker stub from a single closed bar (distributed mode)."""
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
        latest = active_candles[-1] if active_candles else {}
        price = float(info["price"])
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
                logger.error("IB feed error: %s — reconnecting in 15s", exc)
                await asyncio.sleep(15)

    async def _connect_and_stream(self) -> None:
        from ib_async import IB

        ib = IB()
        self._ib = ib
        await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID, timeout=20)
        self._connected = True
        self._last_error = None
        ib.reqMarketDataType(IB_MARKET_DATA_TYPE)
        logger.info("IB connected — subscribing to %d symbols", len(self.symbols))

        self._streams_active = 0
        for symbol in self.symbols:
            if not self.active:
                break
            try:
                await self._subscribe_symbol(ib, symbol)
                self._streams_active += 1
            except Exception as exc:
                logger.warning("IB subscribe failed for %s: %s", symbol, exc)
            await asyncio.sleep(IB_STREAM_STAGGER_SEC)

        while self.active and ib.isConnected():
            await asyncio.sleep(1)

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

    def _apply_bar_list(self, symbol: str, bars, *, has_new_bar: bool) -> None:
        if not bars:
            return
        normalized = bars_to_candles(bars)
        if not normalized:
            return

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

    def _schedule_broadcast(self, symbol: str) -> None:
        if not self.broadcast_callback:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._broadcast_symbol(symbol))

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
