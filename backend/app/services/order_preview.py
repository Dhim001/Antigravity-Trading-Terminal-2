"""Pre-trade order preview — same validation math as OMS without placing."""

from __future__ import annotations

from app.config import MAX_ORDER_VALUE


def preview_order(oms, order_req: dict) -> dict:
    """
    Return a structured preview: allowed, quantity, notional, SL/TP, block_reason.
    Uses OMS feed + account snapshot when available (SIM path).
    """
    symbol = (order_req.get("symbol") or "").upper()
    order_type = (order_req.get("type") or "MARKET").upper()
    side = (order_req.get("side") or "").upper()
    price = order_req.get("price")
    quantity = float(order_req.get("quantity") or 0)

    stop_loss_price = order_req.get("stop_loss_price")
    take_profit_price = order_req.get("take_profit_price")
    stop_loss_percent = order_req.get("stop_loss_percent")
    take_profit_percent = order_req.get("take_profit_percent")

    if not symbol:
        return _blocked("Symbol is required")
    if side not in ("BUY", "SELL"):
        return _blocked("Side must be BUY or SELL")
    if quantity <= 0:
        return _blocked("Quantity must be greater than 0")

    feed = getattr(oms, "feed", None)
    market_price = None
    if feed and hasattr(feed, "_symbols") and symbol in feed._symbols:
        market_price = float(feed._symbols[symbol].get("price") or 0)
    elif hasattr(oms, "get_account_data"):
        account = oms.get_account_data() or {}
        tickers = account.get("tickers") or {}
        if symbol in tickers:
            market_price = float(tickers[symbol].get("price") or 0)

    if order_type == "LIMIT":
        order_price = float(price) if price is not None else 0.0
        if order_price <= 0:
            return _blocked("Limit price must be greater than 0")
    else:
        order_price = market_price or 0.0
        if order_price <= 0:
            return _blocked("Market price unavailable for preview")

    notional = order_price * quantity
    is_crypto = "USDT" in symbol
    base = symbol.replace("USDT", "") if is_crypto else symbol
    quote = "USDT" if is_crypto else "USD"

    ref_price = order_price
    if stop_loss_price is not None and stop_loss_percent is None and ref_price > 0:
        stop_loss_percent = round(abs(ref_price - float(stop_loss_price)) / ref_price * 100, 2)
    if take_profit_price is not None and take_profit_percent is None and ref_price > 0:
        take_profit_percent = round(abs(ref_price - float(take_profit_price)) / ref_price * 100, 2)

    sl_price = float(stop_loss_price) if stop_loss_price is not None else None
    tp_price = float(take_profit_price) if take_profit_price is not None else None
    if sl_price is None and stop_loss_percent is not None and ref_price > 0:
        pct = float(stop_loss_percent)
        sl_price = ref_price * (1 - pct / 100) if side == "BUY" else ref_price * (1 + pct / 100)
    if tp_price is None and take_profit_percent is not None and ref_price > 0:
        pct = float(take_profit_percent)
        tp_price = ref_price * (1 + pct / 100) if side == "BUY" else ref_price * (1 - pct / 100)

    risk_per_share = abs(ref_price - sl_price) if sl_price is not None else None
    rr_ratio = None
    if risk_per_share and risk_per_share > 0 and tp_price is not None:
        reward = abs(tp_price - ref_price)
        rr_ratio = round(reward / risk_per_share, 2)

    warnings: list[str] = []
    block_reason = None

    if notional > MAX_ORDER_VALUE:
        block_reason = f"Order value exceeds maximum risk limit of ${MAX_ORDER_VALUE:,.0f}"

    quote_available = None
    base_position = None

    if hasattr(oms, "get_account_data"):
        account = oms.get_account_data() or {}
        balances = account.get("balances") or {}
        positions = account.get("positions") or {}
        quote_row = balances.get(quote) or {}
        quote_available = float(quote_row.get("balance") or 0) - float(quote_row.get("locked") or 0)
        pos = positions.get(symbol) or {}
        base_position = float(pos.get("size") or 0)

        if side == "BUY" and quote_available is not None and notional > quote_available:
            block_reason = block_reason or f"Insufficient {quote}. Available: {quote_available:.2f}"
        if side == "SELL" and base_position is not None and quantity > base_position:
            block_reason = block_reason or f"Insufficient {base}. Owned: {base_position}"

    if sl_price is None and tp_price is None:
        warnings.append("No stop-loss or take-profit set")

    allowed = block_reason is None
    return {
        "status": "preview",
        "allowed": allowed,
        "block_reason": block_reason,
        "warnings": warnings,
        "symbol": symbol,
        "side": side,
        "type": order_type,
        "quantity": quantity,
        "price": order_price if order_type == "LIMIT" else None,
        "market_price": market_price,
        "notional": round(notional, 2),
        "quote": quote,
        "base": base,
        "quote_available": quote_available,
        "base_position": base_position,
        "stop_loss_price": round(sl_price, 8) if sl_price is not None else None,
        "take_profit_price": round(tp_price, 8) if tp_price is not None else None,
        "risk_reward_ratio": rr_ratio,
        "max_order_value": MAX_ORDER_VALUE,
    }


def _blocked(reason: str) -> dict:
    return {
        "status": "preview",
        "allowed": False,
        "block_reason": reason,
        "warnings": [],
    }
