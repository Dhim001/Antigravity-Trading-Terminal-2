import uuid
import time
from app.database import get_connection
from app.config import MAX_ORDER_VALUE

class OrderManager:
    def __init__(self, simulator):
        self.simulator = simulator

    def get_account_data(self):
        """Returns current balances, open positions, and orders list."""
        conn = get_connection()
        cursor = conn.cursor()
        
        # Get balances
        cursor.execute("SELECT asset, balance, locked FROM accounts")
        balances = {row["asset"]: {"balance": row["balance"], "locked": row["locked"]} for row in cursor.fetchall()}
        
        # Get positions
        cursor.execute("SELECT symbol, size, avg_price, stop_loss_percent, take_profit_percent, stop_loss_price, take_profit_price FROM positions WHERE size != 0.0")
        positions = {
            row["symbol"]: {
                "size": row["size"],
                "avg_price": row["avg_price"],
                "stop_loss_percent": row["stop_loss_percent"],
                "take_profit_percent": row["take_profit_percent"],
                "stop_loss_price": row["stop_loss_price"],
                "take_profit_price": row["take_profit_price"]
            } for row in cursor.fetchall()
        }
        
        # Get orders (limit 50 for live blotter panel)
        cursor.execute("SELECT id, symbol, type, side, price, quantity, status, filled_quantity, average_fill_price, timestamp FROM orders ORDER BY timestamp DESC LIMIT 50")
        orders = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        return {
            "balances": balances,
            "positions": positions,
            "orders": orders
        }

    def get_trade_history(self):
        """
        Returns full order history with FIFO-computed realized P&L per fill.
        Algorithm:
          - Walk all FILLED orders chronologically (oldest first).
          - Maintain a per-symbol FIFO cost-basis queue of (price, qty) buy lots.
          - When a SELL fill arrives, dequeue buy lots FIFO and calculate realized P&L.
          - Returns enriched trade records plus aggregate statistics.
        """
        conn = get_connection()
        cursor = conn.cursor()

        # Fetch all orders (no limit) chronologically for FIFO matching
        cursor.execute("""
            SELECT id, symbol, type, side, price, quantity, status,
                   filled_quantity, average_fill_price, timestamp
            FROM orders
            ORDER BY timestamp ASC
        """)
        all_orders = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # ── FIFO cost-basis engine ────────────────────────────────────────
        # cost_queues[symbol] = list of [price, remaining_qty]  (FIFO)
        cost_queues = {}
        enriched    = []

        for order in all_orders:
            sym    = order["symbol"]
            side   = order["side"]
            status = order["status"]
            fill_price = order["average_fill_price"] or 0.0
            fill_qty   = order["filled_quantity"]    or 0.0

            realized_pnl  = None
            cost_basis    = None

            if status == "FILLED" and fill_qty > 0:
                if sym not in cost_queues:
                    cost_queues[sym] = []

                if side == "BUY":
                    # Push onto cost queue
                    cost_queues[sym].append([fill_price, fill_qty])

                elif side == "SELL":
                    # FIFO dequeue to compute realized P&L
                    queue       = cost_queues.get(sym, [])
                    remaining   = fill_qty
                    total_cost  = 0.0
                    total_qty   = 0.0

                    while remaining > 1e-9 and queue:
                        lot_price, lot_qty = queue[0]
                        used = min(lot_qty, remaining)
                        total_cost += lot_price * used
                        total_qty  += used
                        remaining  -= used
                        queue[0][1] -= used
                        if queue[0][1] < 1e-9:
                            queue.pop(0)

                    if total_qty > 0:
                        cost_basis   = total_cost / total_qty
                        realized_pnl = (fill_price - cost_basis) * fill_qty

            trade_value = (fill_price * fill_qty) if fill_qty > 0 else (
                (order["price"] or 0.0) * order["quantity"]
            )

            enriched.append({
                **order,
                "realized_pnl":  round(realized_pnl, 4) if realized_pnl is not None else None,
                "cost_basis":    round(cost_basis,   4) if cost_basis   is not None else None,
                "trade_value":   round(trade_value,  4),
            })

        # Reverse for newest-first display
        enriched.reverse()

        # ── Aggregate statistics ──────────────────────────────────────────
        filled_sells = [t for t in enriched if t["status"] == "FILLED" and t["side"] == "SELL" and t["realized_pnl"] is not None]
        filled_all   = [t for t in enriched if t["status"] == "FILLED"]

        total_pnl    = sum(t["realized_pnl"] for t in filled_sells)
        wins         = [t for t in filled_sells if t["realized_pnl"] > 0]
        losses       = [t for t in filled_sells if t["realized_pnl"] < 0]
        win_rate     = (len(wins) / len(filled_sells) * 100) if filled_sells else 0.0
        gross_volume = sum(t["trade_value"] for t in filled_all)
        best_trade   = max((t["realized_pnl"] for t in filled_sells), default=0.0)
        worst_trade  = min((t["realized_pnl"] for t in filled_sells), default=0.0)
        avg_win      = (sum(t["realized_pnl"] for t in wins)   / len(wins))   if wins   else 0.0
        avg_loss     = (sum(t["realized_pnl"] for t in losses) / len(losses)) if losses else 0.0
        profit_factor = (abs(sum(t["realized_pnl"] for t in wins)) /
                         abs(sum(t["realized_pnl"] for t in losses))) if losses else None

        stats = {
            "total_pnl":     round(total_pnl,    2),
            "win_rate":      round(win_rate,      1),
            "total_fills":   len(filled_all),
            "total_sells":   len(filled_sells),
            "wins":          len(wins),
            "losses":        len(losses),
            "gross_volume":  round(gross_volume,  2),
            "best_trade":    round(best_trade,    2),
            "worst_trade":   round(worst_trade,   2),
            "avg_win":       round(avg_win,       2),
            "avg_loss":      round(avg_loss,      2),
            "profit_factor": round(profit_factor, 2) if profit_factor else None,
        }

        return {"trades": enriched, "stats": stats}

    def place_order(self, symbol, order_type, side, price, quantity, stop_loss_percent=None, take_profit_percent=None):
        """Places an order after running risk checks, fills markets immediately."""
        if symbol not in self.simulator.symbols:
            return {"status": "error", "message": f"Invalid symbol: {symbol}"}
            
        if quantity <= 0:
            return {"status": "error", "message": "Quantity must be greater than 0"}

        # Fetch current price from simulator
        market_price = self.simulator.symbols[symbol]["price"]
        order_price = price if order_type == "LIMIT" else market_price
        
        if order_type == "LIMIT" and (price is None or price <= 0):
            return {"status": "error", "message": "Limit price must be greater than 0"}

        # Total order cost estimate
        order_value = order_price * quantity
        
        # Pre-Trade Risk Check 1: Max Order Value limit
        if order_value > MAX_ORDER_VALUE:
            return {"status": "error", "message": f"Order value exceeds maximum risk limit of ${MAX_ORDER_VALUE}"}
            
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            asset = self.simulator.symbols[symbol]["asset"]
            quote = self.simulator.symbols[symbol]["quote"]
            
            # Pre-Trade Risk Check 2: Balance/Holdings Check
            if side == "BUY":
                # For buys, we check if the quote asset (e.g. USD/USDT) has enough balance
                cursor.execute("SELECT balance, locked FROM accounts WHERE asset = ?", (quote,))
                row = cursor.fetchone()
                if not row or (row["balance"] - row["locked"]) < order_value:
                    conn.close()
                    return {"status": "error", "message": f"Insufficient {quote} balance. Needed: {order_value:.2f}"}
                    
                # Lock the funds for LIMIT order
                if order_type == "LIMIT":
                    cursor.execute("UPDATE accounts SET locked = locked + ? WHERE asset = ?", (order_value, quote))
                    
            elif side == "SELL":
                # For sells, we check if the base asset (e.g. BTC, AAPL) has enough available size in positions
                cursor.execute("SELECT size FROM positions WHERE symbol = ?", (symbol,))
                row = cursor.fetchone()
                position_size = row["size"] if row else 0.0
                
                # Check locked quantities (we can count pending limit sells)
                cursor.execute("SELECT SUM(quantity) FROM orders WHERE symbol = ? AND side = 'SELL' AND status = 'PENDING'", (symbol,))
                locked_row = cursor.fetchone()
                locked_qty = locked_row[0] if locked_row[0] is not None else 0.0
                
                if (position_size - locked_qty) < quantity:
                    conn.close()
                    return {"status": "error", "message": f"Insufficient {symbol} position size to sell. Available: {position_size - locked_qty}"}

            # Create Order Record
            order_id = str(uuid.uuid4())
            status = "PENDING" if order_type == "LIMIT" else "FILLED"
            
            cursor.execute("""
                INSERT INTO orders (id, symbol, type, side, price, quantity, status, filled_quantity, average_fill_price, stop_loss_percent, take_profit_percent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_id, symbol, order_type, side, 
                price if order_type == "LIMIT" else None, 
                quantity, status,
                quantity if order_type == "MARKET" else 0.0,
                market_price if order_type == "MARKET" else 0.0,
                stop_loss_percent,
                take_profit_percent
            ))
            
            # If it's a MARKET order, process the balance/position update immediately
            if order_type == "MARKET":
                self._process_fill(cursor, symbol, side, market_price, quantity, quote, stop_loss_percent, take_profit_percent)
                
            conn.commit()
            conn.close()
            
            return {
                "status": "success",
                "message": f"Order placed: {side} {quantity} {symbol} @ {order_price}",
                "order_id": order_id
            }
            
        except Exception as e:
            conn.rollback()
            conn.close()
            return {"status": "error", "message": f"Database transaction error: {str(e)}"}

    def cancel_order(self, order_id):
        """Cancels a pending order and releases locked funds."""
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT symbol, type, side, price, quantity, status FROM orders WHERE id = ?", (order_id,))
        order = cursor.fetchone()
        
        if not order:
            conn.close()
            return {"status": "error", "message": "Order not found"}
            
        if order["status"] != "PENDING":
            conn.close()
            return {"status": "error", "message": f"Cannot cancel order with status: {order['status']}"}
            
        try:
            symbol = order["symbol"]
            quote = self.simulator.symbols[symbol]["quote"]
            
            # Update order status
            cursor.execute("UPDATE orders SET status = 'CANCELED' WHERE id = ?", (order_id,))
            
            # Release locked funds if BUY LIMIT
            if order["side"] == "BUY" and order["type"] == "LIMIT":
                release_val = order["price"] * order["quantity"]
                cursor.execute("UPDATE accounts SET locked = MAX(0.0, locked - ?) WHERE asset = ?", (release_val, quote))
                
            conn.commit()
            conn.close()
            return {"status": "success", "message": f"Order {order_id} canceled successfully"}
        except Exception as e:
            conn.rollback()
            conn.close()
            return {"status": "error", "message": f"Cancel transaction error: {str(e)}"}

    def match_pending_orders(self):
        """Checks and matches pending limit orders against current market prices."""
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, symbol, side, price, quantity, stop_loss_percent, take_profit_percent FROM orders WHERE status = 'PENDING'")
        pending_orders = cursor.fetchall()
        
        if not pending_orders:
            conn.close()
            return []
            
        filled_order_updates = []
        
        for order in pending_orders:
            symbol = order["symbol"]
            side = order["side"]
            limit_price = order["price"]
            qty = order["quantity"]
            
            # Current price in the simulator
            market_price = self.simulator.symbols[symbol]["price"]
            
            # Match condition
            should_fill = False
            if side == "BUY" and market_price <= limit_price:
                should_fill = True
            elif side == "SELL" and market_price >= limit_price:
                should_fill = True
                
            if should_fill:
                try:
                    quote = self.simulator.symbols[symbol]["quote"]
                    
                    # Update order record
                    cursor.execute("""
                        UPDATE orders 
                        SET status = 'FILLED', filled_quantity = ?, average_fill_price = ?
                        WHERE id = ?
                    """, (qty, market_price, order["id"]))
                    
                    # Release locked funds if BUY
                    if side == "BUY":
                        locked_val = limit_price * qty
                        # Update locked balance
                        cursor.execute("UPDATE accounts SET locked = MAX(0.0, locked - ?) WHERE asset = ?", (locked_val, quote))
                        
                    # Process balance and position update
                    self._process_fill(cursor, symbol, side, market_price, qty, quote, order["stop_loss_percent"], order["take_profit_percent"])
                    
                    filled_order_updates.append({
                        "id": order["id"],
                        "symbol": symbol,
                        "side": side,
                        "price": market_price,
                        "quantity": qty
                    })
                    
                except Exception as e:
                    print(f"Error matching order {order['id']}: {str(e)}")
                    
        if filled_order_updates:
            conn.commit()
            
        conn.close()
        return filled_order_updates

    def _process_fill(self, cursor, symbol, side, price, quantity, quote, stop_loss_percent=None, take_profit_percent=None):
        """Internal method to update accounts and positions tables during a fill.
        Must be run within an active transaction using a shared cursor.
        """
        base_asset = self.simulator.symbols[symbol]["asset"]
        order_value = price * quantity
        
        # 1. Update balances
        if side == "BUY":
            # Deduct quote balance (buying power)
            cursor.execute("UPDATE accounts SET balance = balance - ? WHERE asset = ?", (order_value, quote))
            # Credit base asset balance (for tracking crypto wallets)
            cursor.execute("UPDATE accounts SET balance = balance + ? WHERE asset = ?", (quantity, base_asset))
        else:
            # Credit quote balance (sale proceeds)
            cursor.execute("UPDATE accounts SET balance = balance + ? WHERE asset = ?", (order_value, quote))
            # Deduct base asset balance
            cursor.execute("UPDATE accounts SET balance = MAX(0.0, balance - ?) WHERE asset = ?", (quantity, base_asset))

        # 2. Update positions
        cursor.execute("SELECT size, avg_price, stop_loss_percent, take_profit_percent FROM positions WHERE symbol = ?", (symbol,))
        pos_row = cursor.fetchone()
        
        if not pos_row:
            # Create new position
            new_size = quantity if side == "BUY" else -quantity
            
            sl_price = None
            tp_price = None
            if stop_loss_percent is not None:
                if new_size > 0:
                    sl_price = price * (1 - stop_loss_percent / 100)
                else:
                    sl_price = price * (1 + stop_loss_percent / 100)
            if take_profit_percent is not None:
                if new_size > 0:
                    tp_price = price * (1 + take_profit_percent / 100)
                else:
                    tp_price = price * (1 - take_profit_percent / 100)
                    
            cursor.execute("""
                INSERT INTO positions (symbol, size, avg_price, stop_loss_percent, take_profit_percent, stop_loss_price, take_profit_price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (symbol, new_size, price, stop_loss_percent, take_profit_percent, sl_price, tp_price))
        else:
            current_size = pos_row["size"]
            current_avg = pos_row["avg_price"]
            
            sl_pct = stop_loss_percent if stop_loss_percent is not None else pos_row["stop_loss_percent"]
            tp_pct = take_profit_percent if take_profit_percent is not None else pos_row["take_profit_percent"]
            
            # Position calculations
            if side == "BUY":
                new_size = current_size + quantity
                # Recalculate average price only if increasing long size or reversing short
                if current_size >= 0:
                    new_avg = ((current_size * current_avg) + order_value) / new_size if new_size > 0 else 0.0
                else:
                    # Closing short position, average cost stays same, just reduce size
                    new_avg = current_avg if new_size < 0 else (price if new_size > 0 else 0.0)
            else: # SELL
                new_size = current_size - quantity
                if current_size <= 0:
                    # Increasing short size
                    new_avg = ((abs(current_size) * current_avg) + order_value) / abs(new_size) if new_size < 0 else 0.0
                else:
                    # Closing long position, average price stays same
                    new_avg = current_avg if new_size > 0 else (price if new_size < 0 else 0.0)
            
            # If position is flat, clear average price and SL/TP fields
            if abs(new_size) < 1e-8:
                new_size = 0.0
                new_avg = 0.0
                sl_pct = None
                tp_pct = None
                sl_price = None
                tp_price = None
            else:
                # Recalculate SL/TP prices based on new average price
                sl_price = None
                tp_price = None
                if sl_pct is not None:
                    if new_size > 0:
                        sl_price = new_avg * (1 - sl_pct / 100)
                    else:
                        sl_price = new_avg * (1 + sl_pct / 100)
                if tp_pct is not None:
                    if new_size > 0:
                        tp_price = new_avg * (1 + tp_pct / 100)
                    else:
                        tp_price = new_avg * (1 - tp_pct / 100)
                
            cursor.execute("""
                UPDATE positions 
                SET size = ?, avg_price = ?, stop_loss_percent = ?, take_profit_percent = ?, stop_loss_price = ?, take_profit_price = ?
                WHERE symbol = ?
            """, (new_size, new_avg, sl_pct, tp_pct, sl_price, tp_price, symbol))

    def update_position_sl_tp(self, symbol, stop_loss_percent, take_profit_percent):
        """Updates stop loss and take profit percentages (and calculates prices) on an active position."""
        conn = get_connection()
        cursor = conn.cursor()
        
        # Check if position exists
        cursor.execute("SELECT size, avg_price FROM positions WHERE symbol = ?", (symbol,))
        pos_row = cursor.fetchone()
        
        if not pos_row or pos_row["size"] == 0.0:
            conn.close()
            return {"status": "error", "message": f"No active position for {symbol} to update SL/TP"}
            
        try:
            size = pos_row["size"]
            avg_price = pos_row["avg_price"]
            
            sl_price = None
            tp_price = None
            
            if size > 0: # Long
                sl_price = avg_price * (1 - stop_loss_percent / 100) if stop_loss_percent is not None else None
                tp_price = avg_price * (1 + take_profit_percent / 100) if take_profit_percent is not None else None
            elif size < 0: # Short
                sl_price = avg_price * (1 + stop_loss_percent / 100) if stop_loss_percent is not None else None
                tp_price = avg_price * (1 - take_profit_percent / 100) if take_profit_percent is not None else None
                
            cursor.execute("""
                UPDATE positions 
                SET stop_loss_percent = ?, take_profit_percent = ?, stop_loss_price = ?, take_profit_price = ?
                WHERE symbol = ?
            """, (stop_loss_percent, take_profit_percent, sl_price, tp_price, symbol))
            
            conn.commit()
            conn.close()
            return {"status": "success", "message": f"Updated SL/TP for {symbol}: SL={stop_loss_percent}%, TP={take_profit_percent}%"}
        except Exception as e:
            conn.rollback()
            conn.close()
            return {"status": "error", "message": f"Failed to update SL/TP: {str(e)}"}

    def check_sl_tp_triggers(self):
        """Checks active positions for SL/TP breaches and executes server-side market exits if breached."""
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT symbol, size, avg_price, stop_loss_percent, take_profit_percent, stop_loss_price, take_profit_price 
            FROM positions 
            WHERE size != 0.0 AND (stop_loss_price IS NOT NULL OR take_profit_price IS NOT NULL)
        """)
        active_positions = cursor.fetchall()
        
        if not active_positions:
            conn.close()
            return [], []
            
        filled_exits = []
        triggered_logs = []
        
        for pos in active_positions:
            symbol = pos["symbol"]
            size = pos["size"]
            avg_price = pos["avg_price"]
            sl_price = pos["stop_loss_price"]
            tp_price = pos["take_profit_price"]
            
            market_price = self.simulator.symbols[symbol]["price"]
            quote = self.simulator.symbols[symbol]["quote"]
            
            trigger_type = None # 'SL' or 'TP'
            
            # Check breach conditions
            if size > 0: # Long
                if sl_price is not None and market_price <= sl_price:
                    trigger_type = 'SL'
                elif tp_price is not None and market_price >= tp_price:
                    trigger_type = 'TP'
            elif size < 0: # Short
                if sl_price is not None and market_price >= sl_price:
                    trigger_type = 'SL'
                elif tp_price is not None and market_price <= tp_price:
                    trigger_type = 'TP'
                    
            if trigger_type:
                try:
                    # Execute server-side market exit
                    order_id = str(uuid.uuid4())
                    side = "SELL" if size > 0 else "BUY"
                    qty = abs(size)
                    
                    # Log trigger action
                    if trigger_type == 'SL':
                        log_msg = f"🚨 STOP LOSS TRIGGERED for {symbol} at ${market_price:.2f} (Avg Entry: ${avg_price:.2f}, SL limit: ${sl_price:.2f}). Exiting..."
                    else:
                        log_msg = f"🎯 TAKE PROFIT TRIGGERED for {symbol} at ${market_price:.2f} (Avg Entry: ${avg_price:.2f}, TP limit: ${tp_price:.2f}). Exiting..."
                        
                    triggered_logs.append(log_msg)
                    
                    # Create Order Record as FILLED MARKET
                    cursor.execute("""
                        INSERT INTO orders (id, symbol, type, side, price, quantity, status, filled_quantity, average_fill_price)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (order_id, symbol, "MARKET", side, None, qty, "FILLED", qty, market_price))
                    
                    # Process fill balances and positions
                    self._process_fill(cursor, symbol, side, market_price, qty, quote)
                    
                    filled_exits.append({
                        "id": order_id,
                        "symbol": symbol,
                        "side": side,
                        "price": market_price,
                        "quantity": qty
                    })
                    
                except Exception as e:
                    print(f"Error executing SL/TP exit for {symbol}: {str(e)}")
                    
        if filled_exits:
            conn.commit()
            
        conn.close()
        return filled_exits, triggered_logs
