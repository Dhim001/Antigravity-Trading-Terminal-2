import json
import logging
from app.services.oms import OrderManager
from app.websocket.connection_manager import ConnectionManager

async def handle_client_message(websocket, message_str, oms: OrderManager, manager: ConnectionManager):
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
            await manager.send_to(websocket, {
                "type": "order_result",
                "data": result
            })
            
            # Push account update on change
            await manager.send_to(websocket, {
                "type": "account_update",
                "data": oms.get_account_data()
            })
            
            # Push trade history update on change
            await manager.send_to(websocket, {
                "type": "trade_history",
                "data": oms.get_trade_history()
            })
            
        elif action == "cancel_order":
            order_id = message.get("order_id")
            result = oms.cancel_order(order_id)
            
            await manager.send_to(websocket, {
                "type": "order_result",
                "data": result
            })
            
            # Push account update on change
            await manager.send_to(websocket, {
                "type": "account_update",
                "data": oms.get_account_data()
            })
            
            # Push trade history update on change
            await manager.send_to(websocket, {
                "type": "trade_history",
                "data": oms.get_trade_history()
            })
            
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
            await manager.send_to(websocket, {
                "type": "order_result",
                "data": result
            })
            
            # Push account update on change
            await manager.send_to(websocket, {
                "type": "account_update",
                "data": oms.get_account_data()
            })
            
        elif action == "get_account":
            # Direct request for account info
            await manager.send_to(websocket, {
                "type": "account_update",
                "data": oms.get_account_data()
            })

        elif action == "get_history":
            # Full trade history with FIFO realized P&L — sent only to requesting client
            await manager.send_to(websocket, {
                "type": "trade_history",
                "data": oms.get_trade_history()
            })
            
        else:
            await manager.send_to(websocket, {
                "type": "error",
                "message": f"Unknown action: {action}"
            })
            
    except Exception as e:
        logging.error(f"Error processing client message: {str(e)}")
        await manager.send_to(websocket, {
            "type": "error",
            "message": f"Request processing failed: {str(e)}"
        })
