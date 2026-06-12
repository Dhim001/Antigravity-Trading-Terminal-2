from app.api.context import RequestContext
from app.api.protocol import Action
from app.api.responses import send_order_bundle, send_order_bundle_no_history
from app.api.router import route


@route(Action.PLACE_ORDER, tags=["trading"])
async def place_order(ctx: RequestContext) -> None:
    msg = ctx.message
    symbol = msg.get("symbol")
    order_type = msg.get("type")
    side = msg.get("side")
    price = float(msg.get("price")) if msg.get("price") is not None else None
    quantity = float(msg.get("quantity"))

    stop_loss_percent = msg.get("stop_loss_percent")
    take_profit_percent = msg.get("take_profit_percent")
    stop_loss_price = msg.get("stop_loss_price")
    take_profit_price = msg.get("take_profit_price")

    ref_price = price if price is not None and price > 0 else None
    if ref_price is None and hasattr(ctx.oms, "feed") and symbol in ctx.oms.feed._symbols:
        ref_price = ctx.oms.feed._symbols[symbol]["price"]

    if ref_price and ref_price > 0:
        if stop_loss_price is not None and stop_loss_percent is None:
            stop_loss_percent = round(abs(ref_price - float(stop_loss_price)) / ref_price * 100, 2)
        if take_profit_price is not None and take_profit_percent is None:
            take_profit_percent = round(abs(ref_price - float(take_profit_price)) / ref_price * 100, 2)

    if stop_loss_percent is not None:
        stop_loss_percent = float(stop_loss_percent)
    if take_profit_percent is not None:
        take_profit_percent = float(take_profit_percent)

    result = await ctx.oms.place_order({
        "symbol": symbol,
        "type": order_type,
        "side": side,
        "price": price,
        "quantity": quantity,
        "stop_loss_percent": stop_loss_percent,
        "take_profit_percent": take_profit_percent,
    })
    await send_order_bundle(ctx, result)


@route(Action.CANCEL_ORDER, tags=["trading"])
async def cancel_order(ctx: RequestContext) -> None:
    order_id = ctx.message.get("order_id")
    result = await ctx.oms.cancel_order(order_id)
    await send_order_bundle(ctx, result)


@route(Action.UPDATE_POSITION_SL_TP, tags=["trading"])
async def update_position_sl_tp(ctx: RequestContext) -> None:
    msg = ctx.message
    symbol = msg.get("symbol")
    stop_loss_percent = msg.get("stop_loss_percent")
    take_profit_percent = msg.get("take_profit_percent")
    stop_loss_price = msg.get("stop_loss_price")
    take_profit_price = msg.get("take_profit_price")

    if stop_loss_percent is not None:
        stop_loss_percent = float(stop_loss_percent)
    if take_profit_percent is not None:
        take_profit_percent = float(take_profit_percent)
    if stop_loss_price is not None:
        stop_loss_price = float(stop_loss_price)
    if take_profit_price is not None:
        take_profit_price = float(take_profit_price)

    result = await ctx.oms.update_position_sl_tp(
        symbol,
        stop_loss_percent=stop_loss_percent,
        take_profit_percent=take_profit_percent,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
    )
    await send_order_bundle_no_history(ctx, result)
