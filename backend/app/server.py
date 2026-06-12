import asyncio
import logging
import websockets

from app.bootstrap import create_app_state
from app.config import (
    WS_HOST,
    WS_PORT,
    WS_MAX_MESSAGE_SIZE,
    TERMINAL_MODE,
    TERMINAL_ROLE,
    ALLOW_LIVE_BOTS,
    REDIS_URL,
    HTTP_ENABLED,
    HTTP_HOST,
    HTTP_PORT,
)
from app.database import init_db
from app.api.http_server import run_http_server
from app.api.outbound import (
    account_update,
    bot_logs_history,
    orderbook_update,
    publish_bot_log,
    publish_market_update,
    publish_post_trade_bundle,
    publish_system_stats,
    terminal_config,
    trade_history,
)
from app.websocket.handlers import handle_client_message
from app.services.bots.runtime import (
    bar_publish_loop,
    bot_market_loop,
    bot_snapshot_loop,
    runs_bar_publisher,
    runs_bot_engine_inline,
)
from app.services.events import channels, publish as event_publish

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("websockets.server").setLevel(logging.WARNING)

if TERMINAL_ROLE == "worker":
    raise SystemExit(
        "TERMINAL_ROLE=worker — run `python worker.py` instead of main.py / server.py"
    )

state = create_app_state()


async def redis_forward_loop():
    logging.info("Redis forward loop active on %s", channels.WS_BROADCAST)
    while True:
        await asyncio.sleep(3600)


async def simulated_market_loop():
    logging.info("Starting simulated market data broadcast loop...")
    feed = state.feed
    oms = state.oms
    manager = state.manager
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
                    await publish_bot_log(manager.broadcast, "system", "INFO", log_msg)

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
            await publish_market_update(manager.broadcast, slim_data)

            for client in list(manager.connected_clients):
                sym = manager.client_symbols.get(client)
                if sym and sym in data and data[sym].get("orderbook"):
                    await manager.send_to(
                        client,
                        orderbook_update({sym: data[sym]["orderbook"]}),
                    )

            if total_fills:
                logging.info("Orders matched/filled: %s", total_fills)
                await publish_post_trade_bundle(
                    manager.broadcast,
                    oms.get_account_data(),
                    oms.get_trade_history(),
                )

            tick_count += 1
            if tick_count % 12 == 0:
                from app.database import get_db_stats

                stats = get_db_stats()
                stats["clients"] = len(manager.connected_clients)
                stats["tick_interval"] = feed.tick_interval
                stats["volatility_multiplier"] = feed.volatility_multiplier
                await publish_system_stats(manager.broadcast, stats)
        except Exception as e:
            logging.error("Error in simulated broadcast loop: %s", e)
        await asyncio.sleep(feed.tick_interval)


async def diagnostics_broadcast_loop():
    manager = state.manager
    while True:
        try:
            from app.database import get_db_stats

            stats = get_db_stats()
            stats["clients"] = len(manager.connected_clients)
            stats["tick_interval"] = 1.0
            stats["volatility_multiplier"] = 1.0
            await publish_system_stats(manager.broadcast, stats)
        except Exception as e:
            logging.error("Error in diagnostics loop: %s", e)
        await asyncio.sleep(5)


async def websocket_handler(websocket):
    manager = state.manager
    logging.info("New client connected.")
    manager.register(websocket)

    await manager.send_to(websocket, terminal_config({
        "terminalMode": TERMINAL_MODE,
        "terminalRole": TERMINAL_ROLE,
        "symbols": list(state.feed.symbols),
        "allowLiveBots": ALLOW_LIVE_BOTS,
        "distributed": bool(REDIS_URL),
    }))

    await manager.send_to(websocket, account_update(state.oms.get_account_data()))
    await manager.send_to(websocket, trade_history(state.oms.get_trade_history()))
    await manager.send_to(websocket, bot_logs_history(state.bot_manager.get_recent_logs(100)))

    try:
        async for message_str in websocket:
            await handle_client_message(websocket, message_str, state)
    except websockets.exceptions.ConnectionClosed:
        logging.info("Client connection closed.")
    finally:
        manager.unregister(websocket)


async def heartbeat_loop():
    while True:
        await asyncio.sleep(10)
        logging.info("Heartbeat: Server is running...")


async def main():
    init_db()
    state.bot_manager.load_bots_from_db()

    if state.event_bus:
        async def _publish_reload(payload):
            await state.event_bus.publish(channels.BOT_RELOAD, payload)

        async def on_ws_broadcast(payload: dict):
            await state.manager.broadcast(payload)

        event_publish.register_publisher(channels.BOT_RELOAD, _publish_reload)
        state.event_bus.subscribe(channels.WS_BROADCAST, on_ws_broadcast)
        await state.event_bus.start()

    await state.feed.start()
    await state.oms.initialize()

    logging.info("WebSocket Server listening on ws://%s:%s (role=%s)", WS_HOST, WS_PORT, TERMINAL_ROLE)
    if HTTP_ENABLED:
        logging.info("HTTP API enabled on http://%s:%s", HTTP_HOST, HTTP_PORT)

    async with websockets.serve(
        websocket_handler, WS_HOST, WS_PORT, max_size=WS_MAX_MESSAGE_SIZE
    ):
        tasks = [asyncio.create_task(heartbeat_loop())]

        if HTTP_ENABLED:
            tasks.append(asyncio.create_task(run_http_server(state)))

        if runs_bot_engine_inline():
            tasks.append(asyncio.create_task(bot_market_loop(state.bot_manager, state.feed)))
            tasks.append(asyncio.create_task(bot_snapshot_loop(state.bot_manager)))
        elif runs_bar_publisher():
            tasks.append(asyncio.create_task(bar_publish_loop(state.feed, state.event_bus)))
            tasks.append(asyncio.create_task(redis_forward_loop()))

        if TERMINAL_MODE == "SIMULATED":
            tasks.append(asyncio.create_task(simulated_market_loop()))
        else:
            tasks.append(asyncio.create_task(diagnostics_broadcast_loop()))

        await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server stopped.")
