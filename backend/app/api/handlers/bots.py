import asyncio
from functools import partial

from app.api.context import RequestContext
from app.api.protocol import Action
from app.api.responses import (
    broadcast_bots_update,
    send_backtest_progress,
    send_backtest_result,
    send_bot_detail,
    send_bots_history,
    send_bots_update,
    send_order_result,
)
from app.api.router import route
from app.services.bots.backtest_jobs import cancel_job, clear_job, start_job
from app.services.bots.backtest_job_store import (
    create_backtest_job,
    is_job_cancelled,
    set_job_status,
    update_job_progress,
)
from app.services.bots.backtest_sweep import expand_sweep_grid, sweep_label
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


async def _drain_backtest_progress(ctx: RequestContext, queue: asyncio.Queue) -> None:
    while True:
        item = await queue.get()
        if item is None:
            break
        await send_backtest_progress(ctx, item)


def _parse_backtest_request(msg: dict) -> dict:
    try:
        days = int(msg.get("days", 7))
    except (TypeError, ValueError):
        days = 7
    days = max(1, min(days, 365))

    oos_pct = msg.get("oos_pct")
    if oos_pct is not None:
        try:
            oos_pct = max(10.0, min(90.0, float(oos_pct)))
        except (TypeError, ValueError):
            oos_pct = None

    walk_forward = bool(msg.get("walk_forward"))
    train_pct = msg.get("train_pct")
    if train_pct is not None:
        try:
            train_pct = max(50.0, min(90.0, float(train_pct)))
        except (TypeError, ValueError):
            train_pct = 70.0
    else:
        train_pct = 70.0

    config = msg.get("config", {}) or {}
    sim_mode = msg.get("sim_mode") or config.get("sim_mode")
    if sim_mode:
        config = {**config, "sim_mode": sim_mode}

    return {
        "symbol": msg.get("symbol"),
        "strategy": msg.get("strategy"),
        "config": config,
        "days": days,
        "interval": msg.get("interval"),
        "timeframe": msg.get("timeframe", "1m"),
        "oos_pct": oos_pct,
        "walk_forward": walk_forward,
        "train_pct": train_pct,
        "sweep": msg.get("sweep"),
    }


def _apply_oos_window(candles: list, meta: dict, oos_pct: float | None) -> list:
    if not oos_pct or not candles:
        return candles
    split = int(len(candles) * (1 - oos_pct / 100.0))
    split = max(50, min(split, len(candles) - 50))
    if split <= 0 or split >= len(candles):
        return candles
    trimmed = candles[split:]
    if isinstance(meta, dict):
        meta["oos_pct"] = oos_pct
        meta["oos_bars"] = len(trimmed)
        if trimmed:
            meta["oldest"] = trimmed[0].get("time", meta.get("oldest"))
    return trimmed


async def _execute_backtest(
    ctx: RequestContext,
    *,
    job_id: str | None = None,
    symbol: str,
    strategy: str,
    config: dict,
    days: int,
    interval,
    timeframe: str,
    oos_pct: float | None = None,
    sweep: dict | None = None,
    walk_forward: bool = False,
    train_pct: float = 70.0,
) -> None:
    if not ctx.backtester or not hasattr(ctx.oms, "feed"):
        await send_backtest_result(ctx, {"status": "error", "message": "Backtester not available in current mode"})
        return

    request_payload = {
        "symbol": symbol,
        "strategy": strategy,
        "config": config,
        "days": days,
        "interval": interval,
        "timeframe": timeframe,
        "oos_pct": oos_pct,
        "sweep": sweep,
        "walk_forward": walk_forward,
        "train_pct": train_pct,
    }
    client_key = str(id(ctx.websocket)) if ctx.websocket is not None else None
    if not job_id:
        job_id = create_backtest_job(
            request_payload,
            status="running",
            client_key=client_key,
        )

    job = start_job(ctx.websocket, job_id)
    progress_queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    drain_task = asyncio.create_task(_drain_backtest_progress(ctx, progress_queue))

    def enqueue_progress(data: dict) -> None:
        payload = {**data, "job_id": job_id}
        update_job_progress(job_id, payload)
        try:
            progress_queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass

    def is_cancelled() -> bool:
        if job and job.is_cancelled():
            return True
        return is_job_cancelled(job_id)

    async def _finish(status: str, *, message: str | None = None, results: dict | None = None, run_id: str | None = None) -> None:
        if status == "cancelled":
            set_job_status(job_id, "cancelled", error=message)
            await send_backtest_result(ctx, {"status": "cancelled", "message": message or "Backtest cancelled", "job_id": job_id})
        elif status == "error":
            set_job_status(job_id, "failed", error=message)
            await send_backtest_result(ctx, {"status": "error", "message": message or "Backtest failed", "job_id": job_id})
        elif status == "success" and results is not None:
            set_job_status(job_id, "completed", run_id=run_id, results=results)
            await send_backtest_result(ctx, {"status": "success", "results": results, "job_id": job_id})

    try:
        from app.services.archive.resolve import resolve_backtest_candles

        enqueue_progress({"pct": 0, "phase": "resolve", "message": "Loading candles…"})
        try:
            candles, meta = await asyncio.to_thread(
                resolve_backtest_candles,
                symbol,
                ctx.oms.feed,
                days=days,
                interval=interval,
                timeframe=timeframe,
            )
        except ValueError as exc:
            await _finish("error", message=str(exc))
            return

        if is_cancelled():
            await _finish("cancelled")
            return

        candles = _apply_oos_window(candles, meta, oos_pct)
        bar_count = len(candles or [])
        configs = expand_sweep_grid(config, sweep) if sweep else [config]
        is_sweep = len(configs) > 1 or bool(sweep)

        if walk_forward and not is_sweep:
            await _finish("error", message="Walk-forward requires a parameter sweep grid")
            return

        if walk_forward and is_sweep:
            from app.services.bots.backtest_walk_forward import run_walk_forward

            enqueue_progress({
                "pct": 10,
                "phase": "sweep",
                "message": f"Walk-forward: optimizing on {int(train_pct)}% train window…",
                "bars": bar_count,
                "total_runs": len(configs),
            })

            def wf_progress(done: int, total: int) -> None:
                pct = 10 + int((done / max(total, 1)) * 80)
                enqueue_progress({
                    "pct": min(pct, 92),
                    "phase": "sweep",
                    "message": f"Walk-forward bar {done}/{total}…",
                    "bar": done,
                    "bars": total,
                })

            best_result = await asyncio.to_thread(
                partial(
                    run_walk_forward,
                    run_backtest=ctx.backtester.run_backtest,
                    symbol=symbol,
                    strategy=strategy,
                    base_config=config,
                    candles=candles,
                    meta=meta,
                    configs=configs,
                    train_pct=train_pct,
                    progress_cb=wf_progress,
                    cancel_cb=is_cancelled,
                ),
            )
            if isinstance(best_result, dict) and best_result.get("cancelled"):
                await _finish("cancelled")
                return
            if isinstance(best_result, dict) and best_result.get("error"):
                await _finish("error", message=best_result["error"])
                return
            best_config = (best_result.get("walk_forward") or {}).get("best_config") or config
            sweep_rows = (best_result.get("sweep") or {}).get("results") or []
        else:
            sweep_rows = []
            best_result = None
            best_config = None

        if not (walk_forward and is_sweep):
            enqueue_progress({
                "pct": 8,
                "phase": "indicators" if not is_sweep else "sweep",
                "message": f"Running {len(configs)} configuration{'s' if len(configs) != 1 else ''}…",
                "bars": bar_count,
                "total_runs": len(configs),
            })

            sweep_rows = []
            best_result = None
            best_config = None

            for run_idx, run_config in enumerate(configs):
                if is_cancelled():
                    await _finish("cancelled")
                    return

                def progress_cb(done: int, total: int, *, _run=run_idx, _runs=len(configs)) -> None:
                    run_span = 85 / max(_runs, 1)
                    base = 10 + _run * run_span
                    pct = base + int((done / max(total, 1)) * run_span)
                    enqueue_progress({
                        "pct": min(int(pct), 95),
                        "phase": "sweep" if is_sweep else "simulate",
                        "message": (
                            f"Sweep {run_idx + 1}/{len(configs)}: bar {done}/{total}…"
                            if is_sweep
                            else f"Simulating bar {done}/{total}…"
                        ),
                        "bar": done,
                        "bars": total,
                        "run": run_idx + 1,
                        "total_runs": len(configs),
                    })

                results = await asyncio.to_thread(
                    partial(
                        ctx.backtester.run_backtest,
                        symbol,
                        strategy,
                        run_config,
                        candles,
                        progress_cb=progress_cb,
                        cancel_cb=is_cancelled,
                    ),
                )

                if isinstance(results, dict) and results.get("cancelled"):
                    await _finish("cancelled")
                    return

                if isinstance(results, dict) and results.get("error"):
                    if is_sweep:
                        sweep_rows.append({
                            "label": sweep_label(run_config),
                            "config": run_config,
                            "error": results["error"],
                        })
                        continue
                    await _finish("error", message=results["error"])
                    return

                if not isinstance(results, dict):
                    await _finish("error", message="Invalid backtest response")
                    return

                row = {
                    "label": sweep_label(run_config),
                    "config": run_config,
                    "summary": results.get("summary") or {},
                    "total_pnl": results.get("total_pnl"),
                    "trade_count": results.get("trade_count"),
                }
                sweep_rows.append(row)

                pnl = float(results.get("total_pnl") or 0)
                if best_result is None or pnl > float(best_result.get("total_pnl") or -1e18):
                    best_result = results
                    best_config = run_config

        if best_result is None:
            await _finish("error", message="Sweep produced no valid runs")
            return

        enqueue_progress({"pct": 98, "phase": "save", "message": "Saving run…"})
        meta["strategy"] = strategy
        if oos_pct:
            meta["oos_pct"] = oos_pct
        if walk_forward and is_sweep:
            meta["walk_forward"] = True
            meta["train_pct"] = train_pct
        best_result["meta"] = meta
        if is_sweep and not (walk_forward and is_sweep):
            best_result["sweep"] = {
                "configs_tested": len(configs),
                "best_config": best_config,
                "results": sorted(
                    sweep_rows,
                    key=lambda r: float(r.get("total_pnl") or -1e18),
                    reverse=True,
                ),
            }

        from app.services.bots.backtest_store import save_backtest_run

        all_trades = best_result.get("trades") or []
        best_result["trades_total"] = len(all_trades)
        run_id = save_backtest_run(symbol, strategy, best_config or config, days, best_result)
        wire_results = {
            **best_result,
            "trades": all_trades[-100:],
            "run_id": run_id,
        }
        enqueue_progress({"pct": 100, "phase": "done", "message": "Complete"})
        await _finish("success", results=wire_results, run_id=run_id)
    finally:
        clear_job(ctx.websocket)
        await progress_queue.put(None)
        await drain_task


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
    ctx.bot_manager.load_bots_from_db()
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
    req = _parse_backtest_request(ctx.message)
    await _execute_backtest(ctx, **req)


@route(Action.RUN_BACKTEST_SWEEP, tags=["bots"])
async def run_backtest_sweep(ctx: RequestContext) -> None:
    req = _parse_backtest_request(ctx.message)
    sweep = req.pop("sweep", None) or {
        "trailing_stop_percent": ctx.message.get("trailing_stop_values") or [1, 2, 3],
        "take_profit_percent": ctx.message.get("take_profit_values") or [2, 3, 5],
    }
    req["sweep"] = sweep
    await _execute_backtest(ctx, **req)


@route(Action.CANCEL_BACKTEST, tags=["bots"])
async def cancel_backtest(ctx: RequestContext) -> None:
    job_id = ctx.message.get("job_id")
    if job_id:
        from app.services.bots.backtest_job_store import request_cancel_job
        if request_cancel_job(job_id):
            await send_order_result(ctx, {"status": "success", "message": "Backtest cancel requested"})
            return
    if cancel_job(ctx.websocket):
        await send_order_result(ctx, {"status": "success", "message": "Backtest cancel requested"})
    else:
        await send_order_result(ctx, {"status": "error", "message": "No active backtest to cancel"})
