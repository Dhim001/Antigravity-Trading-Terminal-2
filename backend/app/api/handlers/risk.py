"""Risk configuration and entry preview handlers."""

from __future__ import annotations

from app.api.context import RequestContext
from app.api.protocol import Action
from app.api.responses import send_order_result
from app.api.router import route
from app.services.risk_preview import get_risk_config, preview_entry
from app.services.bots.correlation import summarize_basket_correlation


@route(Action.RISK_GET_CONFIG, tags=["risk"])
async def risk_get_config(ctx: RequestContext) -> None:
    config = get_risk_config(oms=ctx.oms)
    await send_order_result(ctx, {
        "status": "success",
        "message": "Risk configuration",
        "risk_config": config,
    })


@route(Action.RISK_PREVIEW_ENTRY, tags=["risk"])
async def risk_preview_entry(ctx: RequestContext) -> None:
    msg = ctx.message
    symbol = (msg.get("symbol") or "").strip()
    if not symbol:
        await send_order_result(ctx, {"status": "error", "message": "symbol is required"})
        return

    side = (msg.get("side") or "BUY").strip()
    notional = msg.get("notional")
    quantity = msg.get("quantity")
    price = msg.get("price")

    try:
        notional_f = float(notional) if notional not in (None, "") else None
        quantity_f = float(quantity) if quantity not in (None, "") else None
        price_f = float(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        await send_order_result(ctx, {"status": "error", "message": "Invalid numeric field"})
        return

    result = preview_entry(
        ctx.oms,
        symbol=symbol,
        side=side,
        notional=notional_f,
        quantity=quantity_f,
        price=price_f,
    )
    if result.get("error"):
        await send_order_result(ctx, {"status": "error", "message": result["error"]})
        return

    await send_order_result(ctx, {
        "status": "success",
        "message": "Entry allowed" if result.get("allowed") else "Entry blocked",
        "risk_preview": result,
    })


@route(Action.RISK_BASKET_CORRELATION, tags=["risk"])
async def risk_basket_correlation(ctx: RequestContext) -> None:
    raw = ctx.message.get("symbols")
    if isinstance(raw, str):
        symbols = [s.strip() for s in raw.split(",") if s.strip()]
    elif isinstance(raw, list):
        symbols = [str(s).strip() for s in raw if s]
    else:
        symbols = []

    feed = getattr(ctx.oms, "feed", None)
    summary = summarize_basket_correlation(symbols, feed=feed)
    await send_order_result(ctx, {
        "status": "success",
        "message": f"Basket correlation ({len(summary.get('symbols') or symbols)} symbols)",
        "basket_correlation": summary,
    })
