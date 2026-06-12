import asyncio
import logging
import websockets

from app.config import (
    WS_HOST,
    WS_PORT,
    WS_MAX_MESSAGE_SIZE,
    TERMINAL_MODE,
    TERMINAL_ROLE,
    ALLOW_LIVE_BOTS,
    REDIS_URL,
)
from app.database import init_db
from app.db.connection import DB_DRIVER
from app.websocket.connection_manager import ConnectionManager
from app.websocket.handlers import handle_client_message
from app.services.bots.runtime import (
    bar_publish_loop,
    bot_market_loop,
    bot_snapshot_loop,
    create_bot_stack,
    create_feed_and_oms,
    runs_bar_publisher,
    runs_bot_engine_inline,
)
from app.services.events.event_bus import create_event_bus
from app.services.events import channels, publish as event_publish

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("websockets.server").setLevel(logging.WARNING)

if TERMINAL_ROLE == "worker":
    raise SystemExit(
        "TERMINAL_ROLE=worker — run `python worker.py` instead of main.py / server.py"
    )

manager = ConnectionManager()
event_bus = create_event_bus(REDIS_URL) if REDIS_URL and TERMINAL_ROLE == "server" else None

logging.info("Initializing feed & OMS (mode=%s, role=%s, db=%s)...", TERMINAL_MODE, TERMINAL_ROLE, DB_DRIVER)
feed, oms = create_feed_and_oms()


async def broadcast_wrapper(payload: dict):
    await manager.broadcast(payload)
    if event_bus and TERMINAL_ROLE == "server":
        await event_bus.publish(channels.WS_BROADCAST, payload)


feed.register_broadcast_callback(broadcast_wrapper)
if hasattr(oms, "register_broadcast_callback"):
    oms.register_broadcast_callback(broadcast_wrapper)

screener_service, backtester_service, bot_manager = create_bot_stack(broadcast_wrapper, oms)


async def redis_forward_loop():
    """Keep forward loop alive (subscriptions registered before event_bus.start)."""
    logging.info("Redis forward loop active on %s", channels.WS_BROADCAST)
    while True:
        await asyncio.sleep(3600)


async def simulated_market_loop():
    logging.info("Starting simulated market data broadcast loop...")
    tick_count = 0
    while True:
        try:
            data = {}
            for symbol in feed.symbols:
                market_data = feed.get_market_data(symbol)
                data[symbol] = market_data
            fills = oms.match_pending_orders()
            sl_tp_fills, sl_tp_logs = oms.check_sl_tp_triggers()

            if sl_tp_logs:
                for log_msg in sl_tp_logs:
                    logging.info(log_msg)
                    await manager.broadcast({
                        "type": "bot_log",
                        "data": {"bot_id": "system", "level": "INFO", "message": log_msg},
                    })

            total_fills = fills + sl_tp_fills
            slim_data = {}
            for symbol, md in data.items():
                slim_data[symbol] = {
                    "symbol": symbol,
                    "price": md["price"],
                    "change_24h": md["change_24h"],
                    "volume_24h": md["volume_24h"],
                    "high_24h": md["high_24h"],
                    "low_24h": md["low_24h"],
                    "candle": md["candle"],
                }
            await manager.broadcast({"type": "market_update", "data": slim_data})

            for client in list(manager.connected_clients):
                sym = manager.client_symbols.get(client)
                if sym and sym in data and data[sym].get("orderbook"):
                    await manager.send_to(client, {
                        "type": "orderbook_update",
                        "data": {sym: data[sym]["orderbook"]},
                    })

            if total_fills:
                logging.info("Orders matched/filled: %s", total_fills)
                await manager.broadcast({"type": "account_update", "data": oms.get_account_data()})
                await manager.broadcast({"type": "trade_history", "data": oms.get_trade_history()})

            tick_count += 1
            if tick_count % 12 == 0:
                from app.database import get_db_stats

                stats = get_db_stats()
                stats["clients"] = len(manager.connected_clients)
                stats["tick_interval"] = feed.tick_interval
                stats["volatility_multiplier"] = feed.volatility_multiplier
                await manager.broadcast({"type": "system_stats", "data": stats})
        except Exception as e:
            logging.error("Error in simulated broadcast loop: %s", e)
        await asyncio.sleep(feed.tick_interval)


async def diagnostics_broadcast_loop():
    while True:
        try:
            from app.database import get_db_stats

            stats = get_db_stats()
            stats["clients"] = len(manager.connected_clients)
            stats["tick_interval"] = 1.0
            stats["volatility_multiplier"] = 1.0
            await manager.broadcast({"type": "system_stats", "data": stats})
        except Exception as e:
            logging.error("Error in diagnostics loop: %s", e)
        await asyncio.sleep(5)


async def websocket_handler(websocket):
    logging.info("New client connected.")
    manager.register(websocket)

    await manager.send_to(websocket, {
        "type": "terminal_config",
        "data": {
            "terminalMode": TERMINAL_MODE,
            "terminalRole": TERMINAL_ROLE,
            "symbols": list(feed.symbols),
            "allowLiveBots": ALLOW_LIVE_BOTS,
            "distributed": bool(REDIS_URL),
        },
    })

    await manager.send_to(websocket, {"type": "account_update", "data": oms.get_account_data()})
    await manager.send_to(websocket, {"type": "trade_history", "data": oms.get_trade_history()})
    await manager.send_to(websocket, {
        "type": "bot_logs_history",
        "data": bot_manager.get_recent_logs(100),
    })

    try:
        async for message_str in websocket:
            await handle_client_message(websocket, message_str, oms, manager, bot_manager, backtester_service)
    except websockets.exceptions.ConnectionClosed:
        logging.info("Client connection closed.")
    finally:
        manager.unregister(websocket)


async def main():
    init_db()
    bot_manager.load_bots_from_db()

    if event_bus:
        async def _publish_reload(payload):
            await event_bus.publish(channels.BOT_RELOAD, payload)

        async def on_ws_broadcast(payload: dict):
            await manager.broadcast(payload)

        event_publish.register_publisher(channels.BOT_RELOAD, _publish_reload)
        event_bus.subscribe(channels.WS_BROADCAST, on_ws_broadcast)
        await event_bus.start()

    await feed.start()
    await oms.initialize()

    logging.info("WebSocket Server listening on ws://%s:%s (role=%s)", WS_HOST, WS_PORT, TERMINAL_ROLE)

    async with websockets.serve(
        websocket_handler, WS_HOST, WS_PORT, max_size=WS_MAX_MESSAGE_SIZE
    ):
        tasks = []

        if runs_bot_engine_inline():
            tasks.append(asyncio.create_task(bot_market_loop(bot_manager, feed)))
            tasks.append(asyncio.create_task(bot_snapshot_loop(bot_manager)))
        elif runs_bar_publisher():
            tasks.append(asyncio.create_task(bar_publish_loop(feed, event_bus)))
            tasks.append(asyncio.create_task(redis_forward_loop()))

        if TERMINAL_MODE == "SIMULATED":
            tasks.append(asyncio.create_task(simulated_market_loop()))
        else:
            tasks.append(asyncio.create_task(diagnostics_broadcast_loop()))

        while True:
            await asyncio.sleep(10)
            logging.info("Heartbeat: Server is running...")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server stopped.")
