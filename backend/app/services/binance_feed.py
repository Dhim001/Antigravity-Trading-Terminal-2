import asyncio
import json
import logging
import time
from typing import Callable, Awaitable, List
import websockets
from app.config import BINANCE_WS_URL, SYMBOLS
from app.services.base_feed import BaseFeedService

class BinanceFeedService(BaseFeedService):
    def __init__(self):
        self._symbols = {k: v for k, v in SYMBOLS.items() if "USDT" in k} # crypto only
        self.candles = {sym: self._generate_fallback_candles(sym) for sym in self._symbols}
        self.order_books = {}
        self.broadcast_callback = None
        self.connection_task = None
        self.active = False

        for sym, info in self._symbols.items():
            self.order_books[sym] = self._generate_synthetic_book(sym, info["price"])

    @property
    def symbols(self) -> List[str]:
        return list(self._symbols.keys())

    def register_broadcast_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        self.broadcast_callback = callback

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
        self.active = True
        self.connection_task = asyncio.create_task(self._ws_loop())
        logging.info("Binance feed stream task started.")

    async def stop(self) -> None:
        self.active = False
        if self.connection_task:
            self.connection_task.cancel()
            try:
                await self.connection_task
            except asyncio.CancelledError:
                pass
            logging.info("Binance feed stream stopped.")

    async def subscribe(self, symbol: str) -> None:
        pass

    async def unsubscribe(self, symbol: str) -> None:
        pass

    async def _ws_loop(self):
        # Listen to combined streams (kline_1m and depth20) for all configured symbols
        streams = []
        for symbol in self.symbols:
            sym_lower = symbol.lower()
            streams.append(f"{sym_lower}@kline_1m")
            streams.append(f"{sym_lower}@depth20@100ms")
        url = f"{BINANCE_WS_URL}/stream?streams=" + "/".join(streams)
        
        while self.active:
            try:
                async with websockets.connect(url) as ws:
                    logging.info("Binance combined stream WebSocket connected.")
                    async for msg_str in ws:
                        if not self.active:
                            break
                        msg = json.loads(msg_str)
                        stream_name = msg.get("stream")
                        data = msg.get("data")
                        
                        symbol = stream_name.split("@")[0].upper()
                        if symbol not in self._symbols:
                            continue
                            
                        updates = {}
                        
                        if "@kline" in stream_name:
                            k = data.get("k", {})
                            close_price = float(k.get("c"))
                            self._symbols[symbol]["price"] = close_price
                            
                            t_epoch = int(k.get("t") // 1000)
                            active_candles = self.candles[symbol]
                            new_candle = {
                                "time": t_epoch,
                                "open": float(k.get("o")),
                                "high": float(k.get("h")),
                                "low": float(k.get("l")),
                                "close": close_price,
                                "volume": round(float(k.get("v", 0)), 4)
                            }
                            
                            if active_candles and active_candles[-1]["time"] == t_epoch:
                                active_candles[-1] = new_candle
                            else:
                                active_candles.append(new_candle)
                                if len(active_candles) > 500:
                                    active_candles.pop(0)
                                    
                            updates[symbol] = self.get_market_data(symbol)
                            
                        elif "@depth" in stream_name:
                            # Map bid/ask values
                            bids = [[float(b[0]), float(b[1])] for b in data.get("bids", [])]
                            asks = [[float(a[0]), float(a[1])] for a in data.get("asks", [])]
                            self.order_books[symbol] = {"bids": bids, "asks": asks}
                            updates[symbol] = self.get_market_data(symbol)
                            
                        if updates and self.broadcast_callback:
                            await self.broadcast_callback({
                                "type": "market_update",
                                "data": updates
                            })
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Binance feed connection error: {str(e)}. Reconnecting in 5s.")
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
                "low": p - abs(ch), "close": p + ch, "volume": round(random.uniform(1, 15), 4)
            })
            p += ch
            curr += 60
        return candles

    def _generate_synthetic_book(self, symbol, price) -> dict:
        import random
        decimals = self._symbols[symbol]["decimals"]
        spread = round(price * 0.0004, decimals)
        if spread <= 0: spread = 10**(-decimals)
        best_bid = price - spread/2
        best_ask = price + spread/2
        bids = []
        asks = []
        base_qty = 0.2 if symbol == "BTCUSDT" else 2.0
        for i in range(10):
            step = 0.0002 * (i + 1)
            bids.append([round(best_bid*(1-step), decimals), round(base_qty * random.uniform(0.5, 2.0)*(10-i)/5, 4)])
            asks.append([round(best_ask*(1+step), decimals), round(base_qty * random.uniform(0.5, 2.0)*(10-i)/5, 4)])
        return {"bids": bids, "asks": asks}
