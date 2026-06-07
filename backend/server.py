import asyncio
import json
import logging
import websockets
from database import init_db
from feed_simulator import FeedSimulator
from oms import OrderManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Global instances
simulator = FeedSimulator()
oms = OrderManager(simulator)
connected_clients = set()

# Configuration placeholder for future live integration
USE_LIVE_FEEDS = False # Set to True to connect to live Binance/Alpaca APIs in the future

async def broadcast_market_data():
    """Background task that tick-updates market data and broadcasts to clients."""
    logging.info("Starting market data broadcast loop...")
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
            if sl_tp_logs and connected_clients:
                for log_msg in sl_tp_logs:
                    logging.info(log_msg)
                    log_payload = {
                        "type": "bot_log",
                        "data": log_msg
                    }
                    log_message = json.dumps(log_payload)
                    await asyncio.gather(
                        *[client.send(log_message) for client in connected_clients],
                        return_exceptions=True
                    )
            
            # Combine fills
            total_fills = fills + sl_tp_fills
            
            # 4. Formulate the update payload
            payload = {
                "type": "market_update",
                "data": data
            }
            
            # Broadcast to all connected clients
            if connected_clients:
                message = json.dumps(payload)
                await asyncio.gather(
                    *[client.send(message) for client in connected_clients],
                    return_exceptions=True
                )
                
            # If any limit orders or SL/TP got filled, broadcast the account state update
            if total_fills and connected_clients:
                logging.info(f"Orders matched/filled: {total_fills}")
                account_payload = {
                    "type": "account_update",
                    "data": oms.get_account_data()
                }
                account_msg = json.dumps(account_payload)
                history_payload = {
                    "type": "trade_history",
                    "data": oms.get_trade_history()
                }
                history_msg = json.dumps(history_payload)
                await asyncio.gather(
                    *[client.send(account_msg) for client in connected_clients],
                    *[client.send(history_msg) for client in connected_clients],
                    return_exceptions=True
                )
                
        except Exception as e:
            logging.error(f"Error in broadcast loop: {str(e)}")
            
        await asyncio.sleep(0.25) # 4 ticks per second

async def handle_client_message(websocket, message_str):
    """Processes messages received from clients (order entries, cancellations, etc.)"""
    try:
        message = json.loads(message_str)
        action = message.get("action")
        logging.info(f"Received action: {action} from client")
        
        if action == "place_order":
            symbol = message.get("symbol")
            order_type = message.get("type")
            side = message.get("side")
            price = float(message.get("price")) if message.get("price") is not None else None
            quantity = float(message.get("quantity"))
            
            stop_loss_percent = message.get("stop_loss_percent")
            take_profit_percent = message.get("take_profit_percent")
            if stop_loss_percent is not None:
                stop_loss_percent = float(stop_loss_percent)
            if take_profit_percent is not None:
                take_profit_percent = float(take_profit_percent)
                
            result = oms.place_order(symbol, order_type, side, price, quantity, stop_loss_percent, take_profit_percent)
            
            # Send result notification
            await websocket.send(json.dumps({
                "type": "order_result",
                "data": result
            }))
            
            # Push account update on change
            await websocket.send(json.dumps({
                "type": "account_update",
                "data": oms.get_account_data()
            }))
            
            # Push trade history update on change
            await websocket.send(json.dumps({
                "type": "trade_history",
                "data": oms.get_trade_history()
            }))
            
        elif action == "cancel_order":
            order_id = message.get("order_id")
            result = oms.cancel_order(order_id)
            
            await websocket.send(json.dumps({
                "type": "order_result",
                "data": result
            }))
            
            # Push account update on change
            await websocket.send(json.dumps({
                "type": "account_update",
                "data": oms.get_account_data()
            }))
            
            # Push trade history update on change
            await websocket.send(json.dumps({
                "type": "trade_history",
                "data": oms.get_trade_history()
            }))
            
        elif action == "update_position_sl_tp":
            symbol = message.get("symbol")
            stop_loss_percent = message.get("stop_loss_percent")
            take_profit_percent = message.get("take_profit_percent")
            if stop_loss_percent is not None:
                stop_loss_percent = float(stop_loss_percent)
            if take_profit_percent is not None:
                take_profit_percent = float(take_profit_percent)
                
            result = oms.update_position_sl_tp(symbol, stop_loss_percent, take_profit_percent)
            
            # Send result notification
            await websocket.send(json.dumps({
                "type": "order_result",
                "data": result
            }))
            
            # Push account update on change
            await websocket.send(json.dumps({
                "type": "account_update",
                "data": oms.get_account_data()
            }))
            
        elif action == "get_account":
            # Direct request for account info
            await websocket.send(json.dumps({
                "type": "account_update",
                "data": oms.get_account_data()
            }))

        elif action == "get_history":
            # Full trade history with FIFO realized P&L — sent only to requesting client
            await websocket.send(json.dumps({
                "type": "trade_history",
                "data": oms.get_trade_history()
            }))
            
        else:
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"Unknown action: {action}"
            }))
            
    except Exception as e:
        logging.error(f"Error processing client message: {str(e)}")
        await websocket.send(json.dumps({
            "type": "error",
            "message": f"Request processing failed: {str(e)}"
        }))

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
        # Example implementation:
        # streams = "/".join([f"{s.lower()}@ticker/{s.lower()}@depth10" for s in symbols])
        # async with websockets.connect(f"{self.binance_ws_url}/{streams}") as ws:
        #     while True:
        #         msg = await ws.recv()
        #         self.normalize_and_broadcast(msg)
                
    def normalize_and_broadcast(self, raw_msg):
        """Adapts Binance stream messages into internal market_update formats."""
        pass
# =====================================================================

async def websocket_handler(websocket):
    """Manages WebSocket connection lifecycle."""
    logging.info("New client connected.")
    connected_clients.add(websocket)
    
    # 1. Send initial historical candles to let chart pre-render
    history_payload = {
        "type": "history_update",
        "data": {symbol: simulator.candles[symbol] for symbol in simulator.symbols}
    }
    await websocket.send(json.dumps(history_payload))
    
    # 2. Send current account snapshot
    account_payload = {
        "type": "account_update",
        "data": oms.get_account_data()
    }
    await websocket.send(json.dumps(account_payload))
    
    try:
        async for message_str in websocket:
            await handle_client_message(websocket, message_str)
    except websockets.exceptions.ConnectionClosed:
        logging.info("Client connection closed.")
    finally:
        connected_clients.remove(websocket)

async def main():
    # Ensure database schema is set up
    init_db()
    
    # Start WebSocket Server on port 8765
    server = await websockets.serve(websocket_handler, "localhost", 8765)
    logging.info("WebSocket Server listening on ws://localhost:8765")
    
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
