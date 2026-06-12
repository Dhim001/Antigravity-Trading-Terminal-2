from app.api.context import RequestContext
from app.api.protocol import Action
from app.api.responses import send_history_update
from app.api.router import route


@route(Action.SUBSCRIBE_SYMBOL, tags=["market"])
async def subscribe_symbol(ctx: RequestContext) -> None:
    symbol = ctx.message.get("symbol")
    if symbol:
        ctx.manager.set_client_symbol(ctx.websocket, symbol)
    if symbol and hasattr(ctx.oms, "feed"):
        candles = ctx.oms.feed.get_candles(symbol)
        await send_history_update(ctx, {symbol: candles})
