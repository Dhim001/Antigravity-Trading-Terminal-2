"""Minimal feed for bot worker processes — prices synced from bar-close events."""

from app.config import SYMBOLS


class CandleFeedStub:
    def __init__(self):
        self.symbols = list(SYMBOLS.keys())
        self._symbols = {
            sym: {
                "price": info["price"],
                "asset": info["asset"],
                "quote": info["quote"],
            }
            for sym, info in SYMBOLS.items()
        }
        self._candles: dict[str, list] = {}
        self.tick_interval = 1.0
        self.volatility_multiplier = 1.0

    async def start(self):
        return

    def sync_bar(self, symbol: str, candles: list):
        if len(candles) == 1:
            bar = candles[0]
            buf = list(self._candles.get(symbol, []))
            if buf and buf[-1].get("time") == bar.get("time"):
                buf[-1] = bar
            elif not buf or buf[-1].get("time") < bar.get("time"):
                buf.append(bar)
            self._candles[symbol] = buf
        else:
            self._candles[symbol] = candles
        merged = self._candles.get(symbol, [])
        if merged:
            self._symbols[symbol]["price"] = merged[-1]["close"]

    def get_candles(self, symbol: str):
        return self._candles.get(symbol, [])

    def get_market_data(self, symbol: str):
        price = self._symbols[symbol]["price"]
        return {"symbol": symbol, "price": price, "change_24h": 0, "volume_24h": 0}
