"""Shared bot runtime wiring for monolith, server, and worker roles."""

import asyncio
import logging

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
                    candles = get_bot_candles(symbol, feed)
                    if candles:
                        await bot_manager.process_market_tick(symbol, candles)
        except Exception as exc:
            logger.error("Error in bot market loop: %s", exc)
        await asyncio.sleep(interval)


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
                    {"symbol": symbol, "candles": candles},
                )
        except Exception as exc:
            logger.error("Error in bar publish loop: %s", exc)
        await asyncio.sleep(interval)


def register_worker_handlers(bot_manager: BotManagerService, event_bus, feed=None):
    async def on_bar_close(payload: dict):
        symbol = payload.get("symbol")
        if not symbol:
            return
        candles = get_bot_candles(symbol, feed) if feed else payload.get("candles")
        if candles:
            if feed and hasattr(feed, "sync_bar"):
                feed.sync_bar(symbol, candles)
            await bot_manager.process_market_tick(symbol, candles)

    async def on_bot_reload(_payload: dict):
        bot_manager.load_bots_from_db()
        logger.info("Bot registry reloaded from DB (%d active)", len(bot_manager.active_bots))

    event_bus.subscribe(channels.BAR_CLOSE, on_bar_close)
    event_bus.subscribe(channels.BOT_RELOAD, on_bot_reload)


async def worker_keepalive():
    while True:
        await asyncio.sleep(3600)


async def bot_snapshot_loop(bot_manager: BotManagerService):
    logger.info("Starting bot snapshot loop (interval=%.0fs)...", BOT_SNAPSHOT_INTERVAL)
    while True:
        try:
            if bot_manager.active_bots:
                await bot_manager.snapshot_all_bots()
        except Exception as exc:
            logger.error("Error in bot snapshot loop: %s", exc)
        await asyncio.sleep(BOT_SNAPSHOT_INTERVAL)
