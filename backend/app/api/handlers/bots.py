from app.api.context import RequestContext
from app.api.protocol import Action
from app.api.responses import (
    broadcast_bots_update,
    send_backtest_result,
    send_bot_detail,
    send_bots_history,
    send_bots_update,
    send_order_result,
)
from app.api.router import route
from app.services.events import channels
from app.services.events import publish as event_publish


async def _notify_bot_registry_change() -> None:
    await event_publish.publish(channels.BOT_RELOAD, {})


async def _bot_mutation_success(ctx: RequestContext, message: str) -> None:
    await send_order_result(ctx, {"status": "success", "message": message})
    await broadcast_bots_update(ctx)
    await _notify_bot_registry_change()


async def _bot_mutation_error(ctx: RequestContext, exc: Exception) -> None:
    await send_order_result(ctx, {"status": "error", "message": str(exc)})


@route(Action.BOT_CREATE, tags=["bots"])
async def bot_create(ctx: RequestContext) -> None:
    msg = ctx.message
    strategy = msg.get("strategy")
    symbol = msg.get("symbol")
    timeframe = msg.get("timeframe", "1m")
    allocation = float(msg.get("allocation", 1000))
    config = msg.get("config", {})
    execution_mode = msg.get("execution_mode", "BAR_CLOSE")

    try:
        bot_id = await ctx.bot_manager.create_bot(
            strategy, symbol, timeframe, allocation, config, execution_mode=execution_mode
        )
        await send_order_result(ctx, {
            "status": "success",
            "message": f"Bot {bot_id} created successfully",
        })
        await broadcast_bots_update(ctx)
        await _notify_bot_registry_change()
    except Exception as exc:
        await _bot_mutation_error(ctx, exc)


@route(Action.BOT_STOP, tags=["bots"])
async def bot_stop(ctx: RequestContext) -> None:
    bot_id = ctx.message.get("bot_id")
    try:
        await ctx.bot_manager.stop_bot(bot_id)
        await _bot_mutation_success(ctx, "Bot stopped successfully")
    except Exception as exc:
        await _bot_mutation_error(ctx, exc)


@route(Action.BOT_PAUSE, tags=["bots"])
async def bot_pause(ctx: RequestContext) -> None:
    bot_id = ctx.message.get("bot_id")
    try:
        await ctx.bot_manager.pause_bot(bot_id)
        await _bot_mutation_success(ctx, "Bot paused")
    except Exception as exc:
        await _bot_mutation_error(ctx, exc)


@route(Action.BOT_RESUME, tags=["bots"])
async def bot_resume(ctx: RequestContext) -> None:
    bot_id = ctx.message.get("bot_id")
    try:
        await ctx.bot_manager.resume_bot(bot_id)
        await _bot_mutation_success(ctx, "Bot resumed")
    except Exception as exc:
        await _bot_mutation_error(ctx, exc)


@route(Action.BOT_STOP_ALL, tags=["bots"])
async def bot_stop_all(ctx: RequestContext) -> None:
    try:
        count = await ctx.bot_manager.stop_all_bots()
        await _bot_mutation_success(ctx, f"Stopped {count} bot(s)")
    except Exception as exc:
        await _bot_mutation_error(ctx, exc)


@route(Action.BOT_GET_DETAIL, tags=["bots"])
async def bot_get_detail(ctx: RequestContext) -> None:
    bot_id = ctx.message.get("bot_id")
    detail = ctx.bot_manager.get_bot_detail(bot_id)
    if detail:
        await send_bot_detail(ctx, detail)
    else:
        await send_order_result(ctx, {"status": "error", "message": "Bot not found"})


@route(Action.BOT_UPDATE_CONFIG, tags=["bots"])
async def bot_update_config(ctx: RequestContext) -> None:
    bot_id = ctx.message.get("bot_id")
    config_patch = ctx.message.get("config", {})
    try:
        detail = await ctx.bot_manager.update_bot_config(bot_id, config_patch)
        await send_bot_detail(ctx, detail)
        await send_order_result(ctx, {"status": "success", "message": "Bot config updated"})
        await broadcast_bots_update(ctx)
    except Exception as exc:
        await _bot_mutation_error(ctx, exc)


@route(Action.BOT_GET_ALL, tags=["bots"])
async def bot_get_all(ctx: RequestContext) -> None:
    await send_bots_update(ctx)


@route(Action.BOT_LIST_ALL, tags=["bots"])
async def bot_list_all(ctx: RequestContext) -> None:
    try:
        limit = int(ctx.message.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 500))
    await send_bots_history(ctx, ctx.bot_manager.list_all_bots_public(limit=limit))


@route(Action.RUN_BACKTEST, tags=["bots"])
async def run_backtest(ctx: RequestContext) -> None:
    msg = ctx.message
    symbol = msg.get("symbol")
    strategy = msg.get("strategy")
    config = msg.get("config", {})

    try:
        days = int(msg.get("days", 7))
    except (TypeError, ValueError):
        days = 7
    days = max(1, min(days, 365))
    interval = msg.get("interval")

    if ctx.backtester and hasattr(ctx.oms, "feed"):
        from app.services.archive.resolve import resolve_backtest_candles

        candles, meta = resolve_backtest_candles(
            symbol,
            ctx.oms.feed,
            days=days,
            interval=interval,
        )
        results = ctx.backtester.run_backtest(symbol, strategy, config, candles)
        if isinstance(results, dict) and "error" not in results:
            results["meta"] = meta
            from app.services.bots.backtest_store import save_backtest_run

            run_id = save_backtest_run(symbol, strategy, config, days, results)
            results["run_id"] = run_id
        await send_backtest_result(ctx, {"status": "success", "results": results})
    else:
        await send_backtest_result(ctx, {"status": "error", "message": "Backtester not available in current mode"})
