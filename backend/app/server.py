import asyncio
import logging
import websockets

from app.bootstrap import create_app_state
from app.config import (
    WS_HOST,
    WS_PORT,
    WS_MAX_MESSAGE_SIZE,
    WS_MSGPACK_ENABLED,
    WS_PING_INTERVAL,
    WS_PING_TIMEOUT,
    WS_KEEPALIVE_INTERVAL_SEC,
    TERMINAL_MODE,
    TERMINAL_ROLE,
    ALLOW_LIVE_BOTS,
    ALLOW_CUSTOM_STRATEGIES,
    BOT_MIN_CANDLES,
    REDIS_URL,
    HTTP_ENABLED,
    HTTP_HOST,
    HTTP_PORT,
    ARCHIVE_ENABLED,
    ARCHIVE_TICKS_ENABLED,
    ARCHIVE_PARQUET_ENABLED,
    ARCHIVE_BACKEND,
    DATA_QUALITY_ENABLED,
    ALTDATA_ENABLED,
    AGENT_LLM_ENABLED,
    AGENT_VISION_ENABLED,
    AGENT_ENABLED,
    SCANNER_ENABLED,
    SIM_SBBS_WARM_ON_STARTUP,
    LOG_JSON,
)
from app.database import init_db
from app.api.http_server import start_http_server
from app.api.outbound import (
    account_update,
    bots_update,
    bot_logs_history,
    orderbook_update,
    publish_bot_log,
    publish_market_update,
    publish_post_trade_bundle,
    publish_system_stats,
    keepalive,
    terminal_config,
    trade_history,
)
from app.websocket.handlers import handle_client_message
from app.services.order_capabilities import get_order_capabilities
from app.services.bots.runtime import (
    bar_publish_loop,
    bot_market_loop,
    bot_snapshot_loop,
    risk_monitor_loop,
    bot_reconcile_loop,
    calibration_refresh_loop,
    regime_rotation_loop,
    alpha_decay_loop,
    scanner_deploy_loop,
    runs_bar_publisher,
    runs_bot_engine_inline,
)
from app.services.bots.paper_oms import run_paper_oms_tick
from app.services.bots.massive_scheduler import run_massive_bot_tick
from app.services.bots.execution_mode import execution_mode_label, uses_paper_oms
from app.services.runtime.shutdown import (
    graceful_shutdown,
    install_signal_handlers,
    wait_for_shutdown_or_tasks,
)
from app.services.runtime.startup_recovery import run_startup_recovery
from app.services.runtime import system_state
from app.services.archive.runtime import archive_capture_loop, archive_ingestion_loop, archive_rollup_loop, archive_startup_pipeline
from app.services.events import channels, publish as event_publish

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
if LOG_JSON:
    from app.observability.json_log import JsonFormatter

    _root = logging.getLogger()
    _handler = logging.StreamHandler()
    _handler.setFormatter(JsonFormatter())
    _root.handlers = [_handler]
    _root.setLevel(logging.INFO)
logging.getLogger("websockets.server").setLevel(logging.WARNING)

if TERMINAL_ROLE == "worker":
    raise SystemExit(
        "TERMINAL_ROLE=worker — run `python worker.py` instead of main.py / server.py"
    )

state = create_app_state()

_massive_broadcast_lock = asyncio.Lock()


async def redis_forward_loop():
    logging.info("Redis forward loop active on %s", channels.WS_BROADCAST)
    while True:
        await asyncio.sleep(3600)


async def simulated_market_loop():
    import time

    logging.info("Starting simulated market data broadcast loop...")
    feed = state.feed
    oms = state.oms
    manager = state.manager
    bot_manager = state.bot_manager
    tick_count = 0
    tick_symbols: set[str] = set()
    while True:
        try:
            data = {}
            now_ms = int(time.time() * 1000)
            if runs_bot_engine_inline():
                tick_symbols = {
                    b["symbol"]
                    for b in bot_manager.active_bots.values()
                    if b.get("status") == "RUNNING"
                    and b.get("execution_mode", "BAR_CLOSE") == "TICK"
                }
            for symbol in feed.symbols:
                market_data = feed.get_market_data(symbol)
                data[symbol] = market_data
                if runs_bot_engine_inline() and symbol in tick_symbols:
                    await bot_manager.process_price_tick(
                        symbol, float(market_data["price"]), now_ms
                    )
                elif state.event_bus and runs_bar_publisher():
                    await state.event_bus.publish(
                        channels.TICK_PRICE,
                        {"symbol": symbol, "price": market_data["price"], "time_ms": now_ms},
                    )
            await run_paper_oms_tick(oms, bot_manager, manager)
            slim_data = _slim_market_payload(data)
            await publish_market_update(manager.broadcast, slim_data)

            for client in list(manager.connected_clients):
                sym = manager.client_symbols.get(client)
                if sym and sym in data and data[sym].get("orderbook"):
                    await manager.send_to(
                        client,
                        orderbook_update({sym: data[sym]["orderbook"]}),
                    )

            tick_count += 1
            if tick_count % 120 == 0 and hasattr(feed, "persist_state"):
                await asyncio.to_thread(feed.persist_state)
            if tick_count % 12 == 0:
                from app.services.db_stats_cache import get_db_stats_cached

                stats = await asyncio.to_thread(get_db_stats_cached)
                stats["clients"] = len(manager.connected_clients)
                stats["tick_interval"] = feed.tick_interval
                stats["volatility_multiplier"] = feed.volatility_multiplier
                await publish_system_stats(manager.broadcast, stats)
        except Exception as e:
            logging.error("Error in simulated broadcast loop: %s", e)
        await asyncio.sleep(feed.tick_interval)


def _slim_market_payload(data: dict) -> dict:
    """Strip market snapshots for WebSocket market_update (shared shape with sim loop)."""
    slim: dict = {}
    for symbol, md in data.items():
        slim[symbol] = {
            "symbol": symbol,
            "price": md["price"],
            "change_24h": md["change_24h"],
            "volume_24h": md["volume_24h"],
            "high_24h": md["high_24h"],
            "low_24h": md["low_24h"],
            "candle": md["candle"],
        }
    return slim


def _market_snapshot_changed(prev: dict | None, cur: dict) -> bool:
    """True when a slim market_update payload differs from the last broadcast."""
    if not prev:
        return True
    for key in ("price", "change_24h", "volume_24h", "high_24h", "low_24h"):
        if prev.get(key) != cur.get(key):
            return True
    pc, cc = prev.get("candle") or {}, cur.get("candle") or {}
    if not pc and not cc:
        return False
    for key in ("time", "open", "high", "low", "close", "volume"):
        if pc.get(key) != cc.get(key):
            return True
    return False


async def live_ib_market_broadcast_loop():
    """Push in-memory IB feed state to clients on a fixed interval (LIVE_IB only)."""
    from app.config import IB_BROADCAST_INTERVAL_SEC

    logging.info(
        "Starting LIVE_IB market broadcast loop (interval=%ss)...",
        IB_BROADCAST_INTERVAL_SEC,
    )
    feed = state.feed
    manager = state.manager
    while True:
        try:
            data = {symbol: feed.get_market_data(symbol) for symbol in feed.symbols}
            if data:
                await publish_market_update(manager.broadcast, _slim_market_payload(data))
                for client in list(manager.connected_clients):
                    sym = manager.client_symbols.get(client)
                    if sym and sym in data and data[sym].get("orderbook"):
                        await manager.send_to(
                            client,
                            orderbook_update({sym: data[sym]["orderbook"]}),
                        )
        except Exception as e:
            logging.error("Error in LIVE_IB market broadcast loop: %s", e)
        await asyncio.sleep(IB_BROADCAST_INTERVAL_SEC)


async def live_massive_market_broadcast_loop():
    """Push in-memory Massive feed state to clients; paper OMS + bot scheduler on each tick."""
    from app.config import MASSIVE_BROADCAST_INTERVAL_SEC, MASSIVE_POLL_INTERVAL_SEC

    logging.info(
        "Starting LIVE_MASSIVE market broadcast loop (interval=%ss)...",
        MASSIVE_BROADCAST_INTERVAL_SEC,
    )
    feed = state.feed
    manager = state.manager
    bot_manager = state.bot_manager
    oms = state.oms
    last_sent: dict[str, dict] = {}
    last_prices: dict[str, float] = {}
    while True:
        interval = MASSIVE_BROADCAST_INTERVAL_SEC
        async with _massive_broadcast_lock:
            try:
                if hasattr(feed, "massive_status"):
                    status = feed.massive_status
                    if status.get("poll_fallback"):
                        interval = max(
                            float(MASSIVE_BROADCAST_INTERVAL_SEC),
                            float(MASSIVE_POLL_INTERVAL_SEC),
                        )
                data = {symbol: feed.get_market_data(symbol) for symbol in feed.symbols}
                subscribed = {
                    manager.client_symbols.get(client)
                    for client in manager.connected_clients
                }
                subscribed.discard(None)
                last_prices = await run_massive_bot_tick(
                    bot_manager, feed, manager, oms, last_prices=last_prices,
                )
                changed: dict[str, dict] = {}
                if data:
                    for symbol, md in data.items():
                        slim = _slim_market_payload({symbol: md})[symbol]
                        if symbol in subscribed or _market_snapshot_changed(last_sent.get(symbol), slim):
                            changed[symbol] = slim
                            last_sent[symbol] = slim
                if changed:
                    await publish_market_update(manager.broadcast, changed)
                    for client in list(manager.connected_clients):
                        sym = manager.client_symbols.get(client)
                        if sym and sym in changed:
                            full = data.get(sym) or {}
                            if full.get("orderbook"):
                                await manager.send_to(
                                    client,
                                    orderbook_update({sym: full["orderbook"]}),
                                )
            except Exception as e:
                logging.error("Error in LIVE_MASSIVE market broadcast loop: %s", e)
        await asyncio.sleep(interval)


async def diagnostics_broadcast_loop():
    from app.config import DIAGNOSTICS_INTERVAL_SEC
    from app.db.async_bridge import run_db
    from app.services.db_stats_cache import get_db_stats_cached
    from app.services.data_quality.monitor import evaluate_symbols, data_quality_stats_from_report

    manager = state.manager
    feed = state.feed
    interval = max(5.0, float(DIAGNOSTICS_INTERVAL_SEC))
    while True:
        try:
            stats = await run_db(get_db_stats_cached)
            symbols = list(getattr(feed, "symbols", []) or [])
            if symbols and not stats.get("data_quality"):
                report = await run_db(evaluate_symbols, symbols)
                stats["data_quality"] = data_quality_stats_from_report(report)
            stats["clients"] = len(manager.connected_clients)
            stats["tick_interval"] = 1.0
            stats["volatility_multiplier"] = 1.0
            await publish_system_stats(manager.broadcast, stats)
        except Exception as e:
            logging.error("Error in diagnostics loop: %s", e)
        await asyncio.sleep(interval)


async def _send_ws_bootstrap_payloads(websocket):
    """Defer heavy WS payloads so the client gets terminal_config first."""
    manager = state.manager
    try:
        await manager.send_to(websocket, account_update(state.oms.get_account_data()))
        await manager.send_to(websocket, bots_update(state.bot_manager.list_bots_public()))
        await manager.send_to(websocket, trade_history(state.oms.get_trade_history()))
        await manager.send_to(websocket, bot_logs_history(state.bot_manager.get_recent_logs(100)))
    except Exception as exc:
        logging.debug("WS bootstrap payloads skipped: %s", exc)


async def websocket_handler(websocket):
    manager = state.manager
    logging.info("New client connected.")
    manager.register(websocket)
    from app.observability.ws_metrics import record_ws_connect, record_ws_disconnect

    record_ws_connect()

    llm_config: dict = {"available": False, "provider": "off"}
    llm_task = asyncio.create_task(_fetch_llm_config())

    await manager.send_to(websocket, terminal_config({
        "terminalMode": TERMINAL_MODE,
        "terminalRole": TERMINAL_ROLE,
        "executionMode": execution_mode_label(),
        "symbols": list(state.feed.symbols),
        "allowLiveBots": ALLOW_LIVE_BOTS,
        "allowCustomStrategies": ALLOW_CUSTOM_STRATEGIES,
        "distributed": bool(REDIS_URL),
        "botMinCandles": BOT_MIN_CANDLES,
        "archiveTicksEnabled": ARCHIVE_TICKS_ENABLED,
        "archiveParquetEnabled": ARCHIVE_PARQUET_ENABLED,
        "archiveBackend": ARCHIVE_BACKEND,
        "wsMsgpackEnabled": WS_MSGPACK_ENABLED,
        "agentLlmEnabled": AGENT_LLM_ENABLED,
        "agentLlmAvailable": False,
        "agentVisionEnabled": AGENT_VISION_ENABLED,
        "agentEnabled": AGENT_ENABLED,
        "scannerEnabled": SCANNER_ENABLED,
        "orderCapabilities": get_order_capabilities(state.oms),
    }))

    asyncio.create_task(_send_ws_bootstrap_payloads(websocket))

    try:
        llm_config = await llm_task
        await manager.send_to(websocket, terminal_config({
            "agentLlmAvailable": llm_config.get("available", False),
            "agentLlmProvider": llm_config.get("provider"),
            "agentLlmModel": llm_config.get("model"),
            "agentLlmModels": llm_config.get("models") or [],
        }))
    except Exception:
        pass

    close_code: int | None = None
    close_reason = ""
    try:
        async for message_str in websocket:
            await handle_client_message(websocket, message_str, state)
    except websockets.exceptions.ConnectionClosed as exc:
        close_code = exc.code
        close_reason = exc.reason or ""
        logging.info(
            "Client connection closed (code=%s reason=%r).",
            close_code,
            close_reason,
        )
    finally:
        if close_code is None:
            close_code = websocket.close_code
            close_reason = websocket.close_reason or ""
        record_ws_disconnect(close_code, close_reason)
        try:
            from app.services.bots.backtest_jobs import cancel_job, clear_job

            cancel_job(websocket)
            clear_job(websocket)
        except Exception as exc:
            logging.debug("WS disconnect backtest cleanup skipped: %s", exc)
        manager.unregister(websocket)


async def ws_keepalive_loop():
    """Broadcast lightweight keepalive frames so clients/proxies detect half-open links."""
    import time

    manager = state.manager
    interval = max(5.0, float(WS_KEEPALIVE_INTERVAL_SEC))
    while True:
        await asyncio.sleep(interval)
        if not manager.connected_clients:
            continue
        try:
            await manager.broadcast(keepalive({"ts": time.time()}))
        except Exception as exc:
            logging.debug("WS keepalive broadcast skipped: %s", exc)


async def _fetch_llm_config() -> dict:
    try:
        from app.services.agent.llm.router import get_llm_status

        return await get_llm_status()
    except Exception:
        return {"available": False, "provider": "off"}


async def heartbeat_loop():
    while True:
        await asyncio.sleep(60)
        logging.info("Heartbeat: Server is running...")


async def main():
    from app.db.connection import warm_pool

    warm_pool()
    init_db()
    from app.db.migrations import run_alembic_upgrade_if_enabled

    run_alembic_upgrade_if_enabled()
    system_state.mark_process_starting()
    try:
        from app.config import BACKTEST_JOB_RETENTION_DAYS, OPTIMIZATION_RETENTION_DAYS
        from app.services.bots.backtest_job_store import prune_backtest_jobs
        from app.services.bots.optimization_store import prune_optimization_runs

        opt_del = prune_optimization_runs(OPTIMIZATION_RETENTION_DAYS)
        job_del = prune_backtest_jobs(BACKTEST_JOB_RETENTION_DAYS)
        if opt_del or job_del:
            logging.info(
                "Retention prune: %s optimization run(s), %s backtest job(s)",
                opt_del,
                job_del,
            )
    except Exception:
        logging.exception("Retention prune failed")
    state.bot_manager.load_bots_from_db()

    if state.event_bus:
        async def _publish_reload(payload):
            await state.event_bus.publish(channels.BOT_RELOAD, payload)

        async def on_ws_broadcast(payload: dict):
            await state.manager.broadcast(payload)

        event_publish.register_publisher(channels.BOT_RELOAD, _publish_reload)

        async def _publish_emergency(payload):
            await state.event_bus.publish(channels.EMERGENCY_STOP, payload)

        event_publish.register_publisher(channels.EMERGENCY_STOP, _publish_emergency)
        state.event_bus.subscribe(channels.WS_BROADCAST, on_ws_broadcast)
        await state.event_bus.start()

    await state.feed.start()
    await state.oms.initialize()

    recovery = await run_startup_recovery(
        state.oms,
        state.bot_manager,
        restore_checkpoint=runs_bot_engine_inline(),
    )
    if recovery.get("safe_mode"):
        logging.warning("Server started in safe mode — all bots paused until operator confirms.")

    shutdown_event = asyncio.Event()
    install_signal_handlers(asyncio.get_running_loop(), shutdown_event)
    state.shutdown_event = shutdown_event

    logging.info("Starting server (role=%s, ws=%s:%s, http=%s:%s)...", TERMINAL_ROLE, WS_HOST, WS_PORT, HTTP_HOST, HTTP_PORT)

    try:
        async with websockets.serve(
            websocket_handler,
            WS_HOST,
            WS_PORT,
            max_size=WS_MAX_MESSAGE_SIZE,
            ping_interval=WS_PING_INTERVAL,
            ping_timeout=WS_PING_TIMEOUT,
        ):
            logging.info("WebSocket Server listening on ws://%s:%s", WS_HOST, WS_PORT)
            if HTTP_ENABLED:
                logging.info("HTTP API enabled on http://%s:%s", HTTP_HOST, HTTP_PORT)
            tasks = [
                asyncio.create_task(heartbeat_loop()),
                asyncio.create_task(ws_keepalive_loop()),
            ]
            
            async def _trigger_test():
                await asyncio.sleep(5)
                from app.services.events import channels
                from app.services.agent import copilot_store
                import uuid
                import time
                
                async def fake_narrate(source, msg_text):
                    session_id = copilot_store.ensure_session_id("default")
                    msg = copilot_store.append_message(
                        session_id=session_id,
                        role="assistant",
                        content=msg_text,
                    )
                    await state.manager.broadcast({
                        "type": "copilot_agent_message",
                        "data": {
                            "session_id": session_id,
                            "message": {
                                "id": msg.get("id", str(uuid.uuid4())),
                                "role": "assistant",
                                "content": msg_text,
                                "source_agent": source,
                                "timestamp": time.time()
                            }
                        }
                    })

                try:
                    while True:
                        await asyncio.sleep(30)
                        if not state.manager.connected_clients:
                            continue
                        
                        await fake_narrate("RiskSentinel", "Risk Sentinel is active. Monitoring global portfolio exposure.")
                        await asyncio.sleep(2)
                        await fake_narrate("AlphaDecay", "Alpha Decay scanner online. Current regime is volatile.")
                        await asyncio.sleep(4)
                        await fake_narrate("RegimeRotation", "Market shifted to trending regime. I rotated the BTCUSDT bot from MACD_RSI to SUPERTREND_ADX.")
                except Exception as e:
                    logging.error("Test trigger crashed: %s", e)

            tasks.append(asyncio.create_task(_trigger_test()))


            if HTTP_ENABLED:
                http_task = start_http_server(state, shutdown_event)

                def _log_http_task_exit(task: asyncio.Task) -> None:
                    if task.cancelled():
                        return
                    exc = task.exception()
                    if exc is not None:
                        logging.critical("HTTP server task exited unexpectedly: %s", exc)

                http_task.add_done_callback(_log_http_task_exit)

            if runs_bot_engine_inline():
                if not state.bot_engine_uses_bar_hooks:
                    tasks.append(asyncio.create_task(bot_market_loop(state.bot_manager, state.feed)))
                tasks.append(asyncio.create_task(bot_snapshot_loop(state.bot_manager)))
                tasks.append(asyncio.create_task(risk_monitor_loop(state.bot_manager)))
                tasks.append(asyncio.create_task(calibration_refresh_loop()))
                tasks.append(asyncio.create_task(regime_rotation_loop(state.bot_manager)))
                tasks.append(asyncio.create_task(alpha_decay_loop(state.bot_manager)))
                tasks.append(
                    asyncio.create_task(
                        scanner_deploy_loop(state.bot_manager, backtester=state.backtester, agent_event_bus=state.agent_event_bus)
                    )
                )
                if not uses_paper_oms():
                    tasks.append(asyncio.create_task(bot_reconcile_loop(state.bot_manager)))
            elif runs_bar_publisher():
                tasks.append(asyncio.create_task(bar_publish_loop(state.feed, state.event_bus)))
                tasks.append(asyncio.create_task(redis_forward_loop()))

            if TERMINAL_MODE == "SIMULATED":
                tasks.append(asyncio.create_task(simulated_market_loop()))
                if SIM_SBBS_WARM_ON_STARTUP and hasattr(state.feed, "warm_generators"):
                    tasks.append(asyncio.create_task(state.feed.warm_generators()))
            elif TERMINAL_MODE == "LIVE_IB":
                tasks.append(asyncio.create_task(live_ib_market_broadcast_loop()))
                tasks.append(asyncio.create_task(diagnostics_broadcast_loop()))
            elif TERMINAL_MODE == "LIVE_MASSIVE":
                tasks.append(asyncio.create_task(live_massive_market_broadcast_loop()))
                tasks.append(asyncio.create_task(diagnostics_broadcast_loop()))
            else:
                tasks.append(asyncio.create_task(diagnostics_broadcast_loop()))

            if ARCHIVE_ENABLED:
                from app.config import (
                    ARCHIVE_BACKFILL_ON_STARTUP,
                    ARCHIVE_INGESTION_ENABLED,
                    ARCHIVE_INGESTION_ON_STARTUP,
                )
                if ARCHIVE_BACKFILL_ON_STARTUP or ARCHIVE_INGESTION_ON_STARTUP:
                    tasks.append(asyncio.create_task(archive_startup_pipeline(state.feed)))
                tasks.append(asyncio.create_task(archive_capture_loop(state.feed)))
                tasks.append(asyncio.create_task(archive_rollup_loop(state.feed)))
                if ARCHIVE_INGESTION_ENABLED:
                    tasks.append(asyncio.create_task(archive_ingestion_loop(state.feed)))

            if ARCHIVE_TICKS_ENABLED:
                from app.services.archive.tick_writer import tick_flush_loop
                tasks.append(asyncio.create_task(tick_flush_loop()))

            if DATA_QUALITY_ENABLED:
                from app.services.data_quality.loop import data_quality_loop
                tasks.append(asyncio.create_task(
                    data_quality_loop(state.bot_manager, state.feed)
                ))

            if ALTDATA_ENABLED:
                from app.services.altdata.loop import altdata_refresh_loop
                tasks.append(asyncio.create_task(altdata_refresh_loop(state.feed)))

            from app.config import NOTIFICATION_DIGEST_ENABLED
            if NOTIFICATION_DIGEST_ENABLED:
                from app.services.notifications.digest_loop import notification_digest_loop
                tasks.append(asyncio.create_task(notification_digest_loop(state.oms)))

            from app.services.bots.backtest_worker import backtest_job_worker_loop
            tasks.append(asyncio.create_task(backtest_job_worker_loop(state)))

            await wait_for_shutdown_or_tasks(tasks, shutdown_event)
    except OSError as exc:
        port_in_use = (
            getattr(exc, "winerror", None) == 10048
            or getattr(exc, "errno", None) in (48, 98, 10048)
        )
        if port_in_use:
            logging.critical(
                "Cannot bind WebSocket port %s:%s — already in use. "
                "Stop the other backend or run: .\\scripts\\start-massive.ps1 -Recycle",
                WS_HOST,
                WS_PORT,
            )
        else:
            logging.critical("Server bind failed on %s:%s: %s", WS_HOST, WS_PORT, exc)
        raise
    finally:
        await graceful_shutdown(
            bot_manager=state.bot_manager if runs_bot_engine_inline() else None,
            oms=state.oms,
            feed=state.feed,
            event_bus=state.event_bus,
        )


async def _shutdown() -> None:
    """Backward-compatible shutdown hook for main.py."""
    await graceful_shutdown(
        bot_manager=state.bot_manager if runs_bot_engine_inline() else None,
        oms=state.oms,
        feed=state.feed,
        event_bus=state.event_bus,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Server stopped.")
