import asyncio
import logging
import websockets
from app.config import WS_HOST, WS_PORT, WS_MAX_MESSAGE_SIZE, TERMINAL_MODE
from app.database import init_db
from app.websocket.connection_manager import ConnectionManager
from app.websocket.handlers import handle_client_message
from app.services.bots.screener import MarketScreenerService
from app.services.bots.manager import BotManagerService
from app.services.bots.backtester import BacktesterService

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
# Dev/HMR refreshes often abort the handshake mid-flight; avoid ERROR tracebacks for that.
logging.getLogger("websockets.server").setLevel(logging.WARNING)

# Global connection manager
manager = ConnectionManager()

# Dependency Injection Factory
if TERMINAL_MODE == "LIVE_ALPACA":
    logging.info("Initializing Live Alpaca Feed & OMS...")
    from app.services.alpaca_feed import AlpacaFeedService
    from app.services.alpaca_oms import AlpacaOMSService
    feed = AlpacaFeedService()
    oms = AlpacaOMSService(feed)
elif TERMINAL_MODE == "LIVE_BINANCE":
    logging.info("Initializing Live Binance Feed & OMS...")
    from app.services.binance_feed import BinanceFeedService
    from app.services.binance_oms import BinanceOMSService
    feed = BinanceFeedService()
    oms = BinanceOMSService(feed)
elif TERMINAL_MODE == "LIVE_ETORO":
    logging.info("Initializing Live eToro Feed & OMS...")
    from app.services.etoro_feed import EtoroFeedService
    from app.services.etoro_oms import EtoroOMSService
    feed = EtoroFeedService()
    oms = EtoroOMSService(feed)
else: # "SIMULATED"
    logging.info("Initializing Simulated Feed & OMS...")
    from app.services.sim_feed import SimulatedFeedService
    from app.services.sim_oms import SimulatedOMSService
    feed = SimulatedFeedService()
    oms = SimulatedOMSService(feed)

# Register a bridge to broadcast market updates directly to WebSocket clients
async def broadcast_wrapper(payload: dict):
    await manager.broadcast(payload)

feed.register_broadcast_callback(broadcast_wrapper)
if hasattr(oms, "register_broadcast_callback"):
    oms.register_broadcast_callback(broadcast_wrapper)

# Initialize Bot Engine
screener_service = MarketScreenerService()
backtester_service = BacktesterService(screener_service)
bot_manager = BotManagerService(oms, screener_service, broadcast_wrapper)

async def simulated_market_loop():
    """Simulated broadcast loop, only running in SIMULATED mode."""
    logging.info("Starting simulated market data broadcast loop...")
    tick_count = 0
    while True:
        try:
            # 1. Update prices and order books
            data = {}
            for symbol in feed.symbols:
                market_data = feed.get_market_data(symbol)
                data[symbol] = market_data
                
                # Feed new data to bot manager
                candles = feed.get_candles(symbol)
                if candles:
                    await bot_manager.process_market_tick(symbol, candles)
                
            # 2. Match any pending limit orders based on updated prices
            fills = oms.match_pending_orders()
            
            # 3. Check for server-side SL/TP triggers
            sl_tp_fills, sl_tp_logs = oms.check_sl_tp_triggers()
            
            # Broadcast any triggered SL/TP log actions
            if sl_tp_logs:
                for log_msg in sl_tp_logs:
                    logging.info(log_msg)
                    await manager.broadcast({
                        "type": "bot_log",
                        "data": log_msg
                    })
            
            # Combine fills
            total_fills = fills + sl_tp_fills
            
            # 4. Formulate the update payload
            payload = {
                "type": "market_update",
                "data": data
            }
            await manager.broadcast(payload)
                
            # If any limit orders or SL/TP got filled, broadcast the account state update
            if total_fills:
                logging.info(f"Orders matched/filled: {total_fills}")
                await manager.broadcast({
                    "type": "account_update",
                    "data": oms.get_account_data()
                })
                await manager.broadcast({
                    "type": "trade_history",
                    "data": oms.get_trade_history()
                })
                
            # 5. Periodically broadcast diagnostics stats
            tick_count += 1
            if tick_count % 12 == 0:
                from app.database import get_db_stats
                stats = get_db_stats()
                stats["clients"] = len(manager.connected_clients)
                stats["tick_interval"] = feed.tick_interval
                stats["volatility_multiplier"] = feed.volatility_multiplier
                await manager.broadcast({
                    "type": "system_stats",
                    "data": stats
                })
                
        except Exception as e:
            logging.error(f"Error in simulated broadcast loop: {str(e)}")
            
        await asyncio.sleep(feed.tick_interval)

async def diagnostics_broadcast_loop():
    """Periodic diagnostics updates for live/external feeds."""
    while True:
        try:
            from app.database import get_db_stats
            stats = get_db_stats()
            stats["clients"] = len(manager.connected_clients)
            stats["tick_interval"] = 1.0 # fixed L1 tick
            stats["volatility_multiplier"] = 1.0
            await manager.broadcast({
                "type": "system_stats",
                "data": stats
            })
        except Exception as e:
            logging.error(f"Error in diagnostics loop: {str(e)}")
        await asyncio.sleep(5)

async def websocket_handler(websocket):
    """Manages WebSocket connection lifecycle."""
    logging.info("New client connected.")
    manager.register(websocket)
    
    # Send terminal mode handshake configuration
    await manager.send_to(websocket, {
        "type": "terminal_config",
        "data": {
            "terminalMode": TERMINAL_MODE,
            "symbols": list(feed.symbols)
        }
    })
    
    # 1. Historical candles are now lazy-loaded on client request (via subscribe_symbol)
    
    
    # 2. Send current account snapshot
    account_payload = {
        "type": "account_update",
        "data": oms.get_account_data()
    }
    await manager.send_to(websocket, account_payload)
    
    # 3. Send initial trade history
    history_payload = {
        "type": "trade_history",
        "data": oms.get_trade_history()
    }
    await manager.send_to(websocket, history_payload)
    
    # 4. Send bot logs history
    logs_payload = {
        "type": "bot_logs_history",
        "data": bot_manager.get_recent_logs(100)
    }
    await manager.send_to(websocket, logs_payload)
    
    try:
        async for message_str in websocket:
            await handle_client_message(websocket, message_str, oms, manager, bot_manager, backtester_service)
    except websockets.exceptions.ConnectionClosed:
        logging.info("Client connection closed.")
    finally:
        manager.unregister(websocket)

async def main():
    # Ensure database schema is set up
    init_db()
    
    # Load active bots now that the db schema is guaranteed to exist
    bot_manager.load_bots_from_db()
    
    # Start the Feed and OMS engines
    await feed.start()
    await oms.initialize()
    
    # Start WebSocket Server on defined host/port
    logging.info(f"WebSocket Server listening on ws://{WS_HOST}:{WS_PORT}")
    
    async with websockets.serve(
        websocket_handler, WS_HOST, WS_PORT, max_size=WS_MAX_MESSAGE_SIZE
    ) as server:
        tasks = []
        if TERMINAL_MODE == "SIMULATED":
            tasks.append(asyncio.create_task(simulated_market_loop()))
        else:
            tasks.append(asyncio.create_task(diagnostics_broadcast_loop()))
        
        # Keep the server running forever and print heartbeat
        while True:
            await asyncio.sleep(10)
            logging.info("Heartbeat: Server is running...")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server stopped.")
