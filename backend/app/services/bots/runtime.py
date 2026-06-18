"""Shared bot runtime wiring for monolith, server, and worker roles."""

import asyncio
import logging
import time

from app.config import TERMINAL_MODE, TERMINAL_ROLE, REDIS_URL, BOT_SNAPSHOT_INTERVAL
from app.services.bots.bar_events import BarCloseTracker
from app.services.bots.candle_source import get_bot_candles
from app.services.bots.manager import BotManagerService
from app.services.bots.screener import MarketScreenerService
from app.services.bots.backtester import BacktesterService
from app.services.events import channels

logger = logging.getLogger(__name__)


def create_feed_and_oms():
    if TERMINAL_MODE == "LIVE_ALPACA":
        from app.services.alpaca_feed import AlpacaFeedService
        from app.services.alpaca_oms import AlpacaOMSService

        feed = AlpacaFeedService()
        oms = AlpacaOMSService(feed)
    elif TERMINAL_MODE == "LIVE_BINANCE":
        from app.services.binance_feed import BinanceFeedService
        from app.services.binance_oms import BinanceOMSService

        feed = BinanceFeedService()
        oms = BinanceOMSService(feed)
    elif TERMINAL_MODE == "LIVE_ETORO":
        from app.services.etoro_feed import EtoroFeedService
        from app.services.etoro_oms import EtoroOMSService

        feed = EtoroFeedService()
        oms = EtoroOMSService(feed)
    else:
        from app.services.sim_feed import SimulatedFeedService
        from app.services.sim_oms import SimulatedOMSService

        feed = SimulatedFeedService()
        oms = SimulatedOMSService(feed)
    return feed, oms


def create_bot_stack(broadcast_cb, oms):
    screener = MarketScreenerService()
    backtester = BacktesterService(screener)
    bot_manager = BotManagerService(oms, screener, broadcast_cb)
    return screener, backtester, bot_manager


def runs_bot_engine_inline() -> bool:
    return TERMINAL_ROLE in ("all", "worker")


def runs_bar_publisher() -> bool:
    return TERMINAL_ROLE == "server" and bool(REDIS_URL)


async def bot_market_loop(bot_manager: BotManagerService, feed):
    interval = getattr(feed, "tick_interval", 1.0) if TERMINAL_MODE == "SIMULATED" else 1.0
    logger.info("Starting bot market loop (interval=%.2fs, role=%s)...", interval, TERMINAL_ROLE)
    while True:
        try:
            if bot_manager.active_bots:
                watched = {b["symbol"] for b in bot_manager.active_bots.values()}
                for symbol in watched:
                    await bot_manager.process_market_tick(symbol, feed=feed)
        except Exception as exc:
            logger.error("Error in bot market loop: %s", exc)
        await asyncio.sleep(interval)


def _slim_bar_payload(symbol: str, candles: list) -> dict:
    """Publish only the closed bar — workers hydrate full history from feed/archive."""
    closed = candles[-2] if len(candles) >= 2 else (candles[-1] if candles else None)
    payload: dict = {"symbol": symbol}
    if closed:
        payload["bar"] = closed
        payload["bar_time"] = closed.get("time")
    return payload


async def bar_publish_loop(feed, event_bus):
    tracker = BarCloseTracker()
    interval = getattr(feed, "tick_interval", 1.0) if TERMINAL_MODE == "SIMULATED" else 1.0
    logger.info("Starting bar publish loop (interval=%.2fs)...", interval)
    while True:
        try:
            for symbol in feed.symbols:
                candles = feed.get_candles(symbol)
                if not candles or not tracker.check(symbol, candles):
                    continue
                await event_bus.publish(
                    channels.BAR_CLOSE,
                    _slim_bar_payload(symbol, candles),
                )
        except Exception as exc:
            logger.error("Error in bar publish loop: %s", exc)
        await asyncio.sleep(interval)


def register_worker_handlers(bot_manager: BotManagerService, event_bus, feed=None, oms=None, chart_analyst=None):
    async def on_bar_close(payload: dict):
        symbol = payload.get("symbol")
        if not symbol:
            return
        if feed and hasattr(feed, "sync_bar") and payload.get("bar"):
            feed.sync_bar(symbol, [payload["bar"]])
        candles = get_bot_candles(symbol, feed) if feed else payload.get("candles")
        await bot_manager.process_market_tick(symbol, ohlcv_1m=candles, feed=feed)

        if chart_analyst is not None and candles:
            try:
                await chart_analyst.analyze(symbol, candles=candles, broadcast=True)
            except Exception as exc:
                logger.debug("Worker bar-close chart analyze failed for %s: %s", symbol, exc)

    async def on_bot_reload(_payload: dict):
        bot_manager.load_bots_from_db()
        logger.info("Bot registry reloaded from DB (%d active)", len(bot_manager.active_bots))

    async def on_emergency_stop(_payload: dict):
        logger.warning("Emergency stop received via Redis — halting bots and flattening.")
        await bot_manager.stop_all_bots()
        if oms is not None:
            await oms.emergency_stop()

    async def on_tick_price(payload: dict):
        symbol = payload.get("symbol")
        price = payload.get("price")
        time_ms = payload.get("time_ms")
        if not symbol or price is None:
            return
        await bot_manager.process_price_tick(symbol, float(price), int(time_ms or time.time() * 1000))

    event_bus.subscribe(channels.BAR_CLOSE, on_bar_close)
    event_bus.subscribe(channels.BOT_RELOAD, on_bot_reload)
    event_bus.subscribe(channels.EMERGENCY_STOP, on_emergency_stop)
    event_bus.subscribe(channels.TICK_PRICE, on_tick_price)


async def worker_heartbeat_loop(redis_url: str | None = None):
    """Write heartbeat for docker healthcheck and /health worker status."""
    url = redis_url or REDIS_URL
    if not url:
        while True:
            await asyncio.sleep(3600)
        return

    import redis

    client = redis.from_url(url)
    while True:
        try:
            client.set(channels.WORKER_HEARTBEAT_KEY, str(time.time()), ex=60)
        except Exception as exc:
            logger.debug("Worker heartbeat write failed: %s", exc)
        await asyncio.sleep(10)


async def worker_keepalive():
    await worker_heartbeat_loop()


async def bot_snapshot_loop(bot_manager: BotManagerService):
    logger.info("Starting bot snapshot loop (interval=%.0fs)...", BOT_SNAPSHOT_INTERVAL)
    while True:
        try:
            if bot_manager.active_bots:
                await bot_manager.snapshot_all_bots()
        except Exception as exc:
            logger.error("Error in bot snapshot loop: %s", exc)
        await asyncio.sleep(BOT_SNAPSHOT_INTERVAL)


async def bot_reconcile_loop(bot_manager: BotManagerService, interval: float = 12.0):
    """Poll broker trade history to confirm live bot fills."""
    logger.info("Starting bot pending-fill reconcile loop (interval=%.0fs)...", interval)
    while True:
        try:
            confirmed = await bot_manager.reconcile_pending_fills()
            if confirmed:
                logger.info("Reconciled %d pending bot fill(s).", confirmed)
        except Exception as exc:
            logger.error("Error in bot reconcile loop: %s", exc)
        await asyncio.sleep(interval)
