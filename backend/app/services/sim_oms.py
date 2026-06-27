import uuid
import time
from typing import Dict, List, Any
from app.database import get_connection
from app.config import MAX_ORDER_VALUE
from app.services.base_oms import BaseOMSService
from app.services.bots import positions as bot_positions
from app.services.fifo_pnl import backfill_missing_order_pnl, enrich_orders_with_pnl, record_order_fifo_pnl

class SimulatedOMSService(BaseOMSService):
    def __init__(self, feed):
        self.feed = feed
        self._trade_history_cache: list | None = None
        self._pnl_backfill_done = False

    def invalidate_trade_history_cache(self) -> None:
        self._trade_history_cache = None

    async def initialize(self) -> None:
        pass

    def get_account_data(self) -> dict:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT asset, balance, locked FROM accounts")
        balances = {row["asset"]: {"balance": row["balance"], "locked": row["locked"]} for row in cursor.fetchall()}
        
        cursor.execute("SELECT symbol, size, avg_price, stop_loss_percent, take_profit_percent, stop_loss_price, take_profit_price FROM positions WHERE size != 0.0")
        position_rows = cursor.fetchall()

        cursor.execute(
            """
            SELECT bot_id, symbol, size
            FROM bot_positions
            WHERE ABS(size) > ?
            """,
            (1e-8,),
        )
        owners_by_symbol: dict[str, list[dict]] = {}
        for owner_row in cursor.fetchall():
            sym = owner_row["symbol"]
            owners_by_symbol.setdefault(sym, []).append({
                "bot_id": owner_row["bot_id"],
                "size": float(owner_row["size"]),
            })

        positions = {}
        for row in position_rows:
            sym = row["symbol"]
            owners = owners_by_symbol.get(sym, [])
            positions[sym] = {
                "size": row["size"],
                "avg_price": row["avg_price"],
                "stop_loss_percent": row["stop_loss_percent"],
                "take_profit_percent": row["take_profit_percent"],
                "stop_loss_price": row["stop_loss_price"],
                "take_profit_price": row["take_profit_price"],
                "bot_id": owners[0]["bot_id"] if len(owners) == 1 else None,
                "bot_owners": owners,
            }
        
        cursor.execute("SELECT id, symbol, type, side, price, quantity, status, filled_quantity, average_fill_price, timestamp, bot_id, signal_id FROM orders ORDER BY timestamp DESC LIMIT 50")
        orders = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        return {
            "balances": balances,
            "positions": positions,
            "orders": orders
        }

    def get_trade_history(self) -> List[dict]:
        if self._trade_history_cache is not None:
            return self._trade_history_cache

        conn = get_connection()
        cursor = conn.cursor()

        if not self._pnl_backfill_done:
            try:
                updated = backfill_missing_order_pnl(cursor)
                if updated:
                    conn.commit()
                self._pnl_backfill_done = True
            except Exception:
                conn.rollback()

        cursor.execute("""
            SELECT id, symbol, type, side, price, quantity, status,
                   filled_quantity, average_fill_price, timestamp, bot_id, signal_id,
                   realized_pnl, cost_basis
            FROM orders
            WHERE status = 'FILLED'
            ORDER BY timestamp ASC, id ASC
        """)
        all_orders = [dict(row) for row in cursor.fetchall()]
        conn.close()

        enriched = enrich_orders_with_pnl(all_orders)
        enriched.reverse()
        self._trade_history_cache = enriched
        return enriched

    def get_trades(self, limit: int = 100) -> List[dict]:
        history = self.get_trade_history()
        return history[:limit]

    def get_positions(self) -> List[dict]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT symbol, size, avg_price, stop_loss_percent, take_profit_percent FROM positions WHERE size != 0.0")
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def get_balances(self) -> Dict[str, dict]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT asset, balance, locked FROM accounts")
        balances = {row["asset"]: {"balance": row["balance"], "locked": row["locked"]} for row in cursor.fetchall()}
        conn.close()
        return balances

    async def place_order(self, order_req: dict) -> dict:
        symbol = order_req.get("symbol")
        order_type = order_req.get("type")
        side = order_req.get("side")
        price = order_req.get("price")
        quantity = order_req.get("quantity")
        stop_loss_percent = order_req.get("stop_loss_percent")
        take_profit_percent = order_req.get("take_profit_percent")
        take_profit_price = order_req.get("take_profit_price")
        bot_id = order_req.get("bot_id")
        signal_id = order_req.get("signal_id")

        if symbol not in self.feed._symbols:
            return {"status": "error", "message": f"Invalid symbol: {symbol}"}
            
        if quantity <= 0:
            return {"status": "error", "message": "Quantity must be greater than 0"}

        market_price = self.feed._symbols[symbol]["price"]
        order_price = price if order_type == "LIMIT" else market_price
        
        if order_type == "LIMIT" and (price is None or price <= 0):
            return {"status": "error", "message": "Limit price must be greater than 0"}

        order_value = order_price * quantity
        
        if order_value > MAX_ORDER_VALUE:
            return {"status": "error", "message": f"Order value exceeds maximum risk limit of ${MAX_ORDER_VALUE}"}
            
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            asset = self.feed._symbols[symbol]["asset"]
            quote = self.feed._symbols[symbol]["quote"]
            
            if side == "BUY":
                cursor.execute("SELECT balance, locked FROM accounts WHERE asset = ?", (quote,))
                row = cursor.fetchone()
                if not row or (row["balance"] - row["locked"]) < order_value:
                    conn.close()
                    return {"status": "error", "message": f"Insufficient {quote} balance. Needed: {order_value:.2f}"}
                    
                if order_type == "LIMIT":
                    cursor.execute("UPDATE accounts SET locked = locked + ? WHERE asset = ?", (order_value, quote))
                    
            elif side == "SELL":
                cursor.execute("SELECT size FROM positions WHERE symbol = ?", (symbol,))
                row = cursor.fetchone()
                position_size = row["size"] if row else 0.0
                
                cursor.execute("SELECT SUM(quantity) FROM orders WHERE symbol = ? AND side = 'SELL' AND status = 'PENDING'", (symbol,))
                locked_row = cursor.fetchone()
                locked_qty = locked_row[0] if locked_row[0] is not None else 0.0
                
                if (position_size - locked_qty) < quantity:
                    conn.close()
                    return {"status": "error", "message": f"Insufficient {symbol} position size to sell. Available: {position_size - locked_qty}"}

            order_id = str(uuid.uuid4())
            status = "PENDING" if order_type == "LIMIT" else "FILLED"
            
            cursor.execute("""
                INSERT INTO orders (id, symbol, type, side, price, quantity, status, filled_quantity, average_fill_price, stop_loss_percent, take_profit_percent, bot_id, signal_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_id, symbol, order_type, side, 
                price if order_type == "LIMIT" else None, 
                quantity, status,
                quantity if order_type == "MARKET" else 0.0,
                market_price if order_type == "MARKET" else 0.0,
                stop_loss_percent,
                take_profit_percent,
                bot_id,
                signal_id,
            ))
            
            if order_type == "MARKET":
                pending_bot_fill = self._process_fill(
                    cursor, symbol, side, market_price, quantity, quote,
                    stop_loss_percent, take_profit_percent,
                    take_profit_price=take_profit_price,
                    bot_id=bot_id,
                    order_id=order_id,
                )
            else:
                pending_bot_fill = None
                
            conn.commit()
            conn.close()

            if pending_bot_fill:
                self._apply_bot_fill(pending_bot_fill)

            if order_type == "MARKET":
                self.invalidate_trade_history_cache()
            
            return {
                "status": "success",
                "message": f"Order placed: {side} {quantity} {symbol} @ {order_price}",
                "order_id": order_id,
                "average_fill_price": market_price if order_type == "MARKET" else None,
                "filled_quantity": quantity if order_type == "MARKET" else 0.0,
            }
            
        except Exception as e:
            conn.rollback()
            conn.close()
            return {"status": "error", "message": f"Database transaction error: {str(e)}"}

    async def cancel_order(self, order_id: str) -> dict:
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
            quote = self.feed._symbols[symbol]["quote"]
            
            cursor.execute("UPDATE orders SET status = 'CANCELED' WHERE id = ?", (order_id,))
            
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
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT id, symbol, side, price, quantity, stop_loss_percent, take_profit_percent, bot_id "
            "FROM orders WHERE status = 'PENDING'"
        )
        pending_orders = cursor.fetchall()
        
        if not pending_orders:
            conn.close()
            return []
            
        filled_order_updates = []
        pending_bot_fills: list[tuple] = []
        
        for order in pending_orders:
            symbol = order["symbol"]
            side = order["side"]
            limit_price = order["price"]
            qty = order["quantity"]
            
            market_price = self.feed._symbols[symbol]["price"]
            
            should_fill = False
            if side == "BUY" and market_price <= limit_price:
                should_fill = True
            elif side == "SELL" and market_price >= limit_price:
                should_fill = True
                
            if should_fill:
                try:
                    quote = self.feed._symbols[symbol]["quote"]
                    
                    cursor.execute("""
                        UPDATE orders 
                        SET status = 'FILLED', filled_quantity = ?, average_fill_price = ?
                        WHERE id = ?
                    """, (qty, market_price, order["id"]))
                    
                    if side == "BUY":
                        locked_val = limit_price * qty
                        cursor.execute("UPDATE accounts SET locked = MAX(0.0, locked - ?) WHERE asset = ?", (locked_val, quote))
                        
                    bot_fill = self._process_fill(
                        cursor, symbol, side, market_price, qty, quote,
                        order["stop_loss_percent"], order["take_profit_percent"],
                        bot_id=order.get("bot_id"),
                        order_id=order["id"],
                    )
                    if bot_fill:
                        pending_bot_fills.append(bot_fill)
                    
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
        for bot_fill in pending_bot_fills:
            self._apply_bot_fill(bot_fill)
        if filled_order_updates:
            self.invalidate_trade_history_cache()
        return filled_order_updates

    def _apply_bot_fill(self, fill_tuple) -> None:
        if not fill_tuple:
            return
        if len(fill_tuple) == 5:
            bid, fsym, fside, fqty, fprice = fill_tuple
            risk = None
        else:
            bid, fsym, fside, fqty, fprice, risk = fill_tuple
        bot_positions.apply_fill(bid, fsym, fside, fqty, fprice, risk=risk, feed=self.feed)

    def _process_fill(
        self,
        cursor,
        symbol,
        side,
        price,
        quantity,
        quote,
        stop_loss_percent=None,
        take_profit_percent=None,
        *,
        take_profit_price=None,
        bot_id=None,
        order_id=None,
    ):
        """Apply account/position updates on cursor. Returns bot fill tuple after commit."""
        base_asset = self.feed._symbols[symbol]["asset"]
        order_value = price * quantity
        
        if side == "BUY":
            cursor.execute("UPDATE accounts SET balance = balance - ? WHERE asset = ?", (order_value, quote))
            cursor.execute("UPDATE accounts SET balance = balance + ? WHERE asset = ?", (quantity, base_asset))
        else:
            cursor.execute("UPDATE accounts SET balance = balance + ? WHERE asset = ?", (order_value, quote))
            cursor.execute("UPDATE accounts SET balance = MAX(0.0, balance - ?) WHERE asset = ?", (quantity, base_asset))

        cursor.execute("SELECT size, avg_price, stop_loss_percent, take_profit_percent FROM positions WHERE symbol = ?", (symbol,))
        pos_row = cursor.fetchone()
        
        if not pos_row:
            new_size = quantity if side == "BUY" else -quantity
            
            sl_price = None
            tp_price = None
            if stop_loss_percent is not None:
                sl_price = price * (1 - stop_loss_percent / 100) if new_size > 0 else price * (1 + stop_loss_percent / 100)
            if take_profit_price is not None:
                tp_price = float(take_profit_price)
                take_profit_percent = None
            elif take_profit_percent is not None:
                tp_price = price * (1 + take_profit_percent / 100) if new_size > 0 else price * (1 - take_profit_percent / 100)
                    
            cursor.execute("""
                INSERT INTO positions (symbol, size, avg_price, stop_loss_percent, take_profit_percent, stop_loss_price, take_profit_price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (symbol, new_size, price, stop_loss_percent, take_profit_percent, sl_price, tp_price))
        else:
            current_size = pos_row["size"]
            current_avg = pos_row["avg_price"]
            
            sl_pct = stop_loss_percent if stop_loss_percent is not None else pos_row["stop_loss_percent"]
            tp_pct = take_profit_percent if take_profit_percent is not None else pos_row["take_profit_percent"]
            explicit_tp = take_profit_price
            
            if side == "BUY":
                new_size = current_size + quantity
                if current_size >= 0:
                    new_avg = ((current_size * current_avg) + order_value) / new_size if new_size > 0 else 0.0
                else:
                    new_avg = current_avg if new_size < 0 else (price if new_size > 0 else 0.0)
            else: # SELL
                new_size = current_size - quantity
                if current_size <= 0:
                    new_avg = ((abs(current_size) * current_avg) + order_value) / abs(new_size) if new_size < 0 else 0.0
                else:
                    new_avg = current_avg if new_size > 0 else (price if new_size < 0 else 0.0)
            
            if abs(new_size) < 1e-8:
                new_size = 0.0
                new_avg = 0.0
                sl_pct = None
                tp_pct = None
                sl_price = None
                tp_price = None
            else:
                sl_price = None
                tp_price = None
                if sl_pct is not None:
                    sl_price = new_avg * (1 - sl_pct / 100) if new_size > 0 else new_avg * (1 + sl_pct / 100)
                if explicit_tp is not None:
                    tp_price = float(explicit_tp)
                    tp_pct = None
                elif tp_pct is not None:
                    tp_price = new_avg * (1 + tp_pct / 100) if new_size > 0 else new_avg * (1 - tp_pct / 100)
                
            cursor.execute("""
                UPDATE positions 
                SET size = ?, avg_price = ?, stop_loss_percent = ?, take_profit_percent = ?, stop_loss_price = ?, take_profit_price = ?
                WHERE symbol = ?
            """, (new_size, new_avg, sl_pct, tp_pct, sl_price, tp_price, symbol))

        if order_id:
            record_order_fifo_pnl(cursor, order_id, symbol, side, price, quantity)

        if bot_id:
            risk = None
            if (
                stop_loss_percent is not None
                or take_profit_percent is not None
                or take_profit_price is not None
            ):
                risk = {
                    "stop_loss_percent": stop_loss_percent,
                    "take_profit_percent": take_profit_percent,
                    "take_profit_price": take_profit_price,
                }
            return (bot_id, symbol, side, quantity, price, risk)
        return None

    async def update_position_sl_tp(self, symbol: str, stop_loss_percent: float=None, take_profit_percent: float=None, stop_loss_price: float=None, take_profit_price: float=None) -> dict:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT size, avg_price, stop_loss_percent, take_profit_percent, stop_loss_price, take_profit_price FROM positions WHERE symbol = ?", (symbol,))
        pos_row = cursor.fetchone()
        
        if not pos_row or pos_row["size"] == 0.0:
            conn.close()
            return {"status": "error", "message": f"No active position for {symbol} to update SL/TP"}
            
        try:
            size = pos_row["size"]
            avg_price = pos_row["avg_price"]
            
            # Use provided explicit price, OR calculate from percent, OR keep existing price
            sl_price = stop_loss_price
            tp_price = take_profit_price
            
            # If percent provided, calculate new price
            if stop_loss_percent is not None:
                if size > 0: sl_price = avg_price * (1 - stop_loss_percent / 100)
                elif size < 0: sl_price = avg_price * (1 + stop_loss_percent / 100)
            elif stop_loss_price is not None:
                stop_loss_percent = None # Hard stop loss disables trailing

            if take_profit_percent is not None:
                if size > 0: tp_price = avg_price * (1 + take_profit_percent / 100)
                elif size < 0: tp_price = avg_price * (1 - take_profit_percent / 100)
            elif take_profit_price is not None:
                take_profit_percent = None
                
            # If neither were provided in the request, keep existing DB values
            if stop_loss_price is None and stop_loss_percent is None:
                sl_price = pos_row["stop_loss_price"]
                stop_loss_percent = pos_row["stop_loss_percent"]
                
            if take_profit_price is None and take_profit_percent is None:
                tp_price = pos_row["take_profit_price"]
                take_profit_percent = pos_row["take_profit_percent"]
                
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
        owners_map = bot_positions.list_owners_grouped()

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, size, avg_price, stop_loss_percent, take_profit_percent,
                   stop_loss_price, take_profit_price
            FROM positions
            WHERE size != 0.0
        """)
        account_positions = {row["symbol"]: dict(row) for row in cursor.fetchall()}
        conn.close()

        symbols = set(account_positions.keys()) | set(owners_map.keys())
        if not symbols:
            return [], [], []

        trailing_bot_updates: list[tuple[str, str, float | None, float | None, float | None]] = []
        trailing_account_updates: list[tuple[str, float]] = []
        exit_plans: list[dict] = []
        triggered_logs: list[str] = []

        current_atrs = {}

        for symbol in symbols:
            if symbol not in self.feed._symbols:
                continue

            market_price = self.feed._symbols[symbol]["price"]
            quote = self.feed._symbols[symbol]["quote"]
            account = account_positions.get(symbol, {})
            acc_size = float(account.get("size") or 0.0)
            owners = owners_map.get(symbol, [])
            bot_total = sum(abs(float(o["size"])) for o in owners)

            for owner in owners:
                osize = float(owner["size"])
                if abs(osize) <= 1e-8:
                    continue

                bot_config = owner.get("bot_config") or {}
                use_chandelier = bool(bot_config.get("chandelier_stop_enabled", False))
                current_atr = None

                if use_chandelier:
                    timeframe = owner.get("timeframe") or "1m"
                    key = (symbol, timeframe)
                    if key not in current_atrs:
                        current_atr_val = 0.0
                        try:
                            from app.services.bots.candle_source import get_bot_candles
                            import pandas as pd
                            import pandas_ta as ta

                            candles = get_bot_candles(symbol, self.feed, timeframe=timeframe)
                            if candles and len(candles) >= 15:
                                df = pd.DataFrame(candles)
                                atr_len = bot_config.get("atr_length", 14)
                                atr_series = ta.atr(df["high"], df["low"], df["close"], length=atr_len)
                                if atr_series is not None and not atr_series.empty:
                                    import math
                                    val = atr_series.iloc[-1]
                                    if not math.isnan(val):
                                        current_atr_val = float(val)
                        except Exception as exc:
                            print(f"Failed to calculate current ATR in check_sl_tp_triggers: {exc}")
                        current_atrs[key] = current_atr_val
                    current_atr = current_atrs[key]

                trigger_type, trailing_sl, updated_high, updated_low = bot_positions.evaluate_risk_trigger(
                    osize,
                    float(owner["avg_price"]),
                    market_price,
                    stop_loss_percent=owner["stop_loss_percent"],
                    take_profit_percent=owner["take_profit_percent"],
                    stop_loss_price=owner["stop_loss_price"],
                    take_profit_price=owner["take_profit_price"],
                    chandelier_stop_enabled=use_chandelier,
                    chandelier_multiplier=float(bot_config.get("chandelier_multiplier") or 3.0),
                    high_watermark=owner.get("high_watermark"),
                    low_watermark=owner.get("low_watermark"),
                    entry_atr=owner.get("entry_atr"),
                    current_atr=current_atr,
                )

                if (
                    trailing_sl != owner.get("stop_loss_price")
                    or updated_high != owner.get("high_watermark")
                    or updated_low != owner.get("low_watermark")
                ):
                    trailing_bot_updates.append((owner["bot_id"], symbol, trailing_sl, updated_high, updated_low))

                if not trigger_type:
                    continue

                side = "SELL" if osize > 0 else "BUY"
                sl_ref = owner.get("stop_loss_price")
                tp_ref = owner.get("take_profit_price")
                if trigger_type == "SL":
                    triggered_logs.append(
                        f"🚨 BOT STOP LOSS ({owner['bot_id'][:8]}) for {symbol} at ${market_price:.2f} "
                        f"(SL limit: ${sl_ref:.2f}). Exiting slice..."
                    )
                else:
                    triggered_logs.append(
                        f"🎯 BOT TAKE PROFIT ({owner['bot_id'][:8]}) for {symbol} at ${market_price:.2f} "
                        f"(TP limit: ${tp_ref:.2f}). Exiting slice..."
                    )
                exit_plans.append({
                    "symbol": symbol,
                    "side": side,
                    "market_price": market_price,
                    "quote": quote,
                    "avg_price": float(owner["avg_price"]),
                    "trigger_type": trigger_type,
                    "qty": abs(osize),
                    "bot_id": owner["bot_id"],
                })

            remainder = max(0.0, abs(acc_size) - bot_total)
            if remainder <= 1e-8 or not account:
                continue

            sl_price = account.get("stop_loss_price")
            tp_price = account.get("take_profit_price")
            sl_pct = account.get("stop_loss_percent")
            tp_pct = account.get("take_profit_percent")
            if sl_price is None and tp_price is None and sl_pct is None and tp_pct is None:
                continue

            trigger_type = None
            if acc_size > 0:
                if sl_pct is not None:
                    potential_sl = market_price * (1 - sl_pct / 100)
                    if sl_price is None or potential_sl > sl_price:
                        sl_price = potential_sl
                        trailing_account_updates.append((symbol, sl_price))
                if sl_price is not None and market_price <= sl_price:
                    trigger_type = "SL"
                elif tp_price is not None and market_price >= tp_price:
                    trigger_type = "TP"
            elif acc_size < 0:
                if sl_pct is not None:
                    potential_sl = market_price * (1 + sl_pct / 100)
                    if sl_price is None or potential_sl < sl_price:
                        sl_price = potential_sl
                        trailing_account_updates.append((symbol, sl_price))
                if sl_price is not None and market_price >= sl_price:
                    trigger_type = "SL"
                elif tp_price is not None and market_price <= tp_price:
                    trigger_type = "TP"

            if not trigger_type:
                continue

            side = "SELL" if acc_size > 0 else "BUY"
            avg_price = float(account.get("avg_price") or 0)
            if trigger_type == "SL":
                triggered_logs.append(
                    f"🚨 STOP LOSS TRIGGERED for {symbol} at ${market_price:.2f} "
                    f"(Avg Entry: ${avg_price:.2f}, SL limit: ${sl_price:.2f}). Exiting manual slice..."
                )
            else:
                triggered_logs.append(
                    f"🎯 TAKE PROFIT TRIGGERED for {symbol} at ${market_price:.2f} "
                    f"(Avg Entry: ${avg_price:.2f}, TP limit: ${tp_price:.2f}). Exiting manual slice..."
                )
            exit_plans.append({
                "symbol": symbol,
                "side": side,
                "market_price": market_price,
                "quote": quote,
                "avg_price": avg_price,
                "trigger_type": trigger_type,
                "qty": remainder,
                "bot_id": None,
            })

        if trailing_bot_updates:
            conn = get_connection()
            cursor = conn.cursor()
            try:
                for bot_id, symbol, sl_price, high_wm, low_wm in trailing_bot_updates:
                    cursor.execute(
                        """
                        UPDATE bot_positions
                        SET stop_loss_price = ?, high_watermark = ?, low_watermark = ?
                        WHERE bot_id = ? AND symbol = ?
                        """,
                        (sl_price, high_wm, low_wm, bot_id, symbol),
                    )
                conn.commit()
            finally:
                conn.close()

        if trailing_account_updates:
            conn = get_connection()
            cursor = conn.cursor()
            try:
                for symbol, sl_price in trailing_account_updates:
                    cursor.execute(
                        "UPDATE positions SET stop_loss_price = ? WHERE symbol = ?",
                        (sl_price, symbol),
                    )
                conn.commit()
            finally:
                conn.close()

        if not exit_plans:
            return [], triggered_logs, []

        filled_exits: list[dict] = []
        bot_exits: list[dict] = []
        pending_bot_fills: list[tuple] = []

        conn = get_connection()
        cursor = conn.cursor()
        try:
            for plan in exit_plans:
                symbol = plan["symbol"]
                side = plan["side"]
                market_price = plan["market_price"]
                quote = plan["quote"]
                trigger_type = plan["trigger_type"]
                avg_price = plan["avg_price"]
                qty = plan["qty"]
                bot_id = plan.get("bot_id")

                if qty <= 1e-8:
                    continue

                order_id = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO orders (id, symbol, type, side, price, quantity, status, filled_quantity, average_fill_price, bot_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (order_id, symbol, "MARKET", side, None, qty, "FILLED", qty, market_price, bot_id))

                bot_fill = self._process_fill(
                    cursor, symbol, side, market_price, qty, quote,
                    bot_id=bot_id,
                    order_id=order_id,
                )
                if bot_fill:
                    pending_bot_fills.append(bot_fill)

                filled_exits.append({
                    "id": order_id,
                    "symbol": symbol,
                    "side": side,
                    "price": market_price,
                    "quantity": qty,
                })

                if bot_id:
                    bot_exits.append({
                        "bot_id": bot_id,
                        "order_id": order_id,
                        "symbol": symbol,
                        "side": side,
                        "quantity": qty,
                        "price": market_price,
                        "entry_price": avg_price,
                        "trigger_type": trigger_type,
                        "signal_id": f"{bot_id}:sltp:{order_id}",
                    })

            if filled_exits:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        for bot_fill in pending_bot_fills:
            self._apply_bot_fill(bot_fill)

        if filled_exits:
            self.invalidate_trade_history_cache()

        return filled_exits, triggered_logs, bot_exits

    async def emergency_stop(self) -> dict:
        """Cancel all pending orders and close all open positions."""
        conn = get_connection()
        cursor = conn.cursor()
        
        # 1. Cancel all pending orders
        cursor.execute("SELECT id FROM orders WHERE status = 'PENDING'")
        pending_ids = [r[0] for r in cursor.fetchall()]
        conn.close()
        
        cancelled_count = 0
        for oid in pending_ids:
            res = await self.cancel_order(oid)
            if res.get("status") == "success":
                cancelled_count += 1
                
        # 2. Market close all active positions
        positions = self.get_positions()
        closed_count = 0
        for pos in positions:
            symbol = pos["symbol"]
            size = pos["size"]
            if abs(size) > 1e-8:
                side = "SELL" if size > 0 else "BUY"
                qty = abs(size)
                res = await self.place_order({
                    "symbol": symbol,
                    "type": "MARKET",
                    "side": side,
                    "quantity": qty
                })
                if res.get("status") == "success":
                    closed_count += 1
                    
        return {
            "status": "success",
            "message": f"Emergency liquidation complete. Cancelled {cancelled_count} orders, closed {closed_count} positions."
        }

