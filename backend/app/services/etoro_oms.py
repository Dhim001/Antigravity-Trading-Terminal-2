"""Order Management System backed by the eToro Public API (v2 execution).

Reads account state from `/trading/info/{env}/pnl` (clientPortfolio) and routes
orders through `POST /api/v2/trading/execution/{env}/orders`. Trade execution
follows the plugin's at-most-once discipline: only 429 is safely retried;
timeouts and 5xx are surfaced as ambiguous without resending the payload.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import requests

from app.config import (
    ETORO_ACCESS_TOKEN,
    ETORO_API_BASE,
    ETORO_API_KEY,
    ETORO_ENV,
    ETORO_EXEC_MIN_INTERVAL,
    ETORO_USER_KEY,
    MAX_ORDER_VALUE,
)
from app.services.base_oms import BaseOMSService
from app.api.outbound import publish_account_update
from app.services.sim_oms import SimulatedOMSService

logger = logging.getLogger(__name__)

# PnL endpoint is cached ~10s on eToro's side after executions.
PNL_CACHE_WAIT = 10.0


class EtoroOMSService(BaseOMSService):
    def __init__(self, feed):
        self.feed = feed
        self.use_fallback = not (ETORO_ACCESS_TOKEN or (ETORO_API_KEY and ETORO_USER_KEY))
        self.fallback_oms: Optional[SimulatedOMSService] = None
        self.broadcast_callback: Optional[Callable[[dict], Awaitable[None]]] = None
        self._session = requests.Session()
        self._env: Optional[str] = None  # "demo" | "real"
        self._portfolio_cache: Optional[dict] = None
        self._last_exec_at = 0.0
        self._exec_lock = asyncio.Lock()
        self._poll_task: Optional[asyncio.Task] = None
        self._active = False

        if self.use_fallback:
            logging.warning(
                "eToro OMS: credentials missing — falling back to paper (simulated) OMS."
            )
            self.fallback_oms = SimulatedOMSService(feed)

    def register_broadcast_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        self.broadcast_callback = callback

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def initialize(self) -> None:
        if self.use_fallback:
            await self.fallback_oms.initialize()
            return
        self._env = await asyncio.to_thread(self._resolve_env)
        self._active = True
        self._poll_task = asyncio.create_task(self._account_poll_loop())
        logger.info("eToro OMS initialized (env=%s).", self._env)

    async def stop(self) -> None:
        self._active = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------ #
    # HTTP / auth (etoro-api-conventions)
    # ------------------------------------------------------------------ #
    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-request-id": str(uuid.uuid4()),
        }
        if ETORO_ACCESS_TOKEN:
            headers["Authorization"] = f"Bearer {ETORO_ACCESS_TOKEN}"
        elif ETORO_API_KEY and ETORO_USER_KEY:
            headers["x-api-key"] = ETORO_API_KEY
            headers["x-user-key"] = ETORO_USER_KEY
        return headers

    def _resolve_env(self) -> str:
        if ETORO_ENV in ("demo", "real"):
            return ETORO_ENV
        try:
            resp = self._session.get(
                f"{ETORO_API_BASE}/trading/info/real/pnl",
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return "real"
            if resp.status_code == 403:
                return "demo"
        except Exception as e:
            logger.warning("eToro env probe failed (%s); defaulting to demo.", e)
        return "demo"

    def _pnl_path(self) -> str:
        env = self._env or "demo"
        return f"{ETORO_API_BASE}/trading/info/{env}/pnl"

    def _exec_orders_path(self) -> str:
        env = self._env or "demo"
        if env == "demo":
            return f"{ETORO_API_BASE}/api/v2/trading/execution/demo/orders"
        return f"{ETORO_API_BASE}/api/v2/trading/execution/orders"

    def _exec_cancel_path(self, order_id: str) -> str:
        env = self._env or "demo"
        if env == "demo":
            return f"{ETORO_API_BASE}/api/v2/trading/execution/demo/orders/{order_id}"
        return f"{ETORO_API_BASE}/api/v2/trading/execution/orders/{order_id}"

    def _trade_history_path(self) -> str:
        env = self._env or "demo"
        if env == "demo":
            return f"{ETORO_API_BASE}/trading/info/trade/demo/history"
        return f"{ETORO_API_BASE}/trading/info/trade/history"

    def _fetch_portfolio(self) -> dict:
        resp = self._session.get(self._pnl_path(), headers=self._headers(), timeout=10)
        if resp.status_code == 401:
            raise _AuthDead("eToro session expired or credentials invalid.")
        resp.raise_for_status()
        data = resp.json()
        portfolio = data.get("clientPortfolio") or data
        self._portfolio_cache = portfolio
        return portfolio

    async def _throttle_execution(self) -> None:
        async with self._exec_lock:
            elapsed = time.time() - self._last_exec_at
            if elapsed < ETORO_EXEC_MIN_INTERVAL:
                await asyncio.sleep(ETORO_EXEC_MIN_INTERVAL - elapsed)
            self._last_exec_at = time.time()

    async def _post_order(self, payload: dict) -> dict:
        """POST a trade with at-most-once semantics (retry only on 429)."""
        await self._throttle_execution()
        headers = self._headers()

        def _do_post():
            return self._session.post(
                self._exec_orders_path(), headers=headers, json=payload, timeout=15
            )

        try:
            resp = await asyncio.to_thread(_do_post)
        except requests.RequestException as e:
            return {
                "status": "ambiguous",
                "message": (
                    f"Order outcome unknown (network error: {e}). "
                    "Do not resend — check your eToro portfolio before retrying."
                ),
            }

        if resp.status_code == 200:
            body = resp.json()
            return {
                "status": "success",
                "message": "eToro order submitted.",
                "order_id": str(body.get("orderId", "")),
                "reference_id": body.get("referenceId"),
            }
        if resp.status_code == 429:
            await asyncio.sleep(15)
            try:
                resp2 = await asyncio.to_thread(_do_post)
            except requests.RequestException as e:
                return {
                    "status": "ambiguous",
                    "message": f"Rate-limited retry failed ambiguously: {e}",
                }
            if resp2.status_code == 200:
                body = resp2.json()
                return {
                    "status": "success",
                    "message": "eToro order submitted (after rate-limit retry).",
                    "order_id": str(body.get("orderId", "")),
                }
            return {
                "status": "error",
                "message": f"eToro rate-limited order: {resp2.text[:200]}",
            }
        if resp.status_code == 401:
            return {"status": "error", "message": "eToro credentials invalid — reconnect required."}
        if 400 <= resp.status_code < 500:
            detail = resp.text[:300]
            return {"status": "error", "message": f"eToro rejected order ({resp.status_code}): {detail}"}
        return {
            "status": "ambiguous",
            "message": (
                f"Order outcome unknown (HTTP {resp.status_code}). "
                "Do not resend — verify via portfolio before retrying."
            ),
        }

    # ------------------------------------------------------------------ #
    # Symbol / instrument helpers
    # ------------------------------------------------------------------ #
    def _symbol_for_instrument(self, instrument_id: int) -> Optional[str]:
        if hasattr(self.feed, "_id_to_symbol"):
            return self.feed._id_to_symbol.get(instrument_id)
        if hasattr(self.feed, "_instrument_ids"):
            for sym, iid in self.feed._instrument_ids.items():
                if iid == instrument_id:
                    return sym
        return None

    def _instrument_id_for_symbol(self, symbol: str) -> Optional[int]:
        if hasattr(self.feed, "_instrument_ids") and symbol in self.feed._instrument_ids:
            return self.feed._instrument_ids[symbol]
        info = self.feed._symbols.get(symbol, {})
        etoro_symbol = info.get("asset", symbol)
        try:
            resp = self._session.get(
                f"{ETORO_API_BASE}/market-data/search",
                headers=self._headers(),
                params={"internalSymbolFull": etoro_symbol},
                timeout=10,
            )
            if resp.status_code != 200:
                return None
            items = resp.json().get("items", [])
            exact = next((it for it in items if it.get("symbolFull") == etoro_symbol), None)
            chosen = exact or (items[0] if items else None)
            return int(chosen["instrumentId"]) if chosen else None
        except Exception:
            return None

    def _settlement_type(self, symbol: str) -> str:
        return "cfd" if "USDT" in symbol else "real"

    def _find_position(self, symbol: str, portfolio: dict) -> Optional[dict]:
        iid = self._instrument_id_for_symbol(symbol)
        for pos in portfolio.get("positions", []):
            if iid and pos.get("instrumentID") == iid:
                return pos
        return None

    # ------------------------------------------------------------------ #
    # Account snapshot (etoro-account-snapshot formulas)
    # ------------------------------------------------------------------ #
    def _available_cash(self, portfolio: dict) -> float:
        credit = float(portfolio.get("credit", 0.0))
        locked = 0.0
        for o in portfolio.get("ordersForOpen", []):
            if o.get("mirrorID", 0) == 0:
                locked += float(o.get("amount", 0.0))
        for o in portfolio.get("orders", []):
            locked += float(o.get("amount", 0.0))
        return credit - locked

    def _map_positions(self, portfolio: dict) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for pos in portfolio.get("positions", []):
            sym = self._symbol_for_instrument(pos.get("instrumentID"))
            if not sym:
                continue
            units = float(pos.get("units", 0.0))
            is_buy = pos.get("isBuy", True)
            size = units if is_buy else -units
            leverage = int(pos.get("leverage", 1) or 1)
            avg_price = float(pos.get("openRate", 0.0))
            if leverage == 1 and units:
                avg_price = float(pos.get("amount", 0.0)) / units
            out[sym] = {
                "size": round(size, 6),
                "avg_price": round(avg_price, 4),
                "position_id": pos.get("positionID"),
                "stop_loss_percent": None,
                "take_profit_percent": None,
                "stop_loss_price": pos.get("stopLossRate"),
                "take_profit_price": pos.get("takeProfitRate"),
            }
        return out

    def _map_orders(self, portfolio: dict) -> List[dict]:
        orders: List[dict] = []
        for source_key, default_type in (("ordersForOpen", "MARKET"), ("orders", "LIMIT")):
            for o in portfolio.get(source_key, []):
                sym = self._symbol_for_instrument(o.get("instrumentID"))
                if not sym:
                    continue
                orders.append({
                    "id": str(o.get("orderID", "")),
                    "symbol": sym,
                    "type": default_type,
                    "side": "BUY" if o.get("isBuy", True) else "SELL",
                    "price": None,
                    "quantity": float(o.get("amountInUnits") or o.get("amount", 0.0)),
                    "status": "PENDING",
                    "filled_quantity": 0.0,
                    "average_fill_price": 0.0,
                    "timestamp": _parse_ts(o.get("openDateTime")),
                })
        return orders[:50]

    def get_account_data(self) -> dict:
        if self.use_fallback:
            return self.fallback_oms.get_account_data()
        try:
            portfolio = self._fetch_portfolio()
            cash = self._available_cash(portfolio)
            return {
                "balances": {"USD": {"balance": round(cash, 2), "locked": 0.0}},
                "positions": self._map_positions(portfolio),
                "orders": self._map_orders(portfolio),
            }
        except _AuthDead:
            logger.error("eToro auth dead while fetching account data.")
            return {"balances": {}, "positions": {}, "orders": []}
        except Exception as e:
            logger.error("eToro account fetch error: %s", e)
            return {"balances": {}, "positions": {}, "orders": []}

    def get_positions(self) -> List[dict]:
        if self.use_fallback:
            return self.fallback_oms.get_positions()
        try:
            portfolio = self._fetch_portfolio()
            return [
                {k: v for k, v in p.items() if k != "position_id"}
                for p in self._map_positions(portfolio).values()
            ]
        except Exception:
            return []

    def get_balances(self) -> Dict[str, dict]:
        if self.use_fallback:
            return self.fallback_oms.get_balances()
        try:
            cash = self._available_cash(self._fetch_portfolio())
            return {"USD": {"balance": round(cash, 2), "locked": 0.0}}
        except Exception:
            return {"USD": {"balance": 0.0, "locked": 0.0}}

    def get_trade_history(self) -> List[dict]:
        if self.use_fallback:
            return self.fallback_oms.get_trade_history()
        try:
            min_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
            resp = self._session.get(
                self._trade_history_path(),
                headers=self._headers(),
                params={"minDate": min_date, "pageSize": 100},
                timeout=10,
            )
            if resp.status_code != 200:
                return []
            items = resp.json().get("items", [])
            trades = []
            for t in items:
                sym = self._symbol_for_instrument(t.get("instrumentId"))
                if not sym:
                    continue
                qty = float(t.get("units", 0.0))
                open_rate = float(t.get("openRate", 0.0))
                close_rate = float(t.get("closeRate", 0.0))
                trades.append({
                    "id": str(t.get("orderId", t.get("positionId", ""))),
                    "symbol": sym,
                    "type": "MARKET",
                    "side": "BUY" if t.get("isBuy", True) else "SELL",
                    "price": None,
                    "quantity": qty,
                    "status": "FILLED",
                    "filled_quantity": qty,
                    "average_fill_price": close_rate or open_rate,
                    "timestamp": _parse_ts(t.get("closeTimestamp") or t.get("openTimestamp")),
                    "trade_value": round(qty * (close_rate or open_rate), 2),
                    "realized_pnl": round(float(t.get("netProfit", 0.0)), 4),
                    "cost_basis": round(open_rate, 4),
                })
            trades.sort(key=lambda x: x["timestamp"], reverse=True)
            return trades
        except Exception as e:
            logger.error("eToro trade history error: %s", e)
            return []

    def get_trades(self, limit: int = 100) -> List[dict]:
        return self.get_trade_history()[:limit]

    # ------------------------------------------------------------------ #
    # Order placement
    # ------------------------------------------------------------------ #
    async def place_order(self, order_req: dict) -> dict:
        if self.use_fallback:
            return await self.fallback_oms.place_order(order_req)

        symbol = order_req.get("symbol")
        order_type = (order_req.get("type") or "MARKET").upper()
        side = (order_req.get("side") or "BUY").upper()
        price = order_req.get("price")
        quantity = float(order_req.get("quantity") or 0)
        sl_pct = order_req.get("stop_loss_percent")
        tp_pct = order_req.get("take_profit_percent")

        if symbol not in self.feed._symbols:
            return {"status": "error", "message": f"Invalid symbol: {symbol}"}
        if quantity <= 0:
            return {"status": "error", "message": "Quantity must be greater than 0"}

        mid = self.feed._symbols[symbol]["price"]
        est_value = (price if order_type == "LIMIT" and price else mid) * quantity
        if est_value > MAX_ORDER_VALUE:
            return {
                "status": "error",
                "message": f"Order value exceeds maximum risk limit of ${MAX_ORDER_VALUE}",
            }

        instrument_id = self._instrument_id_for_symbol(symbol)
        if not instrument_id:
            return {"status": "error", "message": f"Could not resolve eToro instrument for {symbol}"}

        asset = self.feed._symbols[symbol].get("asset", symbol)

        # --- Close long (SELL) via v2 close action ---
        if side == "SELL":
            portfolio = self._fetch_portfolio()
            pos = self._find_position(symbol, portfolio)
            if not pos:
                return {"status": "error", "message": f"No open eToro position for {symbol}"}
            payload = {
                "action": "close",
                "transaction": "sell",
                "positionIds": [int(pos["positionID"])],
            }
            result = await self._post_order(payload)
            if result.get("status") == "ambiguous":
                from app.config import TERMINAL_MODE
                from app.services.reconciliation import record_ambiguous_order

                record_ambiguous_order(
                    {**order_req, "symbol": symbol, "side": side, "type": order_type, "quantity": quantity},
                    result.get("message", "Ambiguous eToro order"),
                    broker=TERMINAL_MODE,
                    bot_id=order_req.get("bot_id"),
                )
            if result.get("status") == "success":
                result["message"] = f"eToro close submitted: SELL {symbol}"
            return result

        if side != "BUY":
            return {"status": "error", "message": f"Unsupported side: {side}"}

        # --- Open long (BUY) ---
        if order_type == "LIMIT":
            if not price or price <= 0:
                return {"status": "error", "message": "Limit price must be greater than 0"}
            payload: Dict[str, Any] = {
                "action": "open",
                "transaction": "buy",
                "instrumentId": instrument_id,
                "symbol": asset,
                "settlementType": self._settlement_type(symbol),
                "orderType": "mit",
                "triggerRate": float(price),
                "leverage": 1,
                "units": quantity,
            }
        else:
            payload = {
                "action": "open",
                "transaction": "buy",
                "instrumentId": instrument_id,
                "symbol": asset,
                "settlementType": self._settlement_type(symbol),
                "orderType": "mkt",
                "leverage": 1,
                "units": quantity,
            }

        if sl_pct is not None:
            payload["stopLossRate"] = mid * (1 - sl_pct / 100.0)
            payload["stopLossType"] = "fixed"
        if tp_pct is not None:
            payload["takeProfitRate"] = mid * (1 + tp_pct / 100.0)

        result = await self._post_order(payload)
        if result.get("status") == "ambiguous":
            from app.config import TERMINAL_MODE
            from app.services.reconciliation import record_ambiguous_order

            record_ambiguous_order(
                {**order_req, "symbol": symbol, "side": side, "type": order_type, "quantity": quantity},
                result.get("message", "Ambiguous eToro order"),
                broker=TERMINAL_MODE,
                bot_id=order_req.get("bot_id"),
            )
        if result.get("status") == "success":
            result["message"] = (
                f"eToro order submitted: {side} {quantity} {symbol} "
                f"({'limit @ ' + str(price) if order_type == 'LIMIT' else 'market'})"
            )
        return result

    async def cancel_order(self, order_id: str) -> dict:
        if self.use_fallback:
            return await self.fallback_oms.cancel_order(order_id)
        try:
            await self._throttle_execution()
            resp = await asyncio.to_thread(
                self._session.delete,
                self._exec_cancel_path(order_id),
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code in (200, 204):
                return {"status": "success", "message": f"eToro order {order_id} cancel requested."}
            return {"status": "error", "message": f"eToro cancel failed ({resp.status_code}): {resp.text[:200]}"}
        except Exception as e:
            return {"status": "error", "message": f"Cancel request failed: {e}"}

    async def update_position_sl_tp(
        self,
        symbol: str,
        stop_loss_percent: float = None,
        take_profit_percent: float = None,
        stop_loss_price: float = None,
        take_profit_price: float = None,
    ) -> dict:
        if self.use_fallback:
            return await self.fallback_oms.update_position_sl_tp(
                symbol, stop_loss_percent, take_profit_percent, stop_loss_price, take_profit_price
            )
        return {
            "status": "success",
            "message": (
                f"SL/TP for {symbol} should be set at order placement on eToro. "
                "Close and re-open with new SL/TP, or manage in the eToro app."
            ),
        }

    async def emergency_stop(self) -> dict:
        if self.use_fallback:
            return await self.fallback_oms.emergency_stop()
        cancelled = 0
        closed = 0
        try:
            portfolio = self._fetch_portfolio()
            for o in self._map_orders(portfolio):
                res = await self.cancel_order(o["id"])
                if res.get("status") == "success":
                    cancelled += 1
            for sym, pos in self._map_positions(portfolio).items():
                pid = pos.get("position_id")
                if pid:
                    res = await self._post_order({
                        "action": "close",
                        "transaction": "sell",
                        "positionIds": [int(pid)],
                    })
                    if res.get("status") == "success":
                        closed += 1
            return {
                "status": "success",
                "message": (
                    f"eToro emergency stop: {cancelled} cancels requested, "
                    f"{closed} close orders submitted."
                ),
            }
        except Exception as e:
            return {"status": "error", "message": f"Emergency stop failed: {e}"}

    # ------------------------------------------------------------------ #
    # Background account sync
    # ------------------------------------------------------------------ #
    async def _account_poll_loop(self) -> None:
        while self._active:
            try:
                if self.broadcast_callback:
                    await publish_account_update(self.broadcast_callback, self.get_account_data())
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("eToro account poll error: %s", e)
            await asyncio.sleep(max(PNL_CACHE_WAIT, 5.0))


class _AuthDead(Exception):
    pass


def _parse_ts(value: Any) -> int:
    if not value:
        return int(time.time())
    try:
        if isinstance(value, (int, float)):
            return int(value)
        s = str(value).replace("Z", "+00:00")
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:
        return int(time.time())
