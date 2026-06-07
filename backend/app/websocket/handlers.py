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
            
        elif action == "admin_set_simulation":
            tick_interval = message.get("tick_interval")
            volatility_multiplier = message.get("volatility_multiplier")
            symbol = message.get("symbol")
            bias = message.get("bias")
            
            if tick_interval is not None:
                oms.simulator.tick_interval = float(tick_interval)
            if volatility_multiplier is not None:
                oms.simulator.volatility_multiplier = float(volatility_multiplier)
            if symbol and bias:
                oms.simulator.biases[symbol] = bias
                logging.info(f"Admin override simulation for {symbol}: bias={bias}")
                
            await manager.send_to(websocket, {
                "type": "order_result",
                "data": {"status": "success", "message": "Simulation settings updated"}
            })

        elif action == "admin_seed_balance":
            asset = message.get("asset")
            amount = float(message.get("amount", 0.0))
            
            from app.database import get_connection
            conn = get_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT COUNT(*) FROM accounts WHERE asset = ?", (asset,))
                if cursor.fetchone()[0] > 0:
                    cursor.execute("UPDATE accounts SET balance = balance + ? WHERE asset = ?", (amount, asset))
                else:
                    cursor.execute("INSERT INTO accounts (asset, balance, locked) VALUES (?, ?, 0.0)", (asset, amount))
                conn.commit()
                msg = f"Seeded {amount:.2f} {asset} successfully"
                status = "success"
            except Exception as e:
                conn.rollback()
                msg = f"Failed to seed balance: {str(e)}"
                status = "error"
            finally:
                conn.close()
                
            await manager.send_to(websocket, {
                "type": "order_result",
                "data": {"status": status, "message": msg}
            })
            await manager.send_to(websocket, {
                "type": "account_update",
                "data": oms.get_account_data()
            })

        elif action == "admin_reset_system":
            from app.database import reset_db
            try:
                reset_db()
                oms.simulator.tick_interval = 0.25
                oms.simulator.volatility_multiplier = 1.0
                oms.simulator.biases.clear()
                msg = "System database reset successfully to defaults"
                status = "success"
            except Exception as e:
                msg = f"Failed to reset database: {str(e)}"
                status = "error"
                
            await manager.broadcast({
                "type": "order_result",
                "data": {"status": status, "message": msg}
            })
            await manager.broadcast({
                "type": "account_update",
                "data": oms.get_account_data()
            })
            await manager.broadcast({
                "type": "trade_history",
                "data": oms.get_trade_history()
            })

        elif action == "admin_get_stats":
            from app.database import get_db_stats
            stats = get_db_stats()
            stats["tick_interval"] = oms.simulator.tick_interval
            stats["volatility_multiplier"] = oms.simulator.volatility_multiplier
            await manager.send_to(websocket, {
                "type": "system_stats",
                "data": stats
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
