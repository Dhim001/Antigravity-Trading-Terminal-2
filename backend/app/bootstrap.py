"""Application bootstrap — builds AppState and wires feed/OMS/bots."""

from __future__ import annotations

import logging

from app.api.state import AppState
from app.config import AGENT_ENABLED, REDIS_URL, TERMINAL_MODE, TERMINAL_ROLE
from app.db.connection import DB_DRIVER
from app.services.agent.chart_analyst import init_chart_analyst
from app.services.bots.live_hooks import register_live_bot_hooks
from app.services.bots.runtime import create_bot_stack, create_feed_and_oms, runs_bot_engine_inline
from app.services.events.event_bus import create_event_bus
from app.services.events import channels
from app.websocket.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)


def create_app_state() -> AppState:
    manager = ConnectionManager()
    event_bus = create_event_bus(REDIS_URL) if REDIS_URL and TERMINAL_ROLE == "server" else None

    logger.info(
        "Initializing feed & OMS (mode=%s, role=%s, db=%s)...",
        TERMINAL_MODE,
        TERMINAL_ROLE,
        DB_DRIVER,
    )
    feed, oms = create_feed_and_oms()

    async def broadcast_wrapper(payload: dict):
        await manager.broadcast(payload)
        if event_bus and TERMINAL_ROLE == "server":
            await event_bus.publish(channels.WS_BROADCAST, payload)

    feed.register_broadcast_callback(broadcast_wrapper)
    if hasattr(oms, "register_broadcast_callback"):
        oms.register_broadcast_callback(broadcast_wrapper)

    screener_service, backtester_service, bot_manager = create_bot_stack(broadcast_wrapper, oms)

    chart_analyst = None
    if AGENT_ENABLED:
        chart_analyst = init_chart_analyst(
            screener=screener_service,
            feed=feed,
            broadcast_fn=broadcast_wrapper,
        )
        logger.info("Chart Analyst agent initialized")

    bot_engine_uses_bar_hooks = False
    # Server role publishes bar-close to Redis; worker executes bots — no inline hooks.
    if runs_bot_engine_inline():
        if hasattr(feed, "register_bar_close_callback"):
            register_live_bot_hooks(feed, bot_manager, chart_analyst=chart_analyst, manager=manager)
            bot_engine_uses_bar_hooks = True
            logger.info("Bot engine using feed bar-close hooks (poll loop disabled)")

    return AppState(
        oms=oms,
        manager=manager,
        bot_manager=bot_manager,
        backtester=backtester_service,
        feed=feed,
        event_bus=event_bus,
        screener=screener_service,
        chart_analyst=chart_analyst,
        bot_engine_uses_bar_hooks=bot_engine_uses_bar_hooks,
    )
