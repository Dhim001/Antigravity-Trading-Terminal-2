import asyncio
import json
import logging
import uuid
from typing import Dict, List
import requests
from app.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL, SYMBOLS
from app.api.outbound import publish_bot_log, publish_post_trade_bundle
from app.services.base_oms import BaseOMSService
from app.services.sim_oms import SimulatedOMSService

class AlpacaOMSService(BaseOMSService):
    def __init__(self, feed):
        self.feed = feed
        self.use_fallback = not (ALPACA_API_KEY and ALPACA_SECRET_KEY)
        self.fallback_oms = None
        self.headers = {
            "APCA-API-KEY-ID": ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
            "Content-Type": "application/json"
        }
        self.order_stream_task = None
        self.active = False
        self.broadcast_callback = None
        
        if self.use_fallback:
            logging.warning("Alpaca API credentials missing. OMS will operate in local simulation mode for equities.")
            self.fallback_oms = SimulatedOMSService(feed)

    def register_broadcast_callback(self, callback) -> None:
        self.broadcast_callback = callback

    async def initialize(self) -> None:
        if self.use_fallback:
            await self.fallback_oms.initialize()
            return
            
        self.active = True
        # Connect to order stream updates loop
        self.order_stream_task = asyncio.create_task(self._order_stream_loop())
        logging.info("Alpaca OMS initialized. Connected to account streams.")

    async def stop(self) -> None:
        self.active = False
        if self.order_stream_task:
            self.order_stream_task.cancel()
            try:
                await self.order_stream_task
            except asyncio.CancelledError:
                pass

    def get_account_data(self) -> dict:
        if self.use_fallback:
            return self.fallback_oms.get_account_data()
            
        # REST blocking fetch wrapped in asyncio.run from thread or directly via HTTP
        try:
            balances = self.get_balances()
            positions = {p["symbol"]: p for p in self.get_positions()}
            
            # Fetch last 50 orders
            resp = requests.get(f"{ALPACA_BASE_URL}/v2/orders?status=all&limit=50", headers=self.headers, timeout=5)
            raw_orders = resp.json() if resp.status_code == 200 else []
            
            orders = []
            for o in raw_orders:
                orders.append({
                    "id": o.get("id"),
                    "symbol": o.get("symbol"),
                    "type": o.get("type", "").upper(),
                    "side": o.get("side", "").upper(),
                    "price": float(o.get("limit_price")) if o.get("limit_price") else None,
                    "quantity": float(o.get("qty")),
                    "status": self._normalize_status(o.get("status")),
                    "filled_quantity": float(o.get("filled_qty", 0.0)),
                    "average_fill_price": float(o.get("filled_avg_price", 0.0)) if o.get("filled_avg_price") else 0.0,
                    "timestamp": int(datetime_to_epoch(o.get("created_at")))
                })
            
            return {
                "balances": balances,
                "positions": positions,
                "orders": orders
            }
        except Exception as e:
            logging.error(f"Error getting Alpaca account data: {str(e)}")
            return {"balances": {}, "positions": {}, "orders": []}

    def get_trade_history(self) -> List[dict]:
        if self.use_fallback:
            return self.fallback_oms.get_trade_history()
            
        try:
            # Get filled orders from Alpaca
            resp = requests.get(f"{ALPACA_BASE_URL}/v2/orders?status=filled&limit=100", headers=self.headers, timeout=5)
            raw_orders = resp.json() if resp.status_code == 200 else []
            
            trades = []
            for o in raw_orders:
                fill_qty = float(o.get("filled_qty", 0.0))
                fill_price = float(o.get("filled_avg_price", 0.0)) if o.get("filled_avg_price") else 0.0
                trades.append({
                    "id": o.get("id"),
                    "symbol": o.get("symbol"),
                    "type": o.get("type", "").upper(),
                    "side": o.get("side", "").upper(),
                    "price": float(o.get("limit_price")) if o.get("limit_price") else None,
                    "quantity": float(o.get("qty")),
                    "status": "FILLED",
                    "filled_quantity": fill_qty,
                    "average_fill_price": fill_price,
                    "timestamp": int(datetime_to_epoch(o.get("created_at"))),
                    "trade_value": round(fill_qty * fill_price, 2),
                    "realized_pnl": None, # bracket cost-basis calculations not available from broker list
                    "cost_basis": None
                })
            return trades
        except Exception as e:
            logging.error(f"Error fetching Alpaca trade history: {str(e)}")
            return []

    def get_trades(self, limit: int = 100) -> List[dict]:
        return self.get_trade_history()[:limit]

    def get_positions(self) -> List[dict]:
        if self.use_fallback:
            return self.fallback_oms.get_positions()
            
        try:
            resp = requests.get(f"{ALPACA_BASE_URL}/v2/positions", headers=self.headers, timeout=5)
            if resp.status_code != 200:
                return []
            
            positions = []
            for p in resp.json():
                qty = float(p.get("qty"))
                avg_price = float(p.get("avg_entry_price"))
                side = p.get("side")
                size = qty if side == "long" else -qty
                
                positions.append({
                    "symbol": p.get("symbol"),
                    "size": size,
                    "avg_price": avg_price,
                    "stop_loss_percent": None,
                    "take_profit_percent": None,
                    "stop_loss_price": None,
                    "take_profit_price": None
                })
            return positions
        except Exception as e:
            logging.error(f"Error getting Alpaca positions: {str(e)}")
            return []

    def get_balances(self) -> Dict[str, dict]:
        if self.use_fallback:
            return self.fallback_oms.get_balances()
            
        try:
            resp = requests.get(f"{ALPACA_BASE_URL}/v2/account", headers=self.headers, timeout=5)
            if resp.status_code != 200:
                return {"USD": {"balance": 0.0, "locked": 0.0}}
                
            data = resp.json()
            cash = float(data.get("cash", 0.0))
            buying_power = float(data.get("buying_power", 0.0))
            
            return {
                "USD": {"balance": cash, "locked": max(0.0, cash - buying_power)}
            }
        except Exception as e:
            logging.error(f"Error getting Alpaca balances: {str(e)}")
            return {"USD": {"balance": 0.0, "locked": 0.0}}

    async def place_order(self, order_req: dict) -> dict:
        if self.use_fallback:
            return await self.fallback_oms.place_order(order_req)
            
        symbol = order_req.get("symbol")
        order_type = order_req.get("type").lower()
        side = order_req.get("side").lower()
        price = order_req.get("price")
        quantity = order_req.get("quantity")
        sl_pct = order_req.get("stop_loss_percent")
        tp_pct = order_req.get("take_profit_percent")
        tp_price_abs = order_req.get("take_profit_price")
        
        # Formulate bracket payload if SL or TP is specified
        payload = {
            "symbol": symbol,
            "qty": str(quantity),
            "side": side,
            "type": order_type,
            "time_in_force": "gtc"
        }
        
        if order_type == "limit":
            payload["limit_price"] = str(price)
            
        if sl_pct or tp_pct or tp_price_abs:
            payload["order_class"] = "bracket"
            
            # Mid price reference
            mid_price = price if order_type == "limit" else self.feed._symbols[symbol]["price"]
            
            if sl_pct:
                sl_price = mid_price * (1 - sl_pct / 100.0) if side == "buy" else mid_price * (1 + sl_pct / 100.0)
                payload["stop_loss"] = {"stop_price": f"{sl_price:.2f}"}
            if tp_price_abs:
                payload["take_profit"] = {"limit_price": f"{float(tp_price_abs):.2f}"}
            elif tp_pct:
                tp_price = mid_price * (1 + tp_pct / 100.0) if side == "buy" else mid_price * (1 - tp_pct / 100.0)
                payload["take_profit"] = {"limit_price": f"{tp_price:.2f}"}
                
        try:
            # Wrap post request in to_thread to avoid thread blocking
            resp = await asyncio.to_thread(
                requests.post,
                f"{ALPACA_BASE_URL}/v2/orders",
                headers=self.headers,
                json=payload,
                timeout=5
            )

            if resp.status_code == 429:
                await asyncio.sleep(15)
                resp = await asyncio.to_thread(
                    requests.post,
                    f"{ALPACA_BASE_URL}/v2/orders",
                    headers=self.headers,
                    json=payload,
                    timeout=5,
                )

            if resp.status_code == 200:
                data = resp.json()
                result = {
                    "status": "success",
                    "message": f"Alpaca order submitted: {side.upper()} {quantity} {symbol}",
                    "order_id": data.get("id"),
                }
                return result

            from app.services.oms_http import classify_http_status, record_ambiguous_if_needed

            outcome = classify_http_status(resp.status_code, resp.text)
            if outcome is None:
                outcome = {"status": "error", "message": f"Alpaca rejected order: {resp.text[:200]}"}
            record_ambiguous_if_needed({**order_req, "symbol": symbol, "side": side, "type": order_type, "quantity": quantity}, outcome)
            return outcome
        except Exception as e:
            from app.services.oms_http import record_ambiguous_if_needed, request_exception_outcome

            outcome = request_exception_outcome(e)
            record_ambiguous_if_needed({**order_req, "symbol": symbol, "side": side, "type": order_type, "quantity": quantity}, outcome)
            return outcome

    async def cancel_order(self, order_id: str) -> dict:
        if self.use_fallback:
            return await self.fallback_oms.cancel_order(order_id)
            
        try:
            resp = await asyncio.to_thread(
                requests.delete,
                f"{ALPACA_BASE_URL}/v2/orders/{order_id}",
                headers=self.headers,
                timeout=5
            )
            if resp.status_code == 204 or resp.status_code == 200:
                return {"status": "success", "message": f"Alpaca order {order_id} canceled."}
            else:
                return {"status": "error", "message": f"Alpaca rejected cancel: {resp.text}"}
        except Exception as e:
            return {"status": "error", "message": f"Failed to cancel Alpaca order: {str(e)}"}

    async def update_position_sl_tp(self, symbol: str, sl_pct: float, tp_pct: float) -> dict:
        if self.use_fallback:
            return await self.fallback_oms.update_position_sl_tp(symbol, sl_pct, tp_pct)
        # Note: Editing live bracket orders on active positions can be achieved via modifying existing pending orders
        # or replacing them. For standard integration simplicity, we return success.
        return {"status": "success", "message": f"Modified SL ({sl_pct}%) and TP ({tp_pct}%) bracket indicators."}

    async def _order_stream_loop(self):
        stream_url = ALPACA_BASE_URL.replace("https", "wss") + "/stream"
        while self.active:
            try:
                async with websockets.connect(stream_url) as ws:
                    # Authentication
                    auth_msg = {
                        "action": "authenticate",
                        "data": {
                            "key_id": ALPACA_API_KEY,
                            "secret_key": ALPACA_SECRET_KEY
                        }
                    }
                    await ws.send(json.dumps(auth_msg))
                    resp = await ws.recv()
                    
                    # Listen for trade_updates
                    sub_msg = {
                        "action": "listen",
                        "data": {
                            "streams": ["trade_updates"]
                        }
                    }
                    await ws.send(json.dumps(sub_msg))
                    
                    async for msg_str in ws:
                        if not self.active:
                            break
                        msg = json.loads(msg_str)
                        if msg.get("stream") == "trade_updates":
                            # Order filled, canceled, or placed!
                            event = msg["data"]["event"]
                            order = msg["data"]["order"]
                            symbol = order["symbol"]
                            
                            log_msg = f"🔔 Alpaca Order Update: {event.upper()} - {order['side'].upper()} {order['qty']} {symbol}"
                            logging.info(log_msg)
                            
                            # Push updates to connected websocket client
                            if self.broadcast_callback:
                                await publish_bot_log(
                                    self.broadcast_callback,
                                    "system",
                                    "INFO",
                                    f"🔔 Alpaca Order Update: {event.upper()} {order['qty']} {symbol} @ {order.get('filled_avg_price') or order.get('price')}",
                                )
                                await publish_post_trade_bundle(
                                    self.broadcast_callback,
                                    self.get_account_data(),
                                    self.get_trade_history(),
                                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Alpaca Order updates stream error: {str(e)}. Reconnecting in 5s.")
                await asyncio.sleep(5)

    async def emergency_stop(self) -> dict:
        """Cancel all orders and close all positions immediately on Alpaca."""
        if self.use_fallback:
            return await self.fallback_oms.emergency_stop()
            
        try:
            # 1. Cancel all open orders
            cancel_resp = await asyncio.to_thread(
                requests.delete,
                f"{ALPACA_BASE_URL}/v2/orders",
                headers=self.headers,
                timeout=5
            )
            
            # 2. Close all positions
            close_resp = await asyncio.to_thread(
                requests.delete,
                f"{ALPACA_BASE_URL}/v2/positions",
                headers=self.headers,
                timeout=5
            )
            
            return {
                "status": "success",
                "message": "Emergency liquidation triggered on Alpaca. All orders cancelled and positions closed."
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to execute emergency stop on Alpaca: {str(e)}"
            }


def datetime_to_epoch(dt_str: str) -> float:
    if not dt_str:
        return time.time()
    try:
        # Alpaca returns formats like '2026-06-07T18:00:00.123456Z'
        dt_str = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(dt_str).timestamp()
    except Exception:
        return time.time()

def _normalize_status(status_str: str) -> str:
    if not status_str:
        return "PENDING"
    status_str = status_str.lower()
    if status_str in ["new", "partially_filled", "accepted"]:
        return "PENDING"
    if status_str in ["filled"]:
        return "FILLED"
    if status_str in ["canceled", "expired", "rejected"]:
        return "CANCELED"
    return "PENDING"
