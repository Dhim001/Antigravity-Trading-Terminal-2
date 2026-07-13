"""Bot engine worker — run separately from the WebSocket server."""

import asyncio
import logging

from app.config import AGENT_ENABLED, TERMINAL_MODE, TERMINAL_ROLE, REDIS_URL
from app.database import init_db
from app.db.connection import DB_DRIVER
from app.services.agent.chart_analyst import init_chart_analyst
from app.services.bots.runtime import (
    bot_snapshot_loop,
    risk_monitor_loop,
    bot_reconcile_loop,
    create_bot_stack,
    create_feed_and_oms,
    register_worker_handlers,
    worker_keepalive,
    alpha_decay_loop,
    regime_rotation_loop,
)
from app.services.bots.execution_mode import uses_paper_oms
from app.services.candle_feed_stub import CandleFeedStub
from app.services.events.event_bus import create_event_bus
from app.services.events import channels
from app.services.runtime.shutdown import (
    graceful_shutdown,
    install_signal_handlers,
    wait_for_shutdown_or_tasks,
)
from app.services.runtime.startup_recovery import run_startup_recovery
from app.services.runtime import system_state

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def main():
    if TERMINAL_ROLE != "worker":
        logger.warning("TERMINAL_ROLE=%s — worker.py expects TERMINAL_ROLE=worker", TERMINAL_ROLE)
    if not REDIS_URL:
        raise SystemExit("Worker mode requires REDIS_URL (bar-close events from server role).")

    init_db()
    system_state.mark_process_starting()
    logger.info("Bot worker starting (db=%s, redis=%s)", DB_DRIVER, REDIS_URL)

    if TERMINAL_MODE == "SIMULATED":
        from app.services.sim_oms import SimulatedOMSService

        feed = CandleFeedStub()
        oms = SimulatedOMSService(feed)
    elif TERMINAL_MODE == "LIVE_IB":
        from app.config import IB_OMS_ENABLED
        from app.services.sim_oms import SimulatedOMSService

        feed = CandleFeedStub()
        if IB_OMS_ENABLED:
            from app.services.ib_oms import IbOMSService

            oms = IbOMSService(feed)
            logger.info("LIVE_IB worker: IB OMS enabled (stub feed; IB on server role).")
        else:
            oms = SimulatedOMSService(feed)
            logger.info("LIVE_IB worker: using candle stub (IB connection runs on server role only).")
    elif TERMINAL_MODE == "LIVE_MASSIVE":
        from app.services.sim_oms import SimulatedOMSService

        feed = CandleFeedStub()
        oms = SimulatedOMSService(feed)
        logger.info("LIVE_MASSIVE worker: using candle stub (Massive WS runs on server role only).")
    else:
        feed, oms = create_feed_and_oms()

    event_bus = create_event_bus(REDIS_URL)

    async def broadcast_cb(payload: dict):
        await event_bus.publish(channels.WS_BROADCAST, payload)

    _, screener, bot_manager = create_bot_stack(broadcast_cb, oms)
    bot_manager.load_bots_from_db()

    chart_analyst = None
    if AGENT_ENABLED:
        chart_analyst = init_chart_analyst(screener=screener, feed=feed, broadcast_fn=broadcast_cb)

    await feed.start()
    await oms.initialize()

    recovery = await run_startup_recovery(oms, bot_manager, restore_checkpoint=True)
    if recovery.get("safe_mode"):
        logger.warning("Worker started in safe mode — all bots paused until operator confirms.")

    register_worker_handlers(bot_manager, event_bus, feed, oms, chart_analyst=chart_analyst)
    await event_bus.start()
    logger.info("Bot worker listening on %s", channels.BAR_CLOSE)

    shutdown_event = asyncio.Event()
    install_signal_handlers(asyncio.get_running_loop(), shutdown_event)

    tasks = [
        asyncio.create_task(bot_snapshot_loop(bot_manager)),
        asyncio.create_task(risk_monitor_loop(bot_manager)),
        asyncio.create_task(alpha_decay_loop(bot_manager)),
        asyncio.create_task(regime_rotation_loop(bot_manager)),
        asyncio.create_task(worker_keepalive()),
    ]
    if TERMINAL_MODE != "SIMULATED" and not uses_paper_oms():
        tasks.append(asyncio.create_task(bot_reconcile_loop(bot_manager)))

    try:
        await wait_for_shutdown_or_tasks(tasks, shutdown_event)
    finally:
        await graceful_shutdown(
            bot_manager=bot_manager,
            oms=oms,
            feed=feed,
            event_bus=event_bus,
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot worker stopped.")
