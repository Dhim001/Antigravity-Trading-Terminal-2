import logging

from app.api.context import RequestContext
from app.api.protocol import Action
from app.api.responses import (
    broadcast_account_update,
    broadcast_bots_update,
    broadcast_order_result,
    broadcast_trade_history,
    send_account_update,
    send_order_result,
    send_system_stats,
)
from app.api.router import route
from app.database import get_connection, get_db_stats, reset_db
from app.services.events import channels
from app.services.events import publish as event_publish

logger = logging.getLogger(__name__)


async def _notify_bot_registry_change() -> None:
    await event_publish.publish(channels.BOT_RELOAD, {})


@route(Action.ADMIN_SET_SIMULATION, tags=["admin"])
async def admin_set_simulation(ctx: RequestContext) -> None:
    msg = ctx.message
    tick_interval = msg.get("tick_interval")
    volatility_multiplier = msg.get("volatility_multiplier")
    symbol = msg.get("symbol")
    bias = msg.get("bias")

    success = False
    if hasattr(ctx.oms, "feed") and hasattr(ctx.oms.feed, "tick_interval"):
        if tick_interval is not None:
            ctx.oms.feed.tick_interval = float(tick_interval)
        if volatility_multiplier is not None:
            ctx.oms.feed.volatility_multiplier = float(volatility_multiplier)
        if symbol and bias:
            ctx.oms.feed.biases[symbol] = bias
            logger.info("Admin override simulation for %s: bias=%s", symbol, bias)
        success = True

    await send_order_result(ctx, {
        "status": "success" if success else "error",
        "message": "Simulation settings updated" if success else "Simulation controls disabled in live trading mode",
    })


@route(
    Action.ADMIN_SEED_BALANCE,
    sim_only=True,
    sim_denied_message="Manual balance seeding is disabled in live trading mode.",
    tags=["admin"],
)
async def admin_seed_balance(ctx: RequestContext) -> None:
    msg = ctx.message
    asset = msg.get("asset")
    amount = float(msg.get("amount", 0.0))

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM accounts WHERE asset = ?", (asset,))
        if cursor.fetchone()[0] > 0:
            cursor.execute("UPDATE accounts SET balance = balance + ? WHERE asset = ?", (amount, asset))
        else:
            cursor.execute("INSERT INTO accounts (asset, balance, locked) VALUES (?, ?, 0.0)", (asset, amount))
        conn.commit()
        status = "success"
        result_msg = f"Seeded {amount:.2f} {asset} successfully"
    except Exception as exc:
        conn.rollback()
        status = "error"
        result_msg = f"Failed to seed balance: {exc}"
    finally:
        conn.close()

    await send_order_result(ctx, {"status": status, "message": result_msg})
    await send_account_update(ctx)


@route(
    Action.ADMIN_RESET_SYSTEM,
    sim_only=True,
    sim_denied_message="System nuclear reset is disabled in live trading mode.",
    tags=["admin"],
)
async def admin_reset_system(ctx: RequestContext) -> None:
    try:
        reset_db()
        ctx.bot_manager.active_bots.clear()
        ctx.bot_manager._executed_signals.clear()
        if hasattr(ctx.oms, "feed"):
            ctx.oms.feed.tick_interval = 0.25
            ctx.oms.feed.volatility_multiplier = 1.0
            ctx.oms.feed.biases.clear()
        status = "success"
        result_msg = "System database reset successfully to defaults"
    except Exception as exc:
        status = "error"
        result_msg = f"Failed to reset database: {exc}"

    await broadcast_order_result(ctx, {"status": status, "message": result_msg})
    await broadcast_account_update(ctx)
    await broadcast_trade_history(ctx)
    await broadcast_bots_update(ctx)


@route(Action.ADMIN_EMERGENCY_STOP, tags=["admin"])
async def admin_emergency_stop(ctx: RequestContext) -> None:
    bot_count = await ctx.bot_manager.stop_all_bots()
    result = await ctx.oms.emergency_stop()
    msg = result.get("message", "Emergency stop executed.")
    result["message"] = f"{msg} Stopped {bot_count} bot(s)."
    await _notify_bot_registry_change()
    await send_order_result(ctx, result)
    await broadcast_account_update(ctx)
    await broadcast_trade_history(ctx)
    await broadcast_bots_update(ctx)


@route(Action.ADMIN_GET_STATS, tags=["admin"])
async def admin_get_stats(ctx: RequestContext) -> None:
    stats = get_db_stats()
    if hasattr(ctx.oms, "feed") and hasattr(ctx.oms.feed, "tick_interval"):
        stats["tick_interval"] = ctx.oms.feed.tick_interval
        stats["volatility_multiplier"] = ctx.oms.feed.volatility_multiplier
    else:
        stats["tick_interval"] = 1.0
        stats["volatility_multiplier"] = 1.0

    await send_system_stats(ctx, stats)
