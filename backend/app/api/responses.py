from app.api.context import RequestContext
from app.api.outbound import (
    account_update,
    backtest_progress,
    backtest_result,
    bot_detail,
    bots_history,
    bots_update,
    error,
    history_update,
    order_result,
    system_stats,
    trade_history,
)


async def send_to(ctx: RequestContext, payload: dict) -> None:
    if ctx.websocket is not None:
        await ctx.manager.send_to(ctx.websocket, payload)
        return
    # HTTP dual-transport: HttpConnectionManager collects replies without a socket.
    if hasattr(ctx.manager, "messages"):
        await ctx.manager.send_to(None, payload)


async def broadcast(ctx: RequestContext, payload: dict) -> None:
    await ctx.manager.broadcast(payload)


async def send_error(ctx: RequestContext, message: str) -> None:
    await send_to(ctx, error(message))


async def send_order_result(ctx: RequestContext, data: dict) -> None:
    await send_to(ctx, order_result(data))


async def send_account_update(ctx: RequestContext) -> None:
    await send_to(ctx, account_update(ctx.oms.get_account_data()))


async def send_trade_history(ctx: RequestContext) -> None:
    await send_to(ctx, trade_history(ctx.oms.get_trade_history()))


async def send_order_bundle(ctx: RequestContext, result: dict) -> None:
    """Order result + account + trade history (standard post-trade reply)."""
    await send_order_result(ctx, result)
    await send_account_update(ctx)
    await send_trade_history(ctx)


async def send_order_bundle_no_history(ctx: RequestContext, result: dict) -> None:
    """Order result + account update only."""
    await send_order_result(ctx, result)
    await send_account_update(ctx)


async def broadcast_bots_update(ctx: RequestContext) -> None:
    await broadcast(ctx, bots_update(ctx.bot_manager.list_bots_public()))


async def send_history_update(ctx: RequestContext, data: dict, meta: dict | None = None) -> None:
    payload = history_update(data, meta=meta)
    await send_to(ctx, payload)


async def send_bot_detail(ctx: RequestContext, data: dict) -> None:
    await send_to(ctx, bot_detail(data))


async def send_backtest_result(ctx: RequestContext, data: dict) -> None:
    await send_to(ctx, backtest_result(data))


async def send_backtest_progress(ctx: RequestContext, data: dict) -> None:
    await send_to(ctx, backtest_progress(data))


async def broadcast_order_result(ctx: RequestContext, data: dict) -> None:
    await broadcast(ctx, order_result(data))


async def broadcast_account_update(ctx: RequestContext) -> None:
    await broadcast(ctx, account_update(ctx.oms.get_account_data()))


async def broadcast_trade_history(ctx: RequestContext) -> None:
    await broadcast(ctx, trade_history(ctx.oms.get_trade_history()))


async def send_bots_update(ctx: RequestContext) -> None:
    await send_to(ctx, bots_update(ctx.bot_manager.list_bots_public()))


async def send_bots_history(ctx: RequestContext, data: list | None = None) -> None:
    payload = data if data is not None else ctx.bot_manager.list_all_bots_public()
    await send_to(ctx, bots_history(payload))


async def send_system_stats(ctx: RequestContext, data: dict) -> None:
    await send_to(ctx, system_stats(data))
