import random
import time
import copy
from app.config import SYMBOLS, DEFAULT_TICK_INTERVAL, DEFAULT_VOLATILITY_MULTIPLIER

class FeedSimulator:
    def __init__(self):
        # Deep copy initial prices and parameters for the supported symbols from configuration
        self.symbols = copy.deepcopy(SYMBOLS)
        self.tick_interval = DEFAULT_TICK_INTERVAL
        self.volatility_multiplier = DEFAULT_VOLATILITY_MULTIPLIER
        self.biases = {} # symbol -> 'UP' | 'DOWN' | 'RANDOM'
        
        # Historical candle state for each symbol (storing 1-minute candles)
        self.candles = {}
        # Live order books cache
        self.order_books = {}
        
        for symbol, info in self.symbols.items():
            self.candles[symbol] = self._generate_initial_candles(symbol, info["price"])
            self.order_books[symbol] = self._generate_order_book(symbol, info["price"])

    def _generate_initial_candles(self, symbol, start_price):
        """Generates 100 historical 1-minute candles to pre-populate the chart."""
        candles = []
        current_time = int(time.time()) - (100 * 60)
        price = start_price
        info = self.symbols[symbol]
        
        for i in range(100):
            # Simulating price change
            change = price * random.normalvariate(0, info["volatility"] * 10)
            open_price = price
            close_price = price + change
            high_price = max(open_price, close_price) + abs(random.normalvariate(0, info["volatility"] * 5))
            low_price = min(open_price, close_price) - abs(random.normalvariate(0, info["volatility"] * 5))
            volume = random.uniform(10, 100) if "USDT" in symbol else random.uniform(100, 1000)
            
            candles.append({
                "time": current_time,
                "open": round(open_price, info["decimals"]),
                "high": round(high_price, info["decimals"]),
                "low": round(low_price, info["decimals"]),
                "close": round(close_price, info["decimals"]),
                "volume": round(volume, 2)
            })
            price = close_price
            current_time += 60
            
        return candles

    def _generate_order_book(self, symbol, mid_price):
        """Generates a realistic 10-level Level 2 order book."""
        info = self.symbols[symbol]
        decimals = info["decimals"]
        
        # Spread is typically smaller for liquid assets
        spread_pct = 0.0004 if "USDT" in symbol else 0.0006
        spread = round(mid_price * spread_pct, decimals)
        if spread <= 0:
            spread = 10 ** (-decimals)
            
        best_bid = mid_price - spread / 2
        best_ask = mid_price + spread / 2
        
        bids = []
        asks = []
        
        # Base quantity depends on assets (cryptos have smaller qty per level, stocks have round lots)
        base_qty = 0.5 if "BTC" in symbol else (5.0 if "ETH" in symbol else 100.0)
        
        for i in range(10):
            # Bid prices decrease by step size
            step_pct = 0.0003 * (i + 1)
            bid_price = round(best_bid * (1 - step_pct), decimals)
            bid_qty = round(base_qty * random.uniform(0.5, 2.5) * (10 - i) / 5.0, 4)
            
            # Ask prices increase by step size
            ask_price = round(best_ask * (1 + step_pct), decimals)
            ask_qty = round(base_qty * random.uniform(0.5, 2.5) * (10 - i) / 5.0, 4)
            
            bids.append([bid_price, bid_qty])
            asks.append([ask_price, ask_qty])
            
        return {"bids": bids, "asks": asks}

    def get_market_data(self, symbol):
        """Update and return the ticker, L2 order book, and current candle for a symbol."""
        if symbol not in self.symbols:
            return None
            
        info = self.symbols[symbol]
        decimals = info["decimals"]
        
        # Update price using Random Walk with Bias and Volatility Multiplier
        prev_price = info["price"]
        bias = self.biases.get(symbol, 'RANDOM')
        vol = info["volatility"] * self.volatility_multiplier
        
        if bias == 'UP':
            # Force positive change with upward drift
            change = prev_price * abs(random.normalvariate(0.0003, vol))
        elif bias == 'DOWN':
            # Force negative change with downward drift
            change = -prev_price * abs(random.normalvariate(0.0003, vol))
        else:
            # Standard random walk
            change = prev_price * random.normalvariate(0, vol)
            
        new_price = round(prev_price + change, decimals)
        info["price"] = new_price
        
        # Update Order Book based on new price
        self.order_books[symbol] = self._generate_order_book(symbol, new_price)
        
        # Update current active candle
        active_candles = self.candles[symbol]
        current_minute = int(time.time() // 60) * 60
        
        if not active_candles or active_candles[-1]["time"] < current_minute:
            # Create a new candle
            new_candle = {
                "time": current_minute,
                "open": prev_price,
                "high": max(prev_price, new_price),
                "low": min(prev_price, new_price),
                "close": new_price,
                "volume": round(random.uniform(1, 10), 2)
            }
            active_candles.append(new_candle)
            # Cap history to 500 candles
            if len(active_candles) > 500:
                active_candles.pop(0)
        else:
            # Update existing candle
            candle = active_candles[-1]
            candle["high"] = round(max(candle["high"], new_price), decimals)
            candle["low"] = round(min(candle["low"], new_price), decimals)
            candle["close"] = new_price
            candle["volume"] = round(candle["volume"] + random.uniform(0.1, 1.5), 2)
            
        return {
            "symbol": symbol,
            "price": new_price,
            "change_24h": round((new_price - active_candles[0]["close"]) / active_candles[0]["close"] * 100, 2),
            "volume_24h": sum(c["volume"] for c in active_candles),
            "high_24h": max(c["high"] for c in active_candles),
            "low_24h": min(c["low"] for c in active_candles),
            "orderbook": self.order_books[symbol],
            "candle": active_candles[-1]
        }
