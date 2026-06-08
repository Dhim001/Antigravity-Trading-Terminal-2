import json
import logging
from app.services.base_oms import BaseOMSService
from app.websocket.connection_manager import ConnectionManager

async def handle_client_message(websocket, message_str, oms: BaseOMSService, manager: ConnectionManager):
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
            stop_loss_price = message.get("stop_loss_price")
            take_profit_price = message.get("take_profit_price")

            # Map absolute prices (from the UI order entry ticket) to percentages for the OMS
            ref_price = price if price is not None and price > 0 else None
            if ref_price is None and hasattr(oms, "feed") and symbol in oms.feed._symbols:
                ref_price = oms.feed._symbols[symbol]["price"]

            if ref_price and ref_price > 0:
                if stop_loss_price is not None and stop_loss_percent is None:
                    stop_loss_percent = round(abs(ref_price - float(stop_loss_price)) / ref_price * 100, 2)
                if take_profit_price is not None and take_profit_percent is None:
                    take_profit_percent = round(abs(ref_price - float(take_profit_price)) / ref_price * 100, 2)

            if stop_loss_percent is not None:
                stop_loss_percent = float(stop_loss_percent)
            if take_profit_percent is not None:
                take_profit_percent = float(take_profit_percent)
                
            result = await oms.place_order({
                "symbol": symbol,
                "type": order_type,
                "side": side,
                "price": price,
                "quantity": quantity,
                "stop_loss_percent": stop_loss_percent,
                "take_profit_percent": take_profit_percent
            })
            
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
            result = await oms.cancel_order(order_id)
            
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
                
            result = await oms.update_position_sl_tp(symbol, stop_loss_percent, take_profit_percent)
            
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
            # Full trade history — sent only to requesting client
            await manager.send_to(websocket, {
                "type": "trade_history",
                "data": oms.get_trade_history()
            })
            
        elif action == "admin_set_simulation":
            tick_interval = message.get("tick_interval")
            volatility_multiplier = message.get("volatility_multiplier")
            symbol = message.get("symbol")
            bias = message.get("bias")
            
            success = False
            if hasattr(oms, "feed") and hasattr(oms.feed, "tick_interval"):
                if tick_interval is not None:
                    oms.feed.tick_interval = float(tick_interval)
                if volatility_multiplier is not None:
                    oms.feed.volatility_multiplier = float(volatility_multiplier)
                if symbol and bias:
                    oms.feed.biases[symbol] = bias
                    logging.info(f"Admin override simulation for {symbol}: bias={bias}")
                success = True
                
            await manager.send_to(websocket, {
                "type": "order_result",
                "data": {
                    "status": "success" if success else "error", 
                    "message": "Simulation settings updated" if success else "Simulation controls disabled in live trading mode"
                }
            })

        elif action == "admin_seed_balance":
            from app.config import TERMINAL_MODE
            if TERMINAL_MODE != "SIMULATED":
                await manager.send_to(websocket, {
                    "type": "order_result",
                    "data": {"status": "error", "message": "Manual balance seeding is disabled in live trading mode."}
                })
                return

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
            from app.config import TERMINAL_MODE
            if TERMINAL_MODE != "SIMULATED":
                await manager.send_to(websocket, {
                    "type": "order_result",
                    "data": {"status": "error", "message": "System nuclear reset is disabled in live trading mode."}
                })
                return

            from app.database import reset_db
            try:
                reset_db()
                if hasattr(oms, "feed"):
                    oms.feed.tick_interval = 0.25
                    oms.feed.volatility_multiplier = 1.0
                    oms.feed.biases.clear()
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

        elif action == "admin_emergency_stop":
            result = await oms.emergency_stop()
            await manager.send_to(websocket, {
                "type": "order_result",
                "data": result
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
            if hasattr(oms, "feed") and hasattr(oms.feed, "tick_interval"):
                stats["tick_interval"] = oms.feed.tick_interval
                stats["volatility_multiplier"] = oms.feed.volatility_multiplier
            else:
                stats["tick_interval"] = 1.0
                stats["volatility_multiplier"] = 1.0
                
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
