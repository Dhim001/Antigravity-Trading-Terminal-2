import random
import math
import time
import copy
import asyncio
import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Awaitable, List

from app.config import (
    SYMBOLS,
    DEFAULT_TICK_INTERVAL,
    DEFAULT_VOLATILITY_MULTIPLIER,
    SIM_INITIAL_CANDLE_BARS,
    SIM_SBBS_WARM_PARALLEL,
)
from app.services.base_feed import BaseFeedService
from app.services.feeds.bar_close import BarCloseEmitter
from app.services.synthetic_data import SBBSGenerator

logger = logging.getLogger(__name__)


class SimulatedFeedService(BaseFeedService):
    def __init__(self, tick_interval=1.0):
        self._symbols = copy.deepcopy(SYMBOLS)
        self.tick_interval = tick_interval
        self.volatility_multiplier = DEFAULT_VOLATILITY_MULTIPLIER
        self.biases = {}  # symbol -> 'UP' | 'DOWN' | 'RANDOM'

        self.candles = {}  # symbol -> deque(maxlen=10080)
        self.order_books = {}
        self.broadcast_callback = None
        self._generators: dict = {}
        self._target_candles = {}
        self._bar_close = BarCloseEmitter()
        self._sbbs_warmed = False
        self._sbbs_warming = False

        for symbol, info in self._symbols.items():
            self.candles[symbol] = deque(
                self._generate_fallback_candles(symbol, info["price"], SIM_INITIAL_CANDLE_BARS),
                maxlen=10080,
            )
            self.order_books[symbol] = self._generate_order_book(symbol, info["price"])

        self._restore_persisted_state()

    def _restore_persisted_state(self) -> None:
        try:
            from app.services.sim_state import load_sim_market_state
            saved = load_sim_market_state()
            if not saved:
                return
            restored = 0
            for symbol, row in saved.items():
                if symbol not in self._symbols:
                    continue
                if row.get("price") is not None:
                    self._symbols[symbol]["price"] = row["price"]
                if row.get("candles"):
                    self.candles[symbol] = deque(
                        self._normalize_candles(row["candles"]), maxlen=10080,
                    )
                if row.get("target"):
                    self._target_candles[symbol] = row["target"]
                self.order_books[symbol] = self._generate_order_book(
                    symbol, self._symbols[symbol]["price"]
                )
                restored += 1
            if restored:
                print(f"Restored sim market state for {restored} symbol(s) from database.")
        except Exception as exc:
            print(f"Warning: sim state restore skipped: {exc}")

    def persist_state(self) -> None:
        try:
            from app.services.sim_state import save_sim_market_state
            save_sim_market_state(self)
        except Exception as exc:
            print(f"Warning: sim state persist failed: {exc}")

    @property
    def symbols(self) -> List[str]:
        return list(self._symbols.keys())

    async def warm_generators(self) -> None:
        """Load SBBS generators in parallel after the server is listening."""
        if self._sbbs_warmed or self._sbbs_warming:
            return
        self._sbbs_warming = True
        symbols = list(self._symbols.keys())
        workers = min(SIM_SBBS_WARM_PARALLEL, max(1, len(symbols)))
        logger.info("Warming SBBS for %d symbol(s) (%d workers)…", len(symbols), workers)

        def _load_symbol(sym: str):
            try:
                gen = SBBSGenerator(sym, defer_fetch=False)
                if gen.empirical_data is not None and not gen.empirical_data.empty:
                    return sym, gen
            except Exception as exc:
                logger.debug("SBBS warm skip %s: %s", sym, exc)
            return sym, None

        loop = asyncio.get_running_loop()
        loaded = 0

        def _warm_sync():
            nonlocal loaded
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_load_symbol, sym): sym for sym in symbols}
                for fut in as_completed(futures):
                    sym, gen = fut.result()
                    if gen is not None:
                        self._generators[sym] = gen
                        loaded += 1

        await loop.run_in_executor(None, _warm_sync)
        self._sbbs_warmed = True
        self._sbbs_warming = False
        logger.info("SBBS warm complete: %d/%d symbols ready", loaded, len(symbols))

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def subscribe(self, symbol: str) -> None:
        pass

    async def unsubscribe(self, symbol: str) -> None:
        pass

    def register_broadcast_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        self.broadcast_callback = callback

    def register_bar_close_callback(self, callback) -> None:
        self._bar_close.register(callback)

    def get_market_data(self, symbol: str) -> dict:
        if symbol not in self._symbols:
            return {}
            
        info = self._symbols[symbol]
        decimals = info["decimals"]
        
        now = time.time()
        current_minute = int(now // 60) * 60
        active_candles = self.candles[symbol]
        
        # 1. Determine if we need to draw a new target candle from SBBS for this minute
        existing_target = self._target_candles.get(symbol)
        if not existing_target or existing_target.get("minute", 0) < current_minute:
            gen = self._generators.get(symbol)
            if gen and gen.empirical_data is not None and not gen.empirical_data.empty:
                O_ret, H_ret, L_ret, C_ret, Vol = gen.get_next()
                
                # If we just rolled over, the prev_close is the last candle's close
                prev_close = active_candles[-1]["close"] if active_candles else info["price"]
                
                target_open = prev_close * (1 + O_ret)
                target_close = target_open * (1 + C_ret)
                target_high = target_open * (1 + H_ret)
                target_low = target_open * (1 + L_ret)
                
                self._target_candles[symbol] = {
                    'minute': current_minute,
                    'open': target_open,
                    'high': target_high,
                    'low': target_low,
                    'close': target_close,
                    'volume': Vol,
                    'start_price': prev_close,
                    'vol_accumulated': 0.0
                }
            else:
                # SBBS not ready — use fallback random walk; do not store None (breaks next tick)
                self._target_candles.pop(symbol, None)

        target = self._target_candles.get(symbol)
        
        if target:
            # 2. Brownian Bridge Interpolation for live ticks within the minute
            seconds_passed = now - current_minute
            progress = min(seconds_passed / 60.0, 1.0)
            
            # Linear interpolation for base path
            base_price = target['start_price'] + (target['close'] - target['start_price']) * progress
            
            # Add constrained noise (vanishes at t=0 and t=60)
            noise_scale = target['open'] * 0.001 * math.sqrt(progress * (1 - progress))
            new_price = round(base_price + random.normalvariate(0, noise_scale), decimals)
            
            # Bound the price strictly by the target high/low
            new_price = min(max(new_price, target['low']), target['high'])
            
            info["price"] = new_price
            
            # Distribute volume linearly across the minute
            target_vol = target['volume']
            vol_tick = (target_vol / (60.0 / self.tick_interval)) * random.uniform(0.5, 1.5)
            target['vol_accumulated'] += vol_tick
        else:
            # Fallback Random Walk
            prev_price = info["price"]
            vol = info["volatility"] * self.volatility_multiplier
            new_price = round(prev_price + prev_price * random.normalvariate(0, vol), decimals)
            info["price"] = new_price
            vol_tick = round(random.uniform(0.1, 1.5), 2)
        
        self.order_books[symbol] = self._generate_order_book(symbol, new_price)
        
        try:
            from app.services.archive.tick_writer import record_tick
            record_tick(symbol, new_price, volume=vol_tick)
        except Exception:
            pass
        
        if not active_candles or active_candles[-1]["time"] < current_minute:
            # Initialize the new candle — time in UNIX SECONDS
            op = target['open'] if target else new_price
            hi = target['high'] if target else max(info["price"], new_price)
            lo = target['low'] if target else min(info["price"], new_price)
            
            new_candle = {
                "time": current_minute,  # seconds
                "open": round(op, decimals),
                "high": round(hi, decimals),
                "low": round(lo, decimals),
                "close": new_price,
                "volume": round(vol_tick, 2)
            }
            active_candles.append(new_candle)  # deque(maxlen=10080) auto-drops oldest
            self._bar_close.notify(symbol)
        else:
            # Update the current candle
            candle = active_candles[-1]
            if target:
                # Let the Brownian bridge reveal the true high/low as it hits them
                candle["high"] = round(max(candle["high"], new_price), decimals)
                candle["low"] = round(min(candle["low"], new_price), decimals)
            else:
                candle["high"] = round(max(candle["high"], new_price), decimals)
                candle["low"] = round(min(candle["low"], new_price), decimals)
                
            candle["close"] = new_price
            candle["volume"] = round(candle["volume"] + vol_tick, 2)
            
            # Ensure at the end of the minute, we force it to match exactly
            if target and progress >= 0.99:
                candle["close"] = round(target["close"], decimals)
                candle["high"] = round(target["high"], decimals)
                candle["low"] = round(target["low"], decimals)
                candle["volume"] = round(target["volume"], 2)
                
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

    def get_candles(self, symbol: str) -> List[dict]:
        buf = self.candles.get(symbol)
        return list(buf) if buf else []

    def feed_lag_sec(self) -> float | None:
        """Seconds since the latest simulated 1m bar close across watched symbols."""
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

    @staticmethod
    def _normalize_candles(candles: List[dict]) -> List[dict]:
        """Align bar times to minute boundaries and merge duplicates."""
        by_time: dict = {}
        for c in candles:
            t = (int(c["time"]) // 60) * 60
            if t not in by_time:
                by_time[t] = {**c, "time": t}
                continue
            b = by_time[t]
            b["high"] = max(b["high"], c["high"])
            b["low"] = min(b["low"], c["low"])
            b["close"] = c["close"]
            b["volume"] = round(float(b.get("volume", 0)) + float(c.get("volume", 0)), 2)
        return [by_time[t] for t in sorted(by_time)]

    def _generate_fallback_candles(self, symbol, start_price, count=600):
        info = self._symbols[symbol]
        candles = []
        now_minute = (int(time.time()) // 60) * 60
        current_time = now_minute - (count * 60)
        price = start_price
        for _ in range(count):
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
                "volume": round(volume, 2),
            })
            price = close_price
            current_time += 60
        return candles

    def _generate_initial_candles(self, symbol, start_price, count=10080):
        gen = self._generators.get(symbol)
        now_minute = (int(time.time()) // 60) * 60
        if not gen or not hasattr(gen, "empirical_data") or gen.empirical_data is None or gen.empirical_data.empty:
            return self._generate_fallback_candles(symbol, start_price, min(count, SIM_INITIAL_CANDLE_BARS))

        current_close = gen.empirical_data.iloc[-1]["Close"]
        start_time = now_minute - (count * 60)
        candles = []
        for i in range(count):
            candle_time = start_time + (i * 60)
            O_ret, H_ret, L_ret, C_ret, Vol = gen.get_next()
            op = current_close * (1 + O_ret)
            hi = op * (1 + H_ret)
            lo = op * (1 + L_ret)
            cl = op * (1 + C_ret)
            candles.append({
                "time": candle_time,
                "open": round(op, 2),
                "high": round(hi, 2),
                "low": round(lo, 2),
                "close": round(cl, 2),
                "volume": round(Vol, 2),
            })
            current_close = cl
        return candles

    def _generate_order_book(self, symbol, mid_price):
        info = self._symbols[symbol]
        decimals = info["decimals"]
        spread_pct = 0.0004 if "USDT" in symbol else 0.0006
        spread = round(mid_price * spread_pct, decimals)
        if spread <= 0:
            spread = 10 ** (-decimals)
            
        best_bid = mid_price - spread / 2
        best_ask = mid_price + spread / 2
        
        bids = []
        asks = []
        base_qty = 0.5 if "BTC" in symbol else (5.0 if "ETH" in symbol else 100.0)
        
        for i in range(10):
            step_pct = 0.0003 * (i + 1)
            bid_price = round(best_bid * (1 - step_pct), decimals)
            bid_qty = round(base_qty * random.uniform(0.5, 2.5) * (10 - i) / 5.0, 4)
            
            ask_price = round(best_ask * (1 + step_pct), decimals)
            ask_qty = round(base_qty * random.uniform(0.5, 2.5) * (10 - i) / 5.0, 4)
            
            bids.append([bid_price, bid_qty])
            asks.append([ask_price, ask_qty])
            
        return {"bids": bids, "asks": asks}
