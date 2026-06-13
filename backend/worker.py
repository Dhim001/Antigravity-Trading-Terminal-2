"""Bot engine worker — run separately from the WebSocket server."""

import asyncio
import logging

from app.config import TERMINAL_MODE, TERMINAL_ROLE, REDIS_URL
from app.services.bots.live_hooks import register_live_bot_hooks
from app.database import init_db
from app.db.connection import DB_DRIVER
from app.services.bots.runtime import (
    bot_snapshot_loop,
    create_bot_stack,
    create_feed_and_oms,
    register_worker_handlers,
    worker_keepalive,
)
from app.services.candle_feed_stub import CandleFeedStub
from app.services.events.event_bus import create_event_bus
from app.services.events import channels

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def main():
    if TERMINAL_ROLE != "worker":
        logger.warning("TERMINAL_ROLE=%s — worker.py expects TERMINAL_ROLE=worker", TERMINAL_ROLE)
    if not REDIS_URL:
        raise SystemExit("Worker mode requires REDIS_URL (bar-close events from server role).")

    init_db()
    logger.info("Bot worker starting (db=%s, redis=%s)", DB_DRIVER, REDIS_URL)

    if TERMINAL_MODE == "SIMULATED":
        from app.services.sim_oms import SimulatedOMSService

        feed = CandleFeedStub()
        oms = SimulatedOMSService(feed)
    else:
        feed, oms = create_feed_and_oms()

    event_bus = create_event_bus(REDIS_URL)

    async def broadcast_cb(payload: dict):
        await event_bus.publish(channels.WS_BROADCAST, payload)

    _, _, bot_manager = create_bot_stack(broadcast_cb, oms)
    bot_manager.load_bots_from_db()

    await feed.start()
    await oms.initialize()

    register_worker_handlers(bot_manager, event_bus, feed)
    register_live_bot_hooks(feed, bot_manager)
    await event_bus.start()
    logger.info("Bot worker listening on %s", channels.BAR_CLOSE)

    await asyncio.gather(
        bot_snapshot_loop(bot_manager),
        worker_keepalive(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot worker stopped.")
