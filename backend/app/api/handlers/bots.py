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
from app.services.bots.strategies import normalize_strategy_name
from app.services.bots.backtest_walk_forward import (
    pick_best_config,
    row_objective_value,
    row_trade_count,
    sort_sweep_rows,
)
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
    rolling_folds = msg.get("rolling_folds")
    if rolling_folds is not None:
        try:
            rolling_folds = max(1, min(5, int(rolling_folds)))
        except (TypeError, ValueError):
            rolling_folds = 1
    else:
        rolling_folds = 1
    reasoning = bool(msg.get("reasoning"))
    llm_model = (msg.get("llm_model") or msg.get("model") or "").strip() or None
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

    sweep = msg.get("sweep")
    sweep_objective = msg.get("sweep_objective") or (sweep or {}).get("sweep_objective") or "total_pnl"
    from app.services.bots.backtest_walk_forward import VALID_SWEEP_OBJECTIVES
    if sweep_objective not in VALID_SWEEP_OBJECTIVES:
        sweep_objective = "total_pnl"
    min_trades = msg.get("min_trades")
    if min_trades is None and isinstance(sweep, dict):
        min_trades = sweep.get("min_trades")
    try:
        min_trades = max(0, int(min_trades if min_trades is not None else 0))
    except (TypeError, ValueError):
        min_trades = 0

    portfolio_symbols = msg.get("portfolio_symbols")
    if isinstance(portfolio_symbols, str):
        portfolio_symbols = [s.strip() for s in portfolio_symbols.split(",") if s.strip()]
    elif not isinstance(portfolio_symbols, list):
        portfolio_symbols = None

    auto_deploy = bool(msg.get("auto_deploy"))
    try:
        auto_deploy_allocation = float(msg.get("auto_deploy_allocation", msg.get("allocation", 1000)))
    except (TypeError, ValueError):
        auto_deploy_allocation = 1000.0
    try:
        auto_deploy_min_oos_pnl = float(msg.get("auto_deploy_min_oos_pnl", 0))
    except (TypeError, ValueError):
        auto_deploy_min_oos_pnl = 0.0
    try:
        auto_deploy_min_oos_trades = max(0, int(msg.get("auto_deploy_min_oos_trades", 1)))
    except (TypeError, ValueError):
        auto_deploy_min_oos_trades = 1
    auto_deploy_skip_existing = msg.get("auto_deploy_skip_existing")
    if auto_deploy_skip_existing is None:
        auto_deploy_skip_existing = True
    else:
        auto_deploy_skip_existing = bool(auto_deploy_skip_existing)

    return {
        "symbol": msg.get("symbol"),
        "strategy": msg.get("strategy"),
        "config": config,
        "days": days,
        "interval": msg.get("interval"),
        "timeframe": msg.get("timeframe", "1m"),
        "oos_pct": oos_pct,
        "walk_forward": walk_forward,
        "rolling_folds": rolling_folds,
        "train_pct": train_pct,
        "sweep": sweep,
        "sweep_objective": sweep_objective,
        "min_trades": min_trades,
        "reasoning": reasoning,
        "llm_model": llm_model,
        "portfolio_symbols": portfolio_symbols,
        "auto_deploy": auto_deploy,
        "auto_deploy_allocation": auto_deploy_allocation,
        "auto_deploy_min_oos_pnl": auto_deploy_min_oos_pnl,
        "auto_deploy_min_oos_trades": auto_deploy_min_oos_trades,
        "auto_deploy_skip_existing": auto_deploy_skip_existing,
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
    rolling_folds: int = 1,
    train_pct: float = 70.0,
    sweep_objective: str = "total_pnl",
    min_trades: int = 0,
    reasoning: bool = False,
    llm_model: str | None = None,
    portfolio_symbols: list[str] | None = None,
    auto_deploy: bool = False,
    auto_deploy_allocation: float = 1000.0,
    auto_deploy_min_oos_pnl: float = 0.0,
    auto_deploy_min_oos_trades: int = 1,
    auto_deploy_skip_existing: bool = True,
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
        "rolling_folds": rolling_folds,
        "train_pct": train_pct,
        "sweep_objective": sweep_objective,
        "min_trades": min_trades,
        "portfolio_symbols": portfolio_symbols,
        "auto_deploy": auto_deploy,
        "auto_deploy_allocation": auto_deploy_allocation,
        "auto_deploy_min_oos_pnl": auto_deploy_min_oos_pnl,
        "auto_deploy_min_oos_trades": auto_deploy_min_oos_trades,
        "auto_deploy_skip_existing": auto_deploy_skip_existing,
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
        from app.services.bots.risk_sizing import enrich_backtest_risk_config

        account_balance = None
        if getattr(ctx, "bot_manager", None):
            account_balance = ctx.bot_manager.get_account_balance()
        elif hasattr(ctx.oms, "get_account_data"):
            balances = ctx.oms.get_account_data().get("balances", {})
            usd = balances.get("USD", {}).get("balance")
            if usd is not None:
                account_balance = float(usd)
            else:
                account_balance = float(balances.get("USDT", {}).get("balance") or 0)
        config = enrich_backtest_risk_config(config, account_balance)

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

        # Multi-symbol portfolio backtest (no sweep / walk-forward)
        if portfolio_symbols and len(portfolio_symbols) > 1 and not sweep and not walk_forward:
            from app.services.bots.backtest_portfolio import run_portfolio_backtest

            enqueue_progress({"pct": 5, "phase": "resolve", "message": f"Portfolio: {len(portfolio_symbols)} symbols…"})

            def resolve_sym(sym: str):
                c, m = resolve_backtest_candles(
                    sym,
                    ctx.oms.feed,
                    days=days,
                    interval=interval,
                    timeframe=timeframe,
                )
                if oos_pct:
                    c = _apply_oos_window(c, m, oos_pct)
                return c, m

            def portfolio_progress(**kw) -> None:
                si = kw.get("symbol_index", 1)
                st = kw.get("symbol_total", 1)
                sym = kw.get("symbol", "")
                enqueue_progress({
                    "pct": min(5 + int((si / max(st, 1)) * 85), 90),
                    "phase": "portfolio",
                    "message": f"Portfolio {si}/{st}: {sym}…",
                    "symbol": sym,
                })

            portfolio_result = await asyncio.to_thread(
                partial(
                    run_portfolio_backtest,
                    run_backtest=ctx.backtester.run_backtest,
                    symbols=portfolio_symbols,
                    strategy=strategy,
                    config=config,
                    resolve_candles=resolve_sym,
                    progress_cb=portfolio_progress,
                    cancel_cb=is_cancelled,
                ),
            )
            if portfolio_result.get("cancelled"):
                await _finish("cancelled")
                return
            if portfolio_result.get("error") and not portfolio_result.get("portfolio"):
                await _finish("error", message=portfolio_result["error"])
                return
            meta["strategy"] = strategy
            meta["portfolio"] = True
            meta["portfolio_symbols"] = portfolio_symbols
            bot_id = str(config.get("backtest_bot_id") or config.get("_bot_id") or "").strip()
            if bot_id:
                meta["bot_id"] = bot_id
            portfolio_result["meta"] = meta
            from app.services.bots.backtest_store import save_backtest_run

            run_id = await asyncio.to_thread(
                save_backtest_run,
                portfolio_symbols[0],
                strategy,
                config,
                days,
                portfolio_result,
            )
            portfolio_result["run_id"] = run_id
            await _finish("success", results=portfolio_result, run_id=run_id)
            return

        configs = expand_sweep_grid(config, sweep) if sweep else [config]
        is_sweep = len(configs) > 1 or bool(sweep)

        if walk_forward and not is_sweep:
            await _finish("error", message="Walk-forward requires a parameter sweep grid")
            return

        # Walk-forward already performs its own train/test (out-of-sample) split, so
        # applying the OOS window first would double-trim the data. Only honor
        # oos_pct for non-walk-forward runs.
        if not (walk_forward and is_sweep):
            candles = _apply_oos_window(candles, meta, oos_pct)
        bar_count = len(candles or [])

        if walk_forward and is_sweep:
            from app.services.bots.backtest_walk_forward import run_walk_forward

            wf_label = (
                f"Rolling walk-forward ({rolling_folds} folds)"
                if rolling_folds > 1
                else f"Walk-forward: optimizing on {int(train_pct)}% train window"
            )
            enqueue_progress({
                "pct": 10,
                "phase": "sweep",
                "message": f"{wf_label}…",
                "bars": bar_count,
                "total_runs": len(configs),
            })

            def wf_progress(
                done: int,
                total: int,
                run_idx: int = 0,
                total_runs: int = 1,
                is_oos: bool = False,
                fold_idx: int = 0,
                total_folds: int = 1,
            ) -> None:
                frac = done / max(total, 1)
                if is_oos:
                    pct = 90 + frac * 7  # OOS validation occupies 90→97%
                    if total_folds > 1:
                        message = f"Fold {fold_idx + 1}/{total_folds} OOS: bar {done}/{total}…"
                    else:
                        message = f"Walk-forward OOS validation: bar {done}/{total}…"
                else:
                    run_span = 78 / max(total_runs, 1)  # in-sample sweep spans 10→88%
                    pct = 10 + run_idx * run_span + frac * run_span
                    if total_folds > 1:
                        message = (
                            f"Fold {fold_idx + 1}/{total_folds} train "
                            f"{run_idx + 1}/{total_runs}: bar {done}/{total}…"
                        )
                    else:
                        message = f"Walk-forward train {run_idx + 1}/{total_runs}: bar {done}/{total}…"
                enqueue_progress({
                    "pct": min(int(pct), 97),
                    "phase": "sweep",
                    "message": message,
                    "bar": done,
                    "bars": total,
                    "run": run_idx + 1,
                    "total_runs": total_runs,
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
                    rolling_folds=rolling_folds,
                    sweep_objective=sweep_objective,
                    min_trades=min_trades,
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
            try:
                from app.services.bots.optimization_store import save_optimization_run

                save_optimization_run(
                    symbol=symbol,
                    strategy=strategy,
                    objective=sweep_objective,
                    request={
                        **request_payload,
                        "sweep_objective": sweep_objective,
                        "min_trades": min_trades,
                        "walk_forward": True,
                        "rolling_folds": rolling_folds,
                    },
                    results=sweep_rows,
                    best_config=best_config,
                    walk_forward=best_result.get("walk_forward"),
                )
            except Exception:
                pass
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

            # Pre-compute indicator DataFrame once for sweep reuse
            if is_sweep and len(configs) > 1:
                try:
                    ctx.backtester.cache_candles(symbol, strategy, candles, config)
                except Exception:
                    pass  # fallback: each run computes its own

            sweep_rows = []
            best_result = None
            best_config = None

            def _run_config(run_idx: int, run_config: dict) -> tuple[int, dict, dict | None]:
                if is_cancelled():
                    return run_idx, run_config, {"cancelled": True}
                results = ctx.backtester.run_backtest(
                    symbol,
                    strategy,
                    run_config,
                    candles,
                    cancel_cb=is_cancelled,
                )
                return run_idx, run_config, results

            def _consume_result(run_idx: int, run_config: dict, results: dict | None) -> bool:
                nonlocal best_result, best_config
                if isinstance(results, dict) and results.get("cancelled"):
                    return False
                if isinstance(results, dict) and results.get("error"):
                    if is_sweep:
                        sweep_rows.append({
                            "label": sweep_label(run_config),
                            "config": run_config,
                            "error": results["error"],
                        })
                        return True
                    return False
                if not isinstance(results, dict):
                    return False
                summary = results.get("summary") or {}
                row = {
                    "label": sweep_label(run_config),
                    "config": run_config,
                    "summary": summary,
                    "total_pnl": results.get("total_pnl"),
                    "trade_count": results.get("trade_count"),
                }
                if summary.get("filter_rejects"):
                    row["filter_rejects"] = summary["filter_rejects"]
                    row["filter_rejects_total"] = summary.get("filter_rejects_total")
                sweep_rows.append(row)
                score = row_objective_value(row, sweep_objective)
                prev_score = (
                    row_objective_value(
                        {
                            "total_pnl": best_result.get("total_pnl"),
                            "summary": best_result.get("summary") or {},
                            "trade_count": best_result.get("trade_count"),
                        },
                        sweep_objective,
                    )
                    if best_result is not None
                    else -1e18
                )
                if (
                    best_result is None
                    or (
                        row_trade_count(row) >= min_trades
                        and score > prev_score
                    )
                ):
                    best_result = results
                    best_config = run_config
                return True

            use_parallel = is_sweep and len(configs) > 1
            if use_parallel:
                workers = min(4, len(configs))
                sem = asyncio.Semaphore(workers)
                completed = 0

                async def _run_one(run_idx: int, run_config: dict):
                    async with sem:
                        if is_cancelled():
                            return run_idx, run_config, {"cancelled": True}
                        return await asyncio.to_thread(_run_config, run_idx, run_config)

                for coro in asyncio.as_completed(
                    [_run_one(idx, cfg) for idx, cfg in enumerate(configs)]
                ):
                    if is_cancelled():
                        await _finish("cancelled")
                        return
                    run_idx, run_config, results = await coro
                    if isinstance(results, dict) and results.get("cancelled"):
                        await _finish("cancelled")
                        return
                    if isinstance(results, dict) and results.get("error") and not is_sweep:
                        await _finish("error", message=results["error"])
                        return
                    if not _consume_result(run_idx, run_config, results):
                        if not is_sweep:
                            err = results.get("error") if isinstance(results, dict) else "Invalid backtest response"
                            await _finish("error", message=err)
                            return
                    completed += 1
                    enqueue_progress({
                        "pct": min(10 + int((completed / len(configs)) * 85), 95),
                        "phase": "sweep",
                        "message": f"Sweep {completed}/{len(configs)} complete…",
                        "run": completed,
                        "total_runs": len(configs),
                    })
            else:
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

                    if not _consume_result(run_idx, run_config, results) and not is_sweep:
                        await _finish("error", message="Invalid backtest response")
                        return

        if best_result is None:
            ctx.backtester.clear_candle_cache()
            await _finish("error", message="Sweep produced no valid runs")
            return

        ctx.backtester.clear_candle_cache()

        if (
            not is_sweep
            and config.get("meta_label_walk_forward")
            and normalize_strategy_name(strategy) == "CHART_AGENT"
        ):
            from app.services.bots.meta_label_walk_forward import evaluate_meta_label_walk_forward

            wf_folds = int(config.get("meta_label_wf_folds") or 2)
            wf_train_pct = float(config.get("meta_label_wf_train_pct") or 70.0)

            def wf_progress(fold: int, total: int, message: str) -> None:
                pct = 90 + int((fold / max(total, 1)) * 6)
                enqueue_progress({
                    "pct": min(pct, 96),
                    "phase": "meta_label_wf",
                    "message": message,
                    "fold": fold,
                    "folds": total,
                })

            enqueue_progress({
                "pct": 90,
                "phase": "meta_label_wf",
                "message": "Meta-label walk-forward: training GBM on in-sample windows…",
            })
            try:
                from app.config import META_LABEL_MIN_TRAIN_SAMPLES

                wf_result = await asyncio.to_thread(
                    partial(
                        evaluate_meta_label_walk_forward,
                        ctx.backtester.run_backtest,
                        symbol,
                        strategy,
                        config,
                        candles,
                        meta=meta,
                        rolling_folds=wf_folds,
                        train_pct=wf_train_pct,
                        min_train_samples=int(
                            config.get("meta_label_min_train_samples") or META_LABEL_MIN_TRAIN_SAMPLES
                        ),
                        progress_cb=wf_progress,
                        cancel_cb=is_cancelled,
                    ),
                )
            except Exception as exc:
                wf_result = {"ok": False, "error": str(exc), "folds": []}

            if wf_result.get("error") == "cancelled":
                await _finish("cancelled")
                return

            best_result["meta_label_walk_forward"] = wf_result
            if isinstance(meta, dict):
                meta["meta_label_walk_forward"] = True
                meta["meta_label_wf_folds"] = wf_folds
                meta["meta_label_wf_train_pct"] = wf_train_pct

        if reasoning:
            from app.services.bots.backtest_reasoning import generate_backtest_reasoning

            enqueue_progress({"pct": 94, "phase": "reasoning", "message": "Generating trade explanations…"})

            def reasoning_progress(done: int, total: int, message: str) -> None:
                pct = 94 + int((done / max(total, 1)) * 4)
                enqueue_progress({"pct": min(pct, 98), "phase": "reasoning", "message": message})

            if walk_forward and is_sweep:
                run_kind = "walk_forward"
            elif is_sweep:
                run_kind = "sweep"
            else:
                run_kind = "single"

            reasoning_result = await generate_backtest_reasoning(
                best_result.get("trades") or [],
                symbol=symbol,
                strategy=strategy,
                model=llm_model,
                progress_cb=reasoning_progress,
                run_kind=run_kind,
                train_pct=train_pct if run_kind == "walk_forward" else None,
                configs_tested=len(configs) if is_sweep else None,
            )
            best_result["reasoning"] = reasoning_result
            if isinstance(meta, dict):
                meta["reasoning"] = True
                meta["reasoning_run_kind"] = run_kind
        elif isinstance(meta, dict):
            meta["reasoning"] = False

        enqueue_progress({"pct": 98, "phase": "save", "message": "Saving run…"})

        meta["strategy"] = strategy
        bot_id = str(config.get("backtest_bot_id") or config.get("_bot_id") or "").strip()
        if bot_id:
            meta["bot_id"] = bot_id
        if sweep_objective:
            meta["sweep_objective"] = sweep_objective
        if min_trades:
            meta["min_trades"] = min_trades
        if oos_pct and not (walk_forward and is_sweep):
            meta["oos_pct"] = oos_pct
        if walk_forward and is_sweep:
            meta["walk_forward"] = True
            meta["train_pct"] = train_pct
            meta["rolling_folds"] = rolling_folds
        best_result["meta"] = meta
        if is_sweep and not (walk_forward and is_sweep):
            ranked = sort_sweep_rows(sweep_rows, objective=sweep_objective, min_trades=min_trades)
            picked_cfg, _picked_row = pick_best_config(
                sweep_rows,
                objective=sweep_objective,
                min_trades=min_trades,
            )
            if picked_cfg:
                best_config = picked_cfg
            best_result["sweep"] = {
                "configs_tested": len(configs),
                "best_config": best_config,
                "objective": sweep_objective,
                "min_trades": min_trades,
                "results": ranked,
            }
            try:
                from app.services.bots.optimization_store import save_optimization_run

                save_optimization_run(
                    symbol=symbol,
                    strategy=strategy,
                    objective=sweep_objective,
                    request={
                        **request_payload,
                        "sweep_objective": sweep_objective,
                        "min_trades": min_trades,
                    },
                    results=ranked,
                    best_config=best_config,
                )
            except Exception:
                pass

        from app.services.bots.backtest_store import save_backtest_run

        all_trades = best_result.get("trades") or []
        best_result["trades_total"] = len(all_trades)
        run_id = save_backtest_run(symbol, strategy, best_config or config, days, best_result)
        wire_results = {
            **best_result,
            "trades": all_trades[-100:],
            "run_id": run_id,
        }

        if auto_deploy and walk_forward and is_sweep:
            from app.services.agent.pipeline import auto_deploy_from_walk_forward

            enqueue_progress({"pct": 99, "phase": "deploy", "message": "Auto-deploying bot from OOS validation…"})
            deploy_out = await auto_deploy_from_walk_forward(
                ctx.bot_manager,
                {**best_result, "run_id": run_id},
                symbol=symbol,
                strategy=strategy,
                timeframe=timeframe,
                allocation=auto_deploy_allocation,
                run_id=run_id,
                min_oos_pnl=auto_deploy_min_oos_pnl,
                min_oos_trades=auto_deploy_min_oos_trades,
                skip_existing=auto_deploy_skip_existing,
                base_config=config,
            )
            wire_results["auto_deploy"] = deploy_out
            if deploy_out.get("deployed"):
                await broadcast_bots_update(ctx)

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
