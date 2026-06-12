import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Dict, List
import requests
from app.config import BINANCE_API_KEY, BINANCE_SECRET_KEY, BINANCE_BASE_URL, SYMBOLS
from app.database import get_connection
from app.services.base_oms import BaseOMSService
from app.services.sim_oms import SimulatedOMSService

class BinanceOMSService(BaseOMSService):
    def __init__(self, feed):
        self.feed = feed
        self.use_fallback = not (BINANCE_API_KEY and BINANCE_SECRET_KEY)
        self.fallback_oms = None
        self.broadcast_callback = None
        self.active = False
        
        if self.use_fallback:
            logging.warning("Binance API credentials missing. OMS will operate in local simulation mode for crypto.")
            self.fallback_oms = SimulatedOMSService(feed)

    def register_broadcast_callback(self, callback) -> None:
        self.broadcast_callback = callback

    async def initialize(self) -> None:
        if self.use_fallback:
            await self.fallback_oms.initialize()
            return
        self.active = True
        # Spawn background loop to monitor SL/TP triggers for active crypto positions locally
        asyncio.create_task(self._sl_tp_monitor_loop())
        logging.info("Binance OMS initialized.")

    async def stop(self) -> None:
        self.active = False

    def _sign(self, query_str: str) -> str:
        return hmac.new(
            BINANCE_SECRET_KEY.encode('utf-8'),
            query_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    def _private_request(self, method: str, endpoint: str, params: dict = None) -> dict:
        if params is None:
            params = {}
            
        params["timestamp"] = int(time.time() * 1000)
        query_str = "&".join([f"{k}={v}" for k, v in params.items()])
        signature = self._sign(query_str)
        signed_query = f"{query_str}&signature={signature}"
        
        headers = {
            "X-MBX-APIKEY": BINANCE_API_KEY
        }
        
        url = f"{BINANCE_BASE_URL}{endpoint}?{signed_query}"
        
        if method.upper() == "GET":
            resp = requests.get(url, headers=headers, timeout=5)
        elif method.upper() == "POST":
            resp = requests.post(url, headers=headers, timeout=5)
        elif method.upper() == "DELETE":
            resp = requests.delete(url, headers=headers, timeout=5)
        else:
            raise ValueError("Unsupported HTTP method")
            
        return resp.json()

    def get_account_data(self) -> dict:
        if self.use_fallback:
            return self.fallback_oms.get_account_data()
            
        try:
            balances = self.get_balances()
            positions = {p["symbol"]: p for p in self.get_positions()}
            
            # Fetch open orders
            raw_orders = self._private_request("GET", "/api/v3/openOrders")
            orders = []
            for o in raw_orders:
                orders.append({
                    "id": str(o.get("orderId")),
                    "symbol": o.get("symbol"),
                    "type": o.get("type", "").upper(),
                    "side": o.get("side", "").upper(),
                    "price": float(o.get("price")) if o.get("price") else None,
                    "quantity": float(o.get("origQty")),
                    "status": "PENDING",
                    "filled_quantity": float(o.get("executedQty", 0.0)),
                    "average_fill_price": float(o.get("price", 0.0)),
                    "timestamp": int(o.get("time", time.time()*1000) // 1000)
                })
            return {
                "balances": balances,
                "positions": positions,
                "orders": orders
            }
        except Exception as e:
            logging.error(f"Error getting Binance account data: {str(e)}")
            return {"balances": {}, "positions": {}, "orders": []}

    def get_trade_history(self) -> List[dict]:
        if self.use_fallback:
            return self.fallback_oms.get_trade_history()
            
        try:
            # Aggregate filled trades across all configured symbols
            all_trades = []
            for symbol in self.feed.symbols:
                params = {"symbol": symbol, "limit": 50}
                raw_trades = self._private_request("GET", "/api/v3/myTrades", params)
                if isinstance(raw_trades, list):
                    for t in raw_trades:
                        qty = float(t.get("qty"))
                        price = float(t.get("price"))
                        all_trades.append({
                            "id": str(t.get("id")),
                            "symbol": symbol,
                            "type": "MARKET" if t.get("isBuyer") else "LIMIT",
                            "side": "BUY" if t.get("isBuyer") else "SELL",
                            "price": price,
                            "quantity": qty,
                            "status": "FILLED",
                            "filled_quantity": qty,
                            "average_fill_price": price,
                            "timestamp": int(t.get("time", time.time()*1000) // 1000),
                            "trade_value": round(qty * price, 2),
                            "realized_pnl": None,
                            "cost_basis": None
                        })
            # Sort chronologically newest first
            all_trades.sort(key=lambda x: x["timestamp"], reverse=True)
            return all_trades
        except Exception as e:
            logging.error(f"Error getting Binance trades: {str(e)}")
            return []

    def get_trades(self, limit: int = 100) -> List[dict]:
        return self.get_trade_history()[:limit]

    def get_positions(self) -> List[dict]:
        if self.use_fallback:
            return self.fallback_oms.get_positions()
            
        # For Binance Spot, there are no native "positions", only asset balances.
        # We calculate virtual position sizes based on BTC/ETH balances relative to USDT.
        try:
            balances = self.get_balances()
            positions = []
            for name in self.feed.symbols:
                info = self.feed._symbols[name]
                asset = info["asset"]
                bal = balances.get(asset, {}).get("balance", 0.0)
                if bal > 1e-4:
                    positions.append({
                        "symbol": name,
                        "size": bal,
                        "avg_price": info["price"], # fallback to current price as avg
                        "stop_loss_percent": None,
                        "take_profit_percent": None,
                        "stop_loss_price": None,
                        "take_profit_price": None
                    })
            return positions
        except Exception as e:
            logging.error(f"Error getting Binance positions: {str(e)}")
            return []

    def get_balances(self) -> Dict[str, dict]:
        if self.use_fallback:
            return self.fallback_oms.get_balances()
            
        try:
            res = self._private_request("GET", "/api/v3/account")
            raw_balances = res.get("balances", [])
            balances = {}
            for b in raw_balances:
                asset = b.get("asset")
                free = float(b.get("free", 0.0))
                locked = float(b.get("locked", 0.0))
                if free > 0.0 or locked > 0.0:
                    balances[asset] = {
                        "balance": free + locked,
                        "locked": locked
                    }
            # Add USD mappings to USDT for compatibility
            if "USDT" in balances:
                balances["USD"] = balances["USDT"]
            return balances
        except Exception as e:
            logging.error(f"Error getting Binance balances: {str(e)}")
            return {"USDT": {"balance": 0.0, "locked": 0.0}}

    async def place_order(self, order_req: dict) -> dict:
        if self.use_fallback:
            return await self.fallback_oms.place_order(order_req)
            
        symbol = order_req.get("symbol")
        order_type = order_req.get("type").upper()
        side = order_req.get("side").upper()
        price = order_req.get("price")
        quantity = order_req.get("quantity")
        
        params = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": str(quantity)
        }
        
        if order_type == "LIMIT":
            params["price"] = str(price)
            params["timeInForce"] = "GTC"
            
        try:
            res = await asyncio.to_thread(
                self._private_request,
                "POST",
                "/api/v3/order",
                params
            )
            
            if "code" in res:
                return {"status": "error", "message": f"Binance error: {res.get('msg')}"}
                
            # If SL/TP percentages exist, store them in our local tracker database for monitoring
            sl_pct = order_req.get("stop_loss_percent")
            tp_pct = order_req.get("take_profit_percent")
            if sl_pct or tp_pct:
                await self.update_position_sl_tp(symbol, sl_pct, tp_pct)
                
            return {
                "status": "success",
                "message": f"Binance order filled: {side} {quantity} {symbol}",
                "order_id": str(res.get("orderId"))
            }
        except Exception as e:
            return {"status": "error", "message": f"Failed to place Binance order: {str(e)}"}

    async def cancel_order(self, order_id: str) -> dict:
        if self.use_fallback:
            return await self.fallback_oms.cancel_order(order_id)
            
        # We need a symbol for cancel_order in Binance API. Search active open orders to find the symbol.
        try:
            open_orders = self._private_request("GET", "/api/v3/openOrders")
            symbol = None
            for o in open_orders:
                if str(o.get("orderId")) == order_id:
                    symbol = o.get("symbol")
                    break
                    
            if not symbol:
                return {"status": "error", "message": "Order not found in open orders."}
                
            res = await asyncio.to_thread(
                self._private_request,
                "DELETE",
                "/api/v3/order",
                {"symbol": symbol, "orderId": order_id}
            )
            if "code" in res:
                return {"status": "error", "message": res.get("msg")}
            return {"status": "success", "message": f"Binance order {order_id} canceled."}
        except Exception as e:
            return {"status": "error", "message": f"Failed to cancel Binance order: {str(e)}"}

    async def update_position_sl_tp(self, symbol: str, sl_pct: float, tp_pct: float) -> dict:
        # Save SL/TP boundaries in local tracking table for local monitoring loops
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT size FROM positions WHERE symbol = ?", (symbol,))
            row = cursor.fetchone()
            # If position does not exist in SQLite, initialize it
            # Fetch current balance to use as position size
            size = row["size"] if row else 0.0
            if size == 0.0:
                balances = self.get_balances()
                asset = self.feed._symbols.get(symbol, {}).get("asset", "BTC")
                size = balances.get(asset, {}).get("balance", 0.0)
                
            avg_price = self.feed._symbols[symbol]["price"]
            sl_price = avg_price * (1 - sl_pct / 100.0) if sl_pct else None
            tp_price = avg_price * (1 + tp_pct / 100.0) if tp_pct else None
            
            if not row:
                cursor.execute("""
                    INSERT INTO positions (symbol, size, avg_price, stop_loss_percent, take_profit_percent, stop_loss_price, take_profit_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (symbol, size, avg_price, sl_pct, tp_pct, sl_price, tp_price))
            else:
                cursor.execute("""
                    UPDATE positions
                    SET size = ?, stop_loss_percent = ?, take_profit_percent = ?, stop_loss_price = ?, take_profit_price = ?
                    WHERE symbol = ?
                """, (size, sl_pct, tp_pct, sl_price, tp_price, symbol))
            conn.commit()
            conn.close()
            return {"status": "success", "message": f"Registered local SL/TP monitoring for {symbol}."}
        except Exception as e:
            conn.close()
            return {"status": "error", "message": f"Failed to save SL/TP: {str(e)}"}

    async def _sl_tp_monitor_loop(self):
        """Checks local positions database and executes market exit to Binance if breached."""
        while self.active:
            try:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT symbol, size, stop_loss_price, take_profit_price FROM positions WHERE size > 0.0")
                rows = cursor.fetchall()
                conn.close()
                
                for r in rows:
                    symbol = r["symbol"]
                    size = r["size"]
                    sl_price = r["stop_loss_price"]
                    tp_price = r["take_profit_price"]
                    market_price = self.feed._symbols[symbol]["price"]
                    
                    breached = False
                    trigger_type = None
                    if sl_price and market_price <= sl_price:
                        breached = True
                        trigger_type = "SL"
                    elif tp_price and market_price >= tp_price:
                        breached = True
                        trigger_type = "TP"
                        
                    if breached:
                        logging.warning(f"LOCAL BINANCE MONITOR: {trigger_type} BREACHED for {symbol} at {market_price}. Exiting position...")
                        # Submit MARKET SELL to Binance
                        order_res = await self.place_order({
                            "symbol": symbol,
                            "type": "MARKET",
                            "side": "SELL",
                            "quantity": size
                        })
                        
                        log_msg = f"🚨 {trigger_type} EXIT EXECUTED on Binance for {symbol}: {order_res.get('message')}"
                        
                        # Wipe local tracking position
                        conn = get_connection()
                        cursor = conn.cursor()
                        cursor.execute("UPDATE positions SET size = 0.0, stop_loss_price = NULL, take_profit_price = NULL WHERE symbol = ?", (symbol,))
                        conn.commit()
                        conn.close()
                        
                        if self.broadcast_callback:
                            await self.broadcast_callback({
                                "type": "bot_log",
                                "data": {"bot_id": "system", "level": "INFO", "message": log_msg}
                            })
                            await self.broadcast_callback({"type": "account_update", "data": self.get_account_data()})
                            
                await asyncio.sleep(1.0)
            except Exception as e:
                logging.error(f"Error in Binance SL/TP monitor loop: {str(e)}")
                await asyncio.sleep(5)

    async def emergency_stop(self) -> dict:
        """Cancel all Binance open orders and liquidate all virtual positions."""
        if self.use_fallback:
            return await self.fallback_oms.emergency_stop()
            
        try:
            # 1. Fetch and cancel all open orders
            open_orders = self._private_request("GET", "/api/v3/openOrders")
            cancelled_count = 0
            for o in open_orders:
                symbol = o.get("symbol")
                order_id = str(o.get("orderId"))
                res = self._private_request("DELETE", "/api/v3/order", {"symbol": symbol, "orderId": order_id})
                if "code" not in res:
                    cancelled_count += 1
                    
            # 2. Fetch balances and close virtual positions (liquidate BTC and ETH to USDT)
            positions = self.get_positions()
            closed_count = 0
            for pos in positions:
                symbol = pos["symbol"]
                size = pos["size"]
                if size > 1e-4:
                    # Submit MARKET SELL to liquidate
                    res = await self.place_order({
                        "symbol": symbol,
                        "type": "MARKET",
                        "side": "SELL",
                        "quantity": size
                    })
                    if res.get("status") == "success":
                        closed_count += 1
                        
            # Clear local tracking positions table
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE positions SET size = 0.0, stop_loss_price = NULL, take_profit_price = NULL")
            conn.commit()
            conn.close()
            
            return {
                "status": "success",
                "message": f"Emergency stop executed on Binance. Cancelled {cancelled_count} orders, closed {closed_count} positions."
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to execute Binance emergency stop: {str(e)}"
            }

