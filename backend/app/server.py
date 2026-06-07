import asyncio
import logging
import websockets
from app.config import WS_HOST, WS_PORT
from app.database import init_db
from app.services.feed_simulator import FeedSimulator
from app.services.oms import OrderManager
from app.websocket.connection_manager import ConnectionManager
from app.websocket.handlers import handle_client_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Global instances
simulator = FeedSimulator()
oms = OrderManager(simulator)
manager = ConnectionManager()

async def broadcast_market_data():
    """Background task that tick-updates market data and broadcasts to clients."""
    logging.info("Starting market data broadcast loop...")
    tick_count = 0
    while True:
        try:
            # 1. Update prices and order books
            data = {}
            for symbol in simulator.symbols:
                data[symbol] = simulator.get_market_data(symbol)
                
            # 2. Match any pending limit orders based on updated prices
            fills = oms.match_pending_orders()
            
            # 3. Check for server-side SL/TP triggers
            sl_tp_fills, sl_tp_logs = oms.check_sl_tp_triggers()
            
            # Broadcast any triggered SL/TP log actions
            if sl_tp_logs:
                for log_msg in sl_tp_logs:
                    logging.info(log_msg)
                    log_payload = {
                        "type": "bot_log",
                        "data": log_msg
                    }
                    await manager.broadcast(log_payload)
            
            # Combine fills
            total_fills = fills + sl_tp_fills
            
            # 4. Formulate the update payload
            payload = {
                "type": "market_update",
                "data": data
            }
            
            # Broadcast market update to all connected clients
            await manager.broadcast(payload)
                
            # If any limit orders or SL/TP got filled, broadcast the account state update
            if total_fills:
                logging.info(f"Orders matched/filled: {total_fills}")
                account_payload = {
                    "type": "account_update",
                    "data": oms.get_account_data()
                }
                history_payload = {
                    "type": "trade_history",
                    "data": oms.get_trade_history()
                }
                await manager.broadcast(account_payload)
                await manager.broadcast(history_payload)
                
            # 5. Periodically broadcast diagnostics stats (every 12 ticks, ~3s at default speed)
            tick_count += 1
            if tick_count % 12 == 0:
                from app.database import get_db_stats
                stats = get_db_stats()
                stats["clients"] = len(manager.connected_clients)
                stats["tick_interval"] = simulator.tick_interval
                stats["volatility_multiplier"] = simulator.volatility_multiplier
                await manager.broadcast({
                    "type": "system_stats",
                    "data": stats
                })
                
        except Exception as e:
            logging.error(f"Error in broadcast loop: {str(e)}")
            
        await asyncio.sleep(simulator.tick_interval)

# =====================================================================
# Future Adaptation: Live API Feed Integration Interface
# =====================================================================
class LiveFeedAdapter:
    """This class stub illustrates how to extend the server to connect 
    to live WebSockets in production (e.g. Binance / Alpaca APIs).
    """
    def __init__(self, binance_ws_url="wss://stream.binance.com:9443/ws", alpaca_ws_url="wss://paper-api.alpaca.markets/stream"):
        self.binance_ws_url = binance_ws_url
        self.alpaca_ws_url = alpaca_ws_url
        self.active = False
        
    async def connect_binance_stream(self, symbols):
        """Connects to Binance WebSockets for live Level 1 and Level 2 streams."""
        if not self.active:
            logging.info("Binance stream adapter is ready. Configure API keys to activate.")
            return
                
    def normalize_and_broadcast(self, raw_msg):
        """Adapts Binance stream messages into internal market_update formats."""
        pass
# =====================================================================

async def websocket_handler(websocket):
    """Manages WebSocket connection lifecycle."""
    logging.info("New client connected.")
    manager.register(websocket)
    
    # 1. Send initial historical candles to let chart pre-render
    history_payload = {
        "type": "history_update",
        "data": {symbol: simulator.candles[symbol] for symbol in simulator.symbols}
    }
    await manager.send_to(websocket, history_payload)
    
    # 2. Send current account snapshot
    account_payload = {
        "type": "account_update",
        "data": oms.get_account_data()
    }
    await manager.send_to(websocket, account_payload)
    
    try:
        async for message_str in websocket:
            await handle_client_message(websocket, message_str, oms, manager)
    except websockets.exceptions.ConnectionClosed:
        logging.info("Client connection closed.")
    finally:
        manager.unregister(websocket)

async def main():
    # Ensure database schema is set up
    init_db()
    
    # Start WebSocket Server on defined host/port
    server = await websockets.serve(websocket_handler, WS_HOST, WS_PORT)
    logging.info(f"WebSocket Server listening on ws://{WS_HOST}:{WS_PORT}")
    
    # Run the broadcast loop concurrently
    await asyncio.gather(
        server.wait_closed(),
        broadcast_market_data()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server stopped.")
