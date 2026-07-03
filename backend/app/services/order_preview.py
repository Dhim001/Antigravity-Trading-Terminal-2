"""Pre-trade order preview — same validation math as OMS without placing."""

from __future__ import annotations

from app.config import (
    MAX_ORDER_VALUE,
    ORDER_PREVIEW_FEE_BPS,
    ORDER_PREVIEW_SLIPPAGE_BPS,
    RISK_MARGIN_ENABLED,
)
from app.services.bots.backtest_costs import entry_fill_price, trade_fee
from app.services.bots.margin_risk import build_margin_snapshot, entry_margin_required, validate_margin_entry
from app.services.bots.portfolio_risk import build_portfolio_snapshot


def preview_order(oms, order_req: dict) -> dict:
    """
    Return a structured preview: allowed, quantity, notional, SL/TP, costs, margin, block_reason.
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

    trailing_stop_percent = order_req.get("trailing_stop_percent")

    fee_bps = float(order_req.get("fee_bps") if order_req.get("fee_bps") is not None else ORDER_PREVIEW_FEE_BPS)
    slippage_bps = float(
        order_req.get("slippage_bps")
        if order_req.get("slippage_bps") is not None
        else ORDER_PREVIEW_SLIPPAGE_BPS
    )

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
    if trailing_stop_percent is not None and ref_price > 0:
        trail = float(trailing_stop_percent)
        sl_price = ref_price * (1 - trail / 100) if side == "BUY" else ref_price * (1 + trail / 100)
        stop_loss_percent = trail
    if tp_price is None and take_profit_percent is not None and ref_price > 0:
        pct = float(take_profit_percent)
        tp_price = ref_price * (1 + pct / 100) if side == "BUY" else ref_price * (1 - pct / 100)

    risk_per_share = abs(ref_price - sl_price) if sl_price is not None else None
    rr_ratio = None
    if risk_per_share and risk_per_share > 0 and tp_price is not None:
        reward = abs(tp_price - ref_price)
        rr_ratio = round(reward / risk_per_share, 2)

    est_fill = entry_fill_price(order_price, side, slippage_bps)
    fill_notional = est_fill * quantity
    estimated_fee = trade_fee(fill_notional, fee_bps)
    estimated_slippage = abs(est_fill - order_price) * quantity

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

        if side == "BUY":
            buy_cost = fill_notional + estimated_fee
            if quote_available is not None and buy_cost > quote_available:
                block_reason = block_reason or (
                    f"Insufficient {quote}. Available: {quote_available:.2f} "
                    f"(incl. est. fee ${estimated_fee:.2f})"
                )
            if base_position < 0 and quantity > abs(base_position):
                block_reason = block_reason or (
                    f"Insufficient short to cover. Short: {abs(base_position):.4f} {base}"
                )
        elif side == "SELL":
            if base_position > 0 and quantity > base_position:
                block_reason = block_reason or f"Insufficient {base}. Owned: {base_position}"

    if sl_price is None and tp_price is None and side == "BUY":
        warnings.append("No stop-loss or take-profit set")

    margin_impact = _preview_margin_impact(oms, side, est_fill, quantity, block_reason)
    if margin_impact and not margin_impact.get("allowed", True) and block_reason is None:
        block_reason = margin_impact.get("message") or "Margin utilization limit reached"

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
        "bracket": bool(
            order_req.get("bracket")
            or sl_price is not None
            or tp_price is not None
            or trailing_stop_percent is not None
        ),
        "trailing_stop_percent": float(trailing_stop_percent) if trailing_stop_percent is not None else None,
        "costs": {
            "fee_bps": fee_bps,
            "slippage_bps": slippage_bps,
            "estimated_fee": round(estimated_fee, 2),
            "estimated_slippage": round(estimated_slippage, 2),
            "estimated_fill_price": round(est_fill, 8),
            "estimated_total_cost": round(estimated_fee + estimated_slippage, 2),
        },
        "margin": margin_impact,
    }


def _preview_margin_impact(oms, side: str, fill_price: float, quantity: float, block_reason: str | None) -> dict | None:
    if side != "BUY" or not RISK_MARGIN_ENABLED or not hasattr(oms, "get_account_data"):
        return None
    try:
        portfolio = build_portfolio_snapshot(oms)
        margin = build_margin_snapshot(oms, portfolio)
        if not margin.enabled:
            return {"enabled": False}

        margin_ok, margin_reason, _ = validate_margin_entry(
            margin,
            price=fill_price,
            quantity=quantity,
        )
        required = entry_margin_required(fill_price * quantity, 1.0)
        capacity = max(margin.margin_capacity, 1.0)
        util_before = margin.utilization_pct
        util_after = round(((margin.margin_used + required) / capacity) * 100, 2)

        if not margin_ok and block_reason is None:
            warnings_msg = margin_reason
        else:
            warnings_msg = None

        return {
            "enabled": True,
            "margin_required": round(required, 2),
            "utilization_pct_before": util_before,
            "utilization_pct_after": min(util_after, 999.0),
            "available_cash": margin.available_cash,
            "allowed": margin_ok,
            "message": warnings_msg if not margin_ok else margin_reason if margin_reason != "OK" else None,
        }
    except Exception:
        return {"enabled": True, "allowed": True, "message": None}


def _blocked(reason: str) -> dict:
    return {
        "status": "preview",
        "allowed": False,
        "block_reason": reason,
        "warnings": [],
    }
