"""Interactive Brokers order management (paper or live via Gateway port).

Uses a dedicated API client id (``IB_OMS_CLIENT_ID``) separate from the feed connection.
When ``IB_OMS_ENABLED=false`` or Gateway is unreachable, falls back to ``SimulatedOMSService``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.config import (
    IB_HOST,
    IB_OMS_CLIENT_ID,
    IB_OMS_ENABLED,
    IB_PORT,
    IB_READ_ONLY_API,
    MAX_ORDER_VALUE,
)
from app.services.base_oms import BaseOMSService
from app.services.ib_contracts import stock_contract
from app.services.sim_oms import SimulatedOMSService

logger = logging.getLogger(__name__)


class IbOMSService(BaseOMSService):
    def __init__(self, feed) -> None:
        self.feed = feed
        self.fallback_oms = SimulatedOMSService(feed)
        self.broadcast_callback: Optional[Callable[[dict], Awaitable[None]]] = None
        self._ib = None
        self._connected = False
        self._account_id: Optional[str] = None
        self._active = False
        self._poll_task: Optional[asyncio.Task] = None

    @property
    def use_fallback(self) -> bool:
        return not IB_OMS_ENABLED or IB_READ_ONLY_API

    def register_broadcast_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        self.broadcast_callback = callback
        if hasattr(self.fallback_oms, "register_broadcast_callback"):
            self.fallback_oms.register_broadcast_callback(callback)

    async def initialize(self) -> None:
        if self.use_fallback:
            if IB_READ_ONLY_API and IB_OMS_ENABLED:
                logger.warning("IB OMS: read-only API enabled - using simulated OMS for orders.")
            elif not IB_OMS_ENABLED:
                logger.info("IB OMS disabled (IB_OMS_ENABLED=false) - using simulated OMS.")
            await self.fallback_oms.initialize()
            return
        self._active = True
        try:
            await self._connect()
            self._poll_task = asyncio.create_task(self._account_poll_loop())
            logger.info("IB OMS initialized (clientId=%s, port=%s).", IB_OMS_CLIENT_ID, IB_PORT)
        except Exception as exc:
            logger.warning("IB OMS connect failed (%s) - falling back to simulated OMS.", exc)
            self._active = False
            await self.fallback_oms.initialize()

    async def stop(self) -> None:
        self._active = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._ib is not None:
            try:
                self._ib.disconnect()
            except Exception:
                pass
        self._ib = None
        self._connected = False

    async def _connect(self) -> None:
        from ib_async import IB

        ib = IB()
        await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_OMS_CLIENT_ID, timeout=20)
        self._ib = ib
        self._connected = True
        accounts = ib.managedAccounts()
        self._account_id = accounts[0] if accounts else None

    async def _account_poll_loop(self) -> None:
        while self._active and self._connected:
            try:
                if self.broadcast_callback:
                    from app.api.outbound import publish_account_update

                    await publish_account_update(self.broadcast_callback, self.get_account_data())
            except Exception as exc:
                logger.debug("IB OMS account poll: %s", exc)
            await asyncio.sleep(12)

    def _require_live(self) -> Optional[dict]:
        if self.use_fallback or not self._connected or self._ib is None:
            return None
        return {}

    async def place_order(self, order_req: dict) -> dict:
        if self.use_fallback or not self._connected:
            return await self.fallback_oms.place_order(order_req)

        symbol = str(order_req.get("symbol", "")).upper()
        order_type = str(order_req.get("type", "MARKET")).upper()
        side = str(order_req.get("side", "")).upper()
        quantity = float(order_req.get("quantity") or 0)
        price = order_req.get("price")

        if symbol not in self.feed._symbols:
            return {"status": "error", "message": f"Invalid symbol: {symbol}"}
        if quantity <= 0:
            return {"status": "error", "message": "Quantity must be greater than 0"}

        market_price = float(self.feed._symbols[symbol]["price"])
        est_price = float(price) if order_type == "LIMIT" and price else market_price
        if est_price * quantity > MAX_ORDER_VALUE:
            return {
                "status": "error",
                "message": f"Order value exceeds maximum risk limit of ${MAX_ORDER_VALUE}",
            }

        try:
            from ib_async import LimitOrder, MarketOrder

            contract = stock_contract(symbol)
            qualified = await self._ib.qualifyContractsAsync(contract)
            if not qualified:
                return {"status": "error", "message": f"Could not qualify IB contract for {symbol}"}
            resolved = qualified[0]

            action = "BUY" if side == "BUY" else "SELL"
            if order_type == "LIMIT":
                if price is None or float(price) <= 0:
                    return {"status": "error", "message": "Limit price must be greater than 0"}
                order = LimitOrder(action, quantity, float(price))
            else:
                order = MarketOrder(action, quantity)

            trade = self._ib.placeOrder(resolved, order)
            order_id = str(getattr(trade.order, "orderId", None) or uuid.uuid4())

            deadline = time.time() + 8.0
            while time.time() < deadline:
                await asyncio.sleep(0.25)
                status = (trade.orderStatus.status or "").upper()
                if status == "FILLED":
                    avg = float(trade.orderStatus.avgFillPrice or est_price)
                    filled = float(trade.orderStatus.filled or quantity)
                    return {
                        "status": "success",
                        "order_id": order_id,
                        "average_fill_price": avg,
                        "filled_quantity": filled,
                        "message": f"IB {side} {symbol} filled @ {avg}",
                    }
                if status in ("CANCELLED", "INACTIVE", "APICANCELLED"):
                    return {
                        "status": "error",
                        "message": f"IB rejected order ({status})",
                    }

            return {
                "status": "ambiguous",
                "order_id": order_id,
                "message": "IB order submitted but fill status unknown - reconcile before retrying.",
            }
        except Exception as exc:
            logger.error("IB place_order failed: %s", exc)
            return {"status": "error", "message": f"IB order failed: {exc}"}

    async def cancel_order(self, order_id: str) -> dict:
        if self.use_fallback or not self._connected:
            return await self.fallback_oms.cancel_order(order_id)
        try:
            for trade in self._ib.openTrades():
                oid = str(getattr(trade.order, "orderId", ""))
                if oid == str(order_id):
                    self._ib.cancelOrder(trade.order)
                    return {"status": "success", "message": f"Cancel requested for {order_id}"}
            return {"status": "error", "message": f"Order {order_id} not found in IB open trades"}
        except Exception as exc:
            return {"status": "error", "message": f"IB cancel failed: {exc}"}

    def get_positions(self) -> List[dict]:
        if self.use_fallback or not self._connected:
            return self.fallback_oms.get_positions()
        try:
            out = []
            for pos in self._ib.positions(self._account_id):
                qty = float(pos.position)
                if abs(qty) < 1e-8:
                    continue
                sym = getattr(pos.contract, "symbol", "")
                out.append({
                    "symbol": sym,
                    "size": qty,
                    "avg_price": float(pos.avgCost or 0),
                    "stop_loss_percent": None,
                    "take_profit_percent": None,
                    "stop_loss_price": None,
                    "take_profit_price": None,
                })
            return out
        except Exception as exc:
            logger.error("IB get_positions: %s", exc)
            return []

    def get_balances(self) -> Dict[str, dict]:
        if self.use_fallback or not self._connected:
            return self.fallback_oms.get_balances()
        try:
            cash = 0.0
            for av in self._ib.accountValues(self._account_id):
                if av.tag == "TotalCashValue" and av.currency == "USD":
                    cash = float(av.value)
                    break
            return {"USD": {"balance": cash, "locked": 0.0}}
        except Exception as exc:
            logger.error("IB get_balances: %s", exc)
            return {"USD": {"balance": 0.0, "locked": 0.0}}

    def get_trades(self, limit: int = 100) -> List[dict]:
        return self.get_trade_history()[:limit]

    def get_trade_history(self) -> List[dict]:
        if self.use_fallback or not self._connected:
            return self.fallback_oms.get_trade_history()
        try:
            trades = []
            for fill in self._ib.fills():
                sym = getattr(fill.contract, "symbol", "")
                qty = float(fill.execution.shares)
                price = float(fill.execution.price)
                side = "BUY" if fill.execution.side.upper().startswith("B") else "SELL"
                ts = fill.execution.time
                epoch = int(ts.timestamp()) if hasattr(ts, "timestamp") else int(time.time())
                trades.append({
                    "id": str(fill.execution.execId),
                    "symbol": sym,
                    "type": "MARKET",
                    "side": side,
                    "price": price,
                    "quantity": qty,
                    "status": "FILLED",
                    "filled_quantity": qty,
                    "average_fill_price": price,
                    "timestamp": epoch,
                    "trade_value": round(qty * price, 2),
                    "realized_pnl": None,
                    "cost_basis": None,
                })
            trades.sort(key=lambda t: t["timestamp"], reverse=True)
            return trades
        except Exception as exc:
            logger.error("IB get_trade_history: %s", exc)
            return []

    def get_account_data(self) -> dict:
        if self.use_fallback or not self._connected:
            return self.fallback_oms.get_account_data()
        positions = {p["symbol"]: p for p in self.get_positions()}
        orders = []
        try:
            for trade in self._ib.openTrades():
                o = trade.order
                st = trade.orderStatus
                orders.append({
                    "id": str(o.orderId),
                    "symbol": getattr(trade.contract, "symbol", ""),
                    "type": o.orderType.upper() if o.orderType else "MARKET",
                    "side": o.action.upper(),
                    "price": float(o.lmtPrice) if o.lmtPrice else None,
                    "quantity": float(o.totalQuantity),
                    "status": (st.status or "PENDING").upper(),
                    "filled_quantity": float(st.filled or 0),
                    "average_fill_price": float(st.avgFillPrice or 0),
                    "timestamp": int(time.time()),
                })
        except Exception:
            pass
        return {
            "balances": self.get_balances(),
            "positions": positions,
            "orders": orders[:50],
        }

    async def update_position_sl_tp(
        self,
        symbol: str,
        stop_loss_percent: float = None,
        take_profit_percent: float = None,
        stop_loss_price: float = None,
        take_profit_price: float = None,
    ) -> dict:
        if self.use_fallback or not self._connected:
            return await self.fallback_oms.update_position_sl_tp(
                symbol, stop_loss_percent, take_profit_percent, stop_loss_price, take_profit_price
            )
        return {
            "status": "error",
            "message": "IB bracket SL/TP not implemented - manage stops in TWS or use simulated mode.",
        }

    async def emergency_stop(self) -> dict:
        if self.use_fallback or not self._connected:
            return await self.fallback_oms.emergency_stop()
        closed = 0
        errors: list[str] = []
        try:
            from ib_async import MarketOrder

            for pos in list(self._ib.positions(self._account_id)):
                qty = float(pos.position)
                if abs(qty) < 1e-8:
                    continue
                sym = getattr(pos.contract, "symbol", "")
                action = "SELL" if qty > 0 else "BUY"
                order = MarketOrder(action, abs(qty))
                self._ib.placeOrder(pos.contract, order)
                closed += 1
                await asyncio.sleep(0.5)
            return {"status": "success", "message": f"IB emergency close submitted for {closed} position(s)"}
        except Exception as exc:
            errors.append(str(exc))
            return {"status": "error", "message": f"IB emergency stop failed: {'; '.join(errors)}"}
