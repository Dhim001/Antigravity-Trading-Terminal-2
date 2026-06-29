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
from app.api.outbound import publish_market_update
from app.services.events import channels
from app.services.events import publish as event_publish

logger = logging.getLogger(__name__)


async def _broadcast_market_snapshot(ctx: RequestContext) -> None:
    """Push current in-memory prices to all clients without resetting the sim feed."""
    feed = getattr(ctx.oms, "feed", None)
    if not feed or not hasattr(feed, "get_market_data"):
        return
    slim = {}
    for symbol in feed.symbols:
        md = feed.get_market_data(symbol)
        if not md:
            continue
        slim[symbol] = {
            "symbol": symbol,
            "price": md["price"],
            "change_24h": md.get("change_24h"),
            "volume_24h": md.get("volume_24h"),
            "high_24h": md.get("high_24h"),
            "low_24h": md.get("low_24h"),
            "candle": md.get("candle"),
        }
    if slim:
        await publish_market_update(ctx.manager.broadcast, slim)


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
        from app.services.bots import signal_ledger
        signal_ledger.clear_signal_ledger()
        feed = getattr(ctx.oms, "feed", None)
        if feed is not None:
            if hasattr(feed, "tick_interval"):
                feed.tick_interval = 0.25
            if hasattr(feed, "volatility_multiplier"):
                feed.volatility_multiplier = 1.0
            if hasattr(feed, "biases"):
                feed.biases.clear()
            if hasattr(feed, "persist_state"):
                feed.persist_state()
        status = "success"
        result_msg = "Trading state reset to defaults — market prices preserved"
    except Exception as exc:
        status = "error"
        result_msg = f"Failed to reset database: {exc}"

    await broadcast_order_result(ctx, {"status": status, "message": result_msg})
    await broadcast_account_update(ctx)
    await broadcast_trade_history(ctx)
    await broadcast_bots_update(ctx)
    if status == "success":
        await _broadcast_market_snapshot(ctx)


@route(Action.ADMIN_EMERGENCY_STOP, tags=["admin"])
async def admin_emergency_stop(ctx: RequestContext) -> None:
    bot_count = await ctx.bot_manager.stop_all_bots()
    result = await ctx.oms.emergency_stop()
    msg = result.get("message", "Emergency stop executed.")
    result["message"] = f"{msg} Stopped {bot_count} bot(s)."
    await event_publish.publish(channels.EMERGENCY_STOP, {"source": "admin"})
    await _notify_bot_registry_change()
    await send_order_result(ctx, result)
    await broadcast_account_update(ctx)
    await broadcast_trade_history(ctx)
    await broadcast_bots_update(ctx)


@route(Action.ADMIN_RESET_RISK_KILL_SWITCH, tags=["admin"])
async def admin_reset_risk_kill_switch(ctx: RequestContext) -> None:
    from app.services.bots.portfolio_risk import build_portfolio_snapshot
    from app.services.bots.risk_state_store import reset_kill_switch

    snap = build_portfolio_snapshot(ctx.oms)
    reset_kill_switch(current_equity=snap.account_equity)
    await send_order_result(ctx, {
        "status": "success",
        "message": (
            f"Drawdown kill switch reset. Peak equity re-based to "
            f"${snap.account_equity:,.2f}."
        ),
    })


@route(Action.ADMIN_GET_STATS, tags=["admin"])
async def admin_get_stats(ctx: RequestContext) -> None:
    stats = get_db_stats()
    if hasattr(ctx.oms, "feed") and hasattr(ctx.oms.feed, "tick_interval"):
        stats["tick_interval"] = ctx.oms.feed.tick_interval
        stats["volatility_multiplier"] = ctx.oms.feed.volatility_multiplier
    else:
        stats["tick_interval"] = 1.0
        stats["volatility_multiplier"] = 1.0

    try:
        from app.services.bots.portfolio_risk import build_portfolio_snapshot
        from app.config import (
            PORTFOLIO_MAX_GROSS_EXPOSURE_PCT,
            PORTFOLIO_MAX_GROUP_EXPOSURE_PCT,
        )
        from app.services.bots.margin_risk import build_margin_snapshot, margin_to_dict

        snap = build_portfolio_snapshot(ctx.oms)
        margin = margin_to_dict(build_margin_snapshot(ctx.oms, snap))
        stats["portfolio"] = {
            "equity": round(snap.account_equity, 2),
            "gross_exposure": round(snap.gross_exposure, 2),
            "gross_exposure_pct": round(
                snap.gross_exposure / snap.account_equity * 100, 1
            ) if snap.account_equity else 0,
            "max_gross_pct": PORTFOLIO_MAX_GROSS_EXPOSURE_PCT,
            "max_group_pct": PORTFOLIO_MAX_GROUP_EXPOSURE_PCT,
            "group_exposure": {k: round(v, 2) for k, v in snap.group_exposure.items()},
            "margin": margin,
            "margin_utilization_pct": margin.get("utilization_pct", 0.0),
        }
    except Exception:
        pass

    await send_system_stats(ctx, stats)


@route(Action.ADMIN_ARCHIVE_BACKFILL, tags=["admin"])
async def admin_archive_backfill(ctx: RequestContext) -> None:
    """Import seed parquet / feed buffer into market_bars_1m (SQLite or Postgres)."""
    from app.config import TERMINAL_MODE
    from app.services.archive.backfill import run_archive_backfill

    feed = getattr(ctx.oms, "feed", None)
    force = bool(ctx.message.get("force"))
    symbols = ctx.message.get("symbols")
    if symbols and not isinstance(symbols, list):
        symbols = [symbols]

    try:
        result = run_archive_backfill(
            symbols,
            feed=feed,
            source=TERMINAL_MODE or "SIMULATED",
            skip_existing=not force,
        )
        await send_order_result(ctx, {
            "status": "success",
            "message": f"Archive backfill wrote {result.get('rows_written', 0)} rows",
            "archive_backfill": result,
        })
    except Exception as exc:
        await send_order_result(ctx, {"status": "error", "message": str(exc)})


@route(Action.ADMIN_ARCHIVE_EXPORT, tags=["admin"])
async def admin_archive_export(ctx: RequestContext) -> None:
    from app.config import ARCHIVE_PARQUET_ENABLED, ARCHIVE_PARQUET_DIR
    from app.services.archive.parquet_export import export_all_symbols

    if not ARCHIVE_PARQUET_ENABLED:
        await send_order_result(ctx, {
            "status": "error",
            "message": "Parquet export disabled. Set ARCHIVE_PARQUET_ENABLED=true or ARCHIVE_BACKEND=both.",
        })
        return

    feed = getattr(ctx.oms, "feed", None)
    symbols = ctx.message.get("symbols")
    if symbols and not isinstance(symbols, list):
        symbols = [symbols]
    if not symbols and feed is not None:
        symbols = list(feed.symbols)
    days = int(ctx.message.get("days") or 90)
    interval = ctx.message.get("interval") or "auto"

    try:
        result = export_all_symbols(symbols or [], days=days, interval=interval)
        await send_order_result(ctx, {
            "status": "success",
            "message": f"Exported {result.get('total_rows', 0)} rows to {ARCHIVE_PARQUET_DIR}",
            "archive_export": result,
        })
    except Exception as exc:
        await send_order_result(ctx, {"status": "error", "message": str(exc)})


@route(Action.ADMIN_GET_RECONCILIATION, tags=["admin"])
async def admin_get_reconciliation(ctx: RequestContext) -> None:
    from app.services.reconciliation import list_ambiguous_orders

    pending = list_ambiguous_orders(include_resolved=False)
    await send_order_result(ctx, {
        "status": "success",
        "message": f"{len(pending)} ambiguous order(s) pending review",
        "reconciliation": {"pending": pending, "count": len(pending)},
    })


@route(Action.ADMIN_RECONCILE, tags=["admin"])
async def admin_reconcile(ctx: RequestContext) -> None:
    from app.services.reconciliation import auto_reconcile_with_portfolio

    result = auto_reconcile_with_portfolio(ctx.oms)
    await send_order_result(ctx, {
        "status": "success",
        "message": f"Auto-reconciled {result.get('matched', 0)} order(s)",
        "reconciliation": result,
    })


@route(Action.ADMIN_RESOLVE_AMBIGUOUS, tags=["admin"])
async def admin_resolve_ambiguous(ctx: RequestContext) -> None:
    from app.services.reconciliation import resolve_ambiguous_order

    order_id = ctx.message.get("order_id") or ctx.message.get("id")
    resolution = ctx.message.get("resolution") or "dismissed"
    note = ctx.message.get("note") or ""
    if not order_id:
        await send_order_result(ctx, {"status": "error", "message": "order_id is required"})
        return
    ok = resolve_ambiguous_order(order_id, resolution, note)
    await send_order_result(ctx, {
        "status": "success" if ok else "error",
        "message": "Ambiguous order resolved" if ok else "Order not found or already resolved",
    })


@route(Action.ADMIN_GET_SAFE_MODE, tags=["admin"])
async def admin_get_safe_mode(ctx: RequestContext) -> None:
    from app.services.runtime.system_state import runtime_status_dict

    runtime = runtime_status_dict()
    await send_order_result(ctx, {
        "status": "success",
        "message": "Safe mode active — confirm before resuming bots" if runtime.get("safe_mode", {}).get("active") else "System operational",
        "runtime": runtime,
    })


@route(Action.ADMIN_CONFIRM_SAFE_MODE, tags=["admin"])
async def admin_confirm_safe_mode(ctx: RequestContext) -> None:
    from app.services.runtime.startup_recovery import confirm_safe_mode

    result = confirm_safe_mode()
    ctx.bot_manager.load_bots_from_db()
    await broadcast_bots_update(ctx)
    await event_publish.publish(channels.BOT_RELOAD, {})
    await send_order_result(ctx, {
        "status": "success",
        "message": "Safe mode cleared — resume bots manually when ready",
        "runtime": result,
    })
