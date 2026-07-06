import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Callable, Awaitable, List, Dict
import websockets
from app.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BROADCAST_INTERVAL_SEC, SYMBOLS
from app.services.alpaca_data import fallback_to_iex, get_alpaca_ws_url, is_sip_entitlement_error
from app.api.outbound import publish_market_update
from app.services.base_feed import BaseFeedService
from app.services.feeds.bar_close import BarCloseEmitter

class AlpacaFeedService(BaseFeedService):
    def __init__(self):
        self._symbols = {k: v for k, v in SYMBOLS.items() if "USDT" not in k} # equities only
        self.candles = {sym: self._generate_fallback_candles(sym) for sym in self._symbols}
        self.order_books = {}
        self.broadcast_callback = None
        self.connection_task = None
        self.active = False
        self._bar_close = BarCloseEmitter()
        self._ws_url = ""
        self._pending_updates: Dict[str, dict] = {}
        self._broadcast_task = None
        for sym, info in self._symbols.items():
            self.order_books[sym] = self._generate_synthetic_book(sym, info["price"])

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
        active_candles = self.candles.get(symbol, [])
        latest_candle = active_candles[-1] if active_candles else {}
        
        return {
            "symbol": symbol,
            "price": info["price"],
            "change_24h": round((info["price"] - active_candles[0]["close"]) / active_candles[0]["close"] * 100, 2) if active_candles else 0.0,
            "volume_24h": sum(c["volume"] for c in active_candles) if active_candles else 0.0,
            "high_24h": max(c["high"] for c in active_candles) if active_candles else info["price"],
            "low_24h": min(c["low"] for c in active_candles) if active_candles else info["price"],
            "orderbook": self.order_books.get(symbol, self._generate_synthetic_book(symbol, info["price"])),
            "candle": latest_candle
        }

    async def start(self) -> None:
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            logging.warning("Alpaca API credentials missing. Feed will run in simulated mode for equities.")
            self._start_simulated_fallback()
            return
            
        self.active = True
        self._ws_url = get_alpaca_ws_url()
        self.connection_task = asyncio.create_task(self._ws_loop())
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        logging.info("Alpaca feed stream task started (ws=%s).", self._ws_url)

    async def stop(self) -> None:
        self.active = False
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
            self._broadcast_task = None
        if self.connection_task:
            self.connection_task.cancel()
            try:
                await self.connection_task
            except asyncio.CancelledError:
                pass
            logging.info("Alpaca feed stream stopped.")

    async def subscribe(self, symbol: str) -> None:
        pass

    async def unsubscribe(self, symbol: str) -> None:
        pass

    def _start_simulated_fallback(self):
        """Starts a background loop to generate simulated ticks if API keys are absent."""
        self.active = True
        async def fallback_loop():
            import random
            while self.active:
                for sym, info in self._symbols.items():
                    # Random walk
                    prev_price = info["price"]
                    change = prev_price * random.normalvariate(0, info["volatility"])
                    new_price = round(prev_price + change, info["decimals"])
                    info["price"] = new_price
                    
                    # Update book
                    self.order_books[sym] = self._generate_synthetic_book(sym, new_price)
                    
                    # Update candle
                    active_candles = self.candles[sym]
                    curr_min = int(time.time() // 60) * 60
                    if not active_candles or active_candles[-1]["time"] < curr_min:
                        active_candles.append({
                            "time": curr_min, "open": prev_price, "high": max(prev_price, new_price),
                            "low": min(prev_price, new_price), "close": new_price, "volume": round(random.uniform(50, 500), 2)
                        })
                        if len(active_candles) > 500: active_candles.pop(0)
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
                
        self.connection_task = asyncio.create_task(fallback_loop())

    async def _broadcast_loop(self) -> None:
        """Coalesce per-tick WS updates into periodic broadcasts (like Massive loop)."""
        interval = max(0.5, float(ALPACA_BROADCAST_INTERVAL_SEC))
        while self.active:
            try:
                await asyncio.sleep(interval)
                if not self.broadcast_callback or not self._pending_updates:
                    continue
                batch = self._pending_updates
                self._pending_updates = {}
                await publish_market_update(self.broadcast_callback, batch)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logging.error("Alpaca broadcast loop error: %s", exc)

    def _queue_market_broadcast(self, symbol: str) -> None:
        if symbol in self._symbols:
            self._pending_updates[symbol] = self.get_market_data(symbol)

    async def _ws_loop(self):
        while self.active:
            try:
                async with websockets.connect(self._ws_url) as ws:
                    # 1. Expect welcome message
                    welcome = await ws.recv()
                    logging.info("Alpaca stream connected (%s): %s", self._ws_url, welcome)
                    
                    # 2. Authenticate
                    auth_msg = {
                        "action": "auth",
                        "key": ALPACA_API_KEY,
                        "secret": ALPACA_SECRET_KEY
                    }
                    await ws.send(json.dumps(auth_msg))
                    auth_resp = await ws.recv()
                    logging.info(f"Alpaca auth response: {auth_resp}")
                    
                    auth_data = json.loads(auth_resp)
                    first = auth_data[0] if auth_data else {}
                    if first.get("msg") != "authenticated":
                        if first.get("T") == "error" and (
                            first.get("code") == 409
                            or is_sip_entitlement_error(0, first.get("msg", ""))
                        ):
                            _, self._ws_url = fallback_to_iex()
                            await asyncio.sleep(1)
                            continue
                        logging.error("Alpaca authentication failed. Sleeping and retrying.")
                        await asyncio.sleep(5)
                        continue
                    
                    # 3. Subscribe to 1-minute bars & trades
                    sub_msg = {
                        "action": "subscribe",
                        "bars": list(self._symbols.keys()),
                        "trades": list(self._symbols.keys())
                    }
                    await ws.send(json.dumps(sub_msg))
                    sub_resp = await ws.recv()
                    logging.info(f"Alpaca subscription response: {sub_resp}")
                    
                    # 4. Message loop
                    async for msg_str in ws:
                        if not self.active:
                            break
                        msgs = json.loads(msg_str)
                        for m in msgs:
                            stream_type = m.get("T")
                            symbol = m.get("S")
                            if symbol not in self._symbols:
                                continue
                                
                            if stream_type == "t": # trade execution
                                price = m.get("p")
                                self._symbols[symbol]["price"] = price
                                try:
                                    from app.services.archive.tick_writer import record_tick
                                    record_tick(symbol, float(price), volume=float(m.get("s", 0) or 0))
                                except Exception:
                                    pass
                                self.order_books[symbol] = self._generate_synthetic_book(symbol, price)
                                
                            elif stream_type == "b": # minute bar
                                t_str = m.get("t")
                                try:
                                    t_epoch = int(datetime.fromisoformat(t_str.replace("Z", "+00:00")).timestamp())
                                except Exception:
                                    t_epoch = int(time.time() // 60) * 60
                                    
                                active_candles = self.candles[symbol]
                                new_candle = {
                                    "time": t_epoch,
                                    "open": m.get("o"),
                                    "high": m.get("h"),
                                    "low": m.get("l"),
                                    "close": m.get("c"),
                                    "volume": round(m.get("v", 0), 2)
                                }
                                
                                # Sync update or append
                                if active_candles and active_candles[-1]["time"] == t_epoch:
                                    active_candles[-1] = new_candle
                                else:
                                    active_candles.append(new_candle)
                                    if len(active_candles) > 500:
                                        active_candles.pop(0)
                                    self._bar_close.notify(symbol)
                                        
                            if stream_type in ("t", "b"):
                                self._queue_market_broadcast(symbol)
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Alpaca feed connection error: {str(e)}. Reconnecting in 5s.")
                await asyncio.sleep(5)

    def _generate_fallback_candles(self, symbol) -> List[dict]:
        candles = []
        curr = int(time.time() // 60) * 60 - (100 * 60)
        p = self._symbols[symbol]["price"]
        for i in range(100):
            import random
            ch = p * random.normalvariate(0, 0.001)
            candles.append({
                "time": curr, "open": p, "high": p + abs(ch),
                "low": p - abs(ch), "close": p + ch, "volume": round(random.uniform(100, 1000), 2)
            })
            p += ch
            curr += 60
        return candles

    def _generate_synthetic_book(self, symbol, price) -> dict:
        import random
        decimals = self._symbols[symbol]["decimals"]
        spread = round(price * 0.0005, decimals)
        if spread <= 0: spread = 10**(-decimals)
        best_bid = price - spread/2
        best_ask = price + spread/2
        bids = []
        asks = []
        for i in range(10):
            step = 0.0003 * (i + 1)
            bids.append([round(best_bid*(1-step), decimals), round(100 * random.uniform(0.5, 2.0)*(10-i)/5, 2)])
            asks.append([round(best_ask*(1+step), decimals), round(100 * random.uniform(0.5, 2.0)*(10-i)/5, 2)])
        return {"bids": bids, "asks": asks}
