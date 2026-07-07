import asyncio
import logging
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
    start_job_execution,
    update_job_progress,
)
from app.services.bots.backtest_perf import (
    backtest_tier_meta,
    heavy_backtest_label,
    parallel_worker_count,
)
from app.services.bots.backtest_sweep import expand_sweep_grid, is_sweep_request, sweep_label
from app.services.bots.backtest_costs import parse_cost_config
from app.services.bots.backtest_payload import trim_results_for_wire
from app.services.bots.strategies import normalize_strategy_name
from app.services.bots.backtest_walk_forward import (
    pick_best_config,
    row_objective_value,
    row_trade_count,
    sort_sweep_rows,
)
from app.services.events import channels
from app.services.events import publish as event_publish

logger = logging.getLogger(__name__)


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


async def _maybe_defer_backtest(ctx: RequestContext, req: dict) -> bool:
    """Queue slow runs in a background task; return True when deferred."""
    tier_meta = backtest_tier_meta(req)
    tier = tier_meta["tier"]
    if tier != "deferred":
        return False

    label = tier_meta.get("label") or heavy_backtest_label({
        **req,
        "config": req.get("config") or {},
    })
    client_key = str(id(ctx.websocket)) if ctx.websocket is not None else None
    job_req = {**req, "tier": tier, "estimated_sec": tier_meta.get("estimated_sec")}
    job_id = create_backtest_job(job_req, status="pending", client_key=client_key)
    start_job(ctx.websocket, job_id)
    progress = {
        "pct": 0,
        "phase": "queued",
        "message": f"Queued {label} backtest (~{tier_meta.get('estimated_sec')}s est.)…",
        "job_id": job_id,
        "tier": tier,
        "estimated_sec": tier_meta.get("estimated_sec"),
    }
    update_job_progress(job_id, progress)
    await send_backtest_progress(ctx, progress)

    async def _run_deferred() -> None:
        try:
            await _execute_backtest(ctx, job_id=job_id, **req)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("Deferred backtest %s failed", job_id)
            set_job_status(job_id, "failed", error=str(exc) or "Deferred backtest failed")
            try:
                await send_backtest_result(
                    ctx,
                    {
                        "status": "error",
                        "message": str(exc) or "Deferred backtest failed",
                        "job_id": job_id,
                    },
                )
            except Exception:
                pass

    asyncio.create_task(_run_deferred())
    return True


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
    else:
        start_job_execution(job_id)

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
            from app.services.bots.backtest_portfolio import (
                PortfolioBacktestConfig,
                format_portfolio_results,
                run_portfolio_backtest,
            )
            from app.services.bots.correlation import summarize_basket_correlation

            enqueue_progress({"pct": 5, "phase": "resolve", "message": f"Portfolio: {len(portfolio_symbols)} symbols…"})

            candles_by_symbol: dict[str, list] = {}
            skipped_resolve: list[dict] = []
            for sym in portfolio_symbols:
                if is_cancelled():
                    await _finish("cancelled")
                    return
                try:
                    c, _m = await asyncio.to_thread(
                        resolve_backtest_candles,
                        sym,
                        ctx.oms.feed,
                        days=days,
                        interval=interval,
                        timeframe=timeframe,
                    )
                    if oos_pct:
                        c = _apply_oos_window(c, _m, oos_pct)
                    candles_by_symbol[sym] = c or []
                except ValueError as exc:
                    candles_by_symbol[sym] = []
                    skipped_resolve.append({"symbol": sym, "reason": str(exc)})

            for sym, c in candles_by_symbol.items():
                if c and len(c) >= 50:
                    try:
                        ctx.backtester.cache_candles(sym, strategy, c, config)
                    except Exception:
                        pass

            slippage_bps, fee_bps = parse_cost_config(config)
            total_capital = float(config.get("allocation") or 10_000.0) * len(portfolio_symbols)
            sym_entries = [
                {"symbol": sym, "strategy": strategy, "config": config, "weight": 1.0}
                for sym in portfolio_symbols
            ]
            portfolio_cfg = PortfolioBacktestConfig(
                symbols=sym_entries,
                total_capital=total_capital,
                slippage_bps=slippage_bps,
                fee_bps=fee_bps,
            )

            skipped_symbols: list[dict] = list(skipped_resolve)

            def portfolio_progress(**kw) -> None:
                si = kw.get("symbol_index", 1)
                st = kw.get("symbol_total", 1)
                sym = kw.get("symbol", "")
                batch_skipped = kw.get("skipped") or []
                if batch_skipped:
                    skipped_symbols.extend(batch_skipped)
                skip_note = ""
                if skipped_symbols:
                    skip_note = f" · {len(skipped_symbols)} skipped"
                enqueue_progress({
                    "pct": min(5 + int((si / max(st, 1)) * 85), 90),
                    "phase": "portfolio",
                    "message": f"Portfolio {si}/{st}: {sym}…{skip_note}",
                    "symbol": sym,
                    "skipped_symbols": skipped_symbols[-8:],
                })

            portfolio_raw = await asyncio.to_thread(
                run_portfolio_backtest,
                ctx.backtester,
                portfolio_cfg,
                candles_by_symbol,
                progress_cb=portfolio_progress,
                cancel_cb=is_cancelled,
            )
            if portfolio_raw.get("cancelled"):
                await _finish("cancelled")
                return
            if portfolio_raw.get("error") and not portfolio_raw.get("per_symbol"):
                await _finish("error", message=portfolio_raw["error"])
                return

            corr_summary = await asyncio.to_thread(
                summarize_basket_correlation,
                portfolio_symbols,
                feed=ctx.oms.feed,
            )
            portfolio_result = format_portfolio_results(
                portfolio_raw,
                correlation_summary=corr_summary,
            )

            if (
                portfolio_result.get("symbols_tested", 0) == 0
                and portfolio_result.get("symbols_failed", 0) > 0
            ):
                portfolio_result["error"] = "All portfolio symbols skipped or failed"
                await _finish("error", message=portfolio_result["error"], results=portfolio_result)
                return

            meta["strategy"] = strategy
            meta["portfolio"] = True
            meta["portfolio_symbols"] = portfolio_symbols
            meta["live_parity"] = config.get("live_parity", config.get("sim_mode", "live_aligned") == "live_aligned")
            meta["config"] = {
                "direction_mode": str(config.get("direction_mode") or "LONG_ONLY").upper(),
                "sim_mode": str(config.get("sim_mode") or "live_aligned").lower(),
                "live_parity": meta["live_parity"],
                "allocation": config.get("allocation"),
            }
            from app.services.bots.backtest_provenance import repo_git_revision

            git_rev = repo_git_revision()
            if git_rev:
                meta["git_revision"] = git_rev
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
            wire = trim_results_for_wire(portfolio_result)
            wire["run_id"] = run_id
            await _finish("success", results=wire, run_id=run_id)
            return

        configs = expand_sweep_grid(config, sweep) if sweep else [config]
        if not configs:
            configs = [config]
        is_sweep = is_sweep_request(sweep, configs)

        if walk_forward and not is_sweep:
            await _finish("error", message="Walk-forward requires a parameter sweep grid")
            return

        # Walk-forward already performs its own train/test (out-of-sample) split, so
        # applying the OOS window first would double-trim the data. Only honor
        # oos_pct for non-walk-forward runs.
        if not (walk_forward and is_sweep):
            candles = _apply_oos_window(candles, meta, oos_pct)
        bar_count = len(candles or [])

        from app.services.bots.backtest_regime_filter import filter_candles_by_regime, parse_optimize_regime
        optimize_regime = parse_optimize_regime(sweep, msg=request_payload)
        if optimize_regime != "all":
            candles, regime_meta = filter_candles_by_regime(candles, optimize_regime)
            meta = {**(meta or {}), "optimize_regime": regime_meta}
            bar_count = len(candles or [])
            if bar_count < 100:
                await _finish(
                    "error",
                    message=(
                        f"Regime filter '{optimize_regime}' left only {bar_count} bars "
                        f"(need ~100+). Try more days or 'all' regimes."
                    ),
                )
                return

        if walk_forward and is_sweep:
            from app.services.bots.backtest_purged_cv import (
                parse_wf_validation_options,
                split_final_holdout,
            )
            from app.services.bots.backtest_walk_forward import (
                run_final_holdout_validation,
                run_walk_forward,
            )
            from app.services.bots.backtester import thread_local_backtest_runner

            wf_options = parse_wf_validation_options(
                sweep,
                msg=request_payload,
                base_config=config,
                timeframe=timeframe,
            )
            holdout_candles: list[dict] = []
            holdout_meta: dict = {}
            if wf_options.get("final_holdout_pct"):
                candles, holdout_candles, meta, holdout_meta = split_final_holdout(
                    candles,
                    meta,
                    wf_options["final_holdout_pct"],
                )
                wf_options = {
                    **wf_options,
                    "holdout_bars": len(holdout_candles),
                    "holdout_pct": wf_options["final_holdout_pct"],
                }

            bar_count = len(candles or [])
            from app.services.bots.backtest_indicator_cache import unique_indicator_configs

            try:
                for warm_cfg in unique_indicator_configs(strategy, configs):
                    ctx.backtester.cache_candles(symbol, strategy, candles, warm_cfg)
            except Exception:
                pass

            wf_mode = str(wf_options.get("wf_mode") or "rolling")
            if wf_mode == "anchored":
                wf_label = f"Anchored walk-forward ({rolling_folds} folds)"
            elif rolling_folds > 1:
                wf_label = f"Rolling walk-forward ({rolling_folds} folds)"
            else:
                wf_label = f"Walk-forward: optimizing on {int(train_pct)}% train window"
            if wf_options.get("purged_splits"):
                wf_label += " (purged)"
            if holdout_candles:
                wf_label += f" · {wf_options.get('holdout_pct')}% holdout reserved"
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

            wf_runner = thread_local_backtest_runner(ctx.backtester)
            best_result = await asyncio.to_thread(
                partial(
                    run_walk_forward,
                    run_backtest=wf_runner,
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
                    sweep=sweep,
                    wf_options=wf_options,
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

            if holdout_candles and best_result:
                enqueue_progress({
                    "pct": 97,
                    "phase": "sweep",
                    "message": "Final holdout validation (never optimized)…",
                    "bars": len(holdout_candles),
                })
                holdout_result = await asyncio.to_thread(
                    partial(
                        run_final_holdout_validation,
                        run_backtest=wf_runner,
                        symbol=symbol,
                        strategy=strategy,
                        config=best_config,
                        optimization_candles=candles,
                        holdout_candles=holdout_candles,
                        holdout_meta=holdout_meta,
                        min_trades=min_trades,
                        min_pnl=auto_deploy_min_oos_pnl,
                        cancel_cb=is_cancelled,
                    ),
                )
                if isinstance(holdout_result, dict) and holdout_result.get("cancelled"):
                    await _finish("cancelled")
                    return
                best_result["final_holdout"] = holdout_result
                if best_result.get("walk_forward"):
                    best_result["walk_forward"]["final_holdout"] = holdout_result

            if wf_options.get("pbo_audit") and sweep_rows and candles:
                from app.services.bots.backtest_pbo import run_pbo_audit

                enqueue_progress({
                    "pct": 98,
                    "phase": "sweep",
                    "message": "PBO / CSCV overfit audit…",
                })

                def _pbo_eval(cfg: dict, segment: list[dict]) -> dict:
                    return wf_runner(
                        symbol,
                        strategy,
                        cfg,
                        segment,
                        cancel_cb=is_cancelled,
                    )

                pbo_result = await asyncio.to_thread(
                    partial(
                        run_pbo_audit,
                        sweep_rows=sweep_rows,
                        candles=candles,
                        evaluate_fn=_pbo_eval,
                        objective=sweep_objective,
                        min_trades=min_trades,
                        top_k=wf_options.get("pbo_top_k", 8),
                        n_groups=wf_options.get("pbo_groups", 8),
                        cancel_cb=is_cancelled,
                    ),
                )
                if isinstance(pbo_result, dict) and pbo_result.get("cancelled"):
                    await _finish("cancelled")
                    return
                best_result["pbo_audit"] = pbo_result
                if best_result.get("walk_forward"):
                    best_result["walk_forward"]["pbo_audit"] = pbo_result
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
            from app.services.bots.backtest_bayesian import is_bayesian_sweep, run_bayesian_sweep
            from app.services.bots.backtest_sweep import _max_combos_for_mode
            from app.services.bots.backtest_sweep_enrich import enrich_sweep_results

            trial_count = (
                _max_combos_for_mode(sweep or {}, "bayesian")
                if is_bayesian_sweep(sweep)
                else len(configs)
            )
            enqueue_progress({
                "pct": 8,
                "phase": "indicators" if not is_sweep else "sweep",
                "message": (
                    f"Bayesian search: up to {trial_count} trials…"
                    if is_bayesian_sweep(sweep) and is_sweep
                    else f"Running {trial_count} configuration{'s' if trial_count != 1 else ''}…"
                ),
                "bars": bar_count,
                "total_runs": trial_count,
            })

            # Pre-compute indicator DataFrames per unique indicator fingerprint (Tier 5)
            if is_sweep:
                from app.services.bots.backtest_indicator_cache import unique_indicator_configs

                try:
                    warm_configs = unique_indicator_configs(strategy, configs) if configs else [config]
                    for warm_cfg in warm_configs:
                        ctx.backtester.cache_candles(symbol, strategy, candles, warm_cfg)
                except Exception:
                    pass  # fallback: each run computes its own

            from app.services.bots.backtest_trial_budget import TrialBudgetTracker, resolve_trial_budget

            trial_budget = TrialBudgetTracker(sweep)
            budget_meta = resolve_trial_budget(sweep)

            sweep_rows = []
            best_result = None
            best_config = None

            portfolio_sweep = bool((sweep or {}).get("portfolio_sweep")) and portfolio_symbols and len(portfolio_symbols) > 1
            portfolio_sweep_complete = False

            if is_sweep and portfolio_sweep and not is_bayesian_sweep(sweep):
                from app.services.bots.backtest_portfolio_sweep import (
                    portfolio_sweep_row,
                    rank_portfolio_sweep_rows,
                )

                enqueue_progress({
                    "pct": 8,
                    "phase": "resolve",
                    "message": f"Portfolio sweep: loading {len(portfolio_symbols)} symbols…",
                })
                candles_by_sym: dict[str, list] = {}
                for sym in portfolio_symbols:
                    if is_cancelled():
                        await _finish("cancelled")
                        return
                    try:
                        c, _m = await asyncio.to_thread(
                            resolve_backtest_candles,
                            sym,
                            ctx.oms.feed,
                            days=days,
                            interval=interval,
                            timeframe=timeframe,
                        )
                        if optimize_regime != "all":
                            c, _ = filter_candles_by_regime(c, optimize_regime)
                        candles_by_sym[sym] = c or []
                    except ValueError:
                        candles_by_sym[sym] = []

                for run_idx, run_config in enumerate(configs):
                    if is_cancelled():
                        await _finish("cancelled")
                        return
                    if trial_budget.should_stop():
                        break
                    sym_rows = []
                    for sym in portfolio_symbols:
                        sym_candles = candles_by_sym.get(sym) or []
                        if len(sym_candles) < 50:
                            sym_rows.append({"symbol": sym, "error": "Insufficient bars"})
                            continue
                        res = ctx.backtester.run_backtest(
                            sym, strategy, run_config, sym_candles, cancel_cb=is_cancelled,
                        )
                        if res.get("cancelled"):
                            await _finish("cancelled")
                            return
                        sym_rows.append({
                            "symbol": sym,
                            "summary": res.get("summary") or {},
                            "total_pnl": res.get("total_pnl"),
                            "trade_count": res.get("trade_count"),
                            "error": res.get("error"),
                        })
                    row = portfolio_sweep_row(
                        run_config, sym_rows, objective=sweep_objective, label_fn=sweep_label,
                    )
                    sweep_rows.append(row)
                    trial_budget.record_trial()
                    enqueue_progress({
                        "pct": min(10 + int((run_idx + 1) / max(len(configs), 1) * 80), 90),
                        "phase": "sweep",
                        "message": f"Portfolio sweep {run_idx + 1}/{len(configs)}…",
                        "run": run_idx + 1,
                        "total_runs": len(configs),
                    })

                ranked = rank_portfolio_sweep_rows(sweep_rows, objective=sweep_objective, min_trades=min_trades)
                if ranked:
                    best_config = ranked[0].get("config") or config
                    best_result = await asyncio.to_thread(
                        ctx.backtester.run_backtest,
                        symbol,
                        strategy,
                        best_config,
                        candles,
                        cancel_cb=is_cancelled,
                    )
                    if isinstance(best_result, dict) and not best_result.get("error"):
                        sweep_block = enrich_sweep_results(
                            ranked,
                            sweep=sweep,
                            base_config=config,
                            objective=sweep_objective,
                            min_trades=min_trades,
                            trial_budget_meta={**budget_meta, **trial_budget.to_meta()},
                        )
                        best_result["sweep"] = sweep_block
                        best_result["portfolio_sweep"] = True
                        best_result["meta"] = {
                            **(best_result.get("meta") or {}),
                            **meta,
                            "portfolio": True,
                            "portfolio_symbols": portfolio_symbols,
                        }
                        portfolio_sweep_complete = True
                elif sweep_rows:
                    await _finish("error", message="Portfolio sweep produced no valid runs")
                    return
                else:
                    await _finish("error", message="Portfolio sweep produced no results")
                    return
            elif is_sweep and portfolio_sweep and is_bayesian_sweep(sweep):
                await _finish("error", message="Portfolio sweep does not support Bayesian mode yet — use grid/random")
                return

            if not portfolio_sweep_complete:
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
                            trial_budget.record_trial()
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
                    trial_budget.record_trial()
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

                if is_bayesian_sweep(sweep) and is_sweep:
                    def _bayes_evaluate(run_cfg: dict) -> dict:
                        if is_cancelled():
                            return {"cancelled": True}
                        return ctx.backtester.run_backtest(
                            symbol,
                            strategy,
                            run_cfg,
                            candles,
                            cancel_cb=is_cancelled,
                        )

                    def _bayes_progress(done: int, total: int) -> None:
                        enqueue_progress({
                            "pct": min(10 + int((done / max(total, 1)) * 85), 95),
                            "phase": "sweep",
                            "message": f"Bayesian trial {done}/{total}…",
                            "run": done,
                            "total_runs": total,
                        })

                    sweep_rows, bayesian_meta = await asyncio.to_thread(
                        partial(
                            run_bayesian_sweep,
                            base_config=config,
                            sweep=sweep,
                            evaluate_fn=_bayes_evaluate,
                            objective=sweep_objective,
                            min_trades=min_trades,
                            progress_cb=_bayes_progress,
                            cancel_cb=is_cancelled,
                            budget_tracker=trial_budget,
                        ),
                    )
                    if is_cancelled():
                        await _finish("cancelled")
                        return
                    if not sweep_rows:
                        await _finish("error", message="Bayesian sweep produced no valid runs")
                        return
                    sweep_block = enrich_sweep_results(
                        sweep_rows,
                        sweep=sweep,
                        base_config=config,
                        objective=sweep_objective,
                        min_trades=min_trades,
                        bayesian_meta=bayesian_meta,
                        trial_budget_meta={**budget_meta, **trial_budget.to_meta()},
                    )
                    best_config = sweep_block.get("stable_config") or sweep_block.get("best_config") or config
                    best_result = await asyncio.to_thread(
                        partial(
                            ctx.backtester.run_backtest,
                            symbol,
                            strategy,
                            best_config,
                            candles,
                            cancel_cb=is_cancelled,
                        ),
                    )
                    if isinstance(best_result, dict) and best_result.get("cancelled"):
                        await _finish("cancelled")
                        return
                    if isinstance(best_result, dict) and best_result.get("error"):
                        await _finish("error", message=best_result["error"])
                        return
                    best_result["sweep"] = sweep_block
                else:
                    use_parallel = is_sweep and len(configs) > 1
                    if use_parallel:
                        workers = parallel_worker_count(len(configs))
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
                            if trial_budget.should_stop():
                                break
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
                            if trial_budget.should_stop():
                                break

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
                                    trial_budget.record_trial()
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
        meta["live_parity"] = config.get(
            "live_parity",
            config.get("sim_mode", "live_aligned") == "live_aligned",
        )
        meta["config"] = {
            "direction_mode": str(config.get("direction_mode") or "LONG_ONLY").upper(),
            "sim_mode": str(config.get("sim_mode") or "live_aligned").lower(),
            "live_parity": meta["live_parity"],
            "allocation": config.get("allocation"),
        }
        tier_meta = backtest_tier_meta({
            **request_payload,
            "reasoning": reasoning,
            "config": config,
        })
        meta["job_tier"] = tier_meta.get("tier")
        meta["estimated_sec"] = tier_meta.get("estimated_sec")
        from app.services.bots.backtest_provenance import repo_git_revision

        git_rev = repo_git_revision()
        if git_rev:
            meta["git_revision"] = git_rev
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
            from app.services.bots.backtest_sweep_enrich import enrich_sweep_results

            if not best_result.get("sweep"):
                sweep_block = enrich_sweep_results(
                    sweep_rows,
                    sweep=sweep,
                    base_config=config,
                    objective=sweep_objective,
                    min_trades=min_trades,
                    trial_budget_meta={**budget_meta, **trial_budget.to_meta()},
                )
                best_result["sweep"] = sweep_block
            picked_cfg = (
                best_result["sweep"].get("stable_config")
                or best_result["sweep"].get("best_config")
            )
            if picked_cfg:
                best_config = picked_cfg
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
                    results=best_result["sweep"].get("results") or sweep_rows,
                    best_config=best_config,
                )
            except Exception:
                pass

        from app.services.bots.backtest_store import save_backtest_run

        all_trades = best_result.get("trades") or []
        best_result["trades_total"] = len(all_trades)
        run_id = save_backtest_run(symbol, strategy, best_config or config, days, best_result)
        wire_results = trim_results_for_wire({
            **best_result,
            "trades": all_trades[-100:],
            "run_id": run_id,
        })
        wire_results["run_id"] = run_id

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
    except Exception as exc:
        logger.exception("Backtest job %s failed", job_id)
        await _finish("error", message=str(exc) or "Backtest failed")
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
    config = dict(msg.get("config") or {})
    execution_mode = msg.get("execution_mode", "BAR_CLOSE")
    force_deploy = bool(msg.get("force_deploy"))
    backtest_fingerprint = msg.get("backtest_fingerprint") or config.get("backtest_fingerprint")
    run_id = config.get("backtest_run_id")

    from app.config import (
        DEPLOY_GATE_ENABLED,
        DEPLOY_MAX_DRAWDOWN_WARN_PCT,
        DEPLOY_MIN_OOS_PNL,
        DEPLOY_MIN_OOS_TRADES,
        DEPLOY_MIN_STABILITY_SCORE,
    )
    from app.services.bots.backtest_store import get_backtest_run
    from app.services.bots.deploy_gate import (
        config_fingerprint,
        enrich_deploy_config,
        evaluate_deploy_gate,
    )

    gate = None
    run = None
    if DEPLOY_GATE_ENABLED and run_id and not force_deploy:
        run = get_backtest_run(str(run_id))
        if not run:
            await send_order_result(ctx, {
                "status": "error",
                "message": f"Backtest run {run_id} not found",
            })
            return
        gate = evaluate_deploy_gate(
            run.get("results"),
            symbol=str(symbol or "").upper() or None,
            deploy_fingerprint=backtest_fingerprint,
            run_config=run.get("config"),
            run_days=run.get("days"),
            run_timeframe=timeframe,
            min_trades=DEPLOY_MIN_OOS_TRADES,
            min_pnl=DEPLOY_MIN_OOS_PNL,
            min_stability_score=DEPLOY_MIN_STABILITY_SCORE,
            max_drawdown_warn_pct=DEPLOY_MAX_DRAWDOWN_WARN_PCT,
        )
        if gate.get("blocking") and not gate.get("passed"):
            await send_order_result(ctx, {
                "status": "error",
                "message": gate.get("block_reason") or "Deploy gate blocked",
                "deploy_gate": gate,
            })
            return

    if not backtest_fingerprint and run:
        backtest_fingerprint = config_fingerprint(
            symbol=run.get("symbol"),
            strategy=run.get("strategy"),
            days=run.get("days"),
            timeframe=timeframe,
            config=config,
        )

    config = enrich_deploy_config(
        config,
        run_id=str(run_id) if run_id else None,
        fingerprint=backtest_fingerprint,
        gate=gate,
    )

    try:
        bot_id = await ctx.bot_manager.create_bot(
            strategy, symbol, timeframe, allocation, config, execution_mode=execution_mode
        )
        await send_order_result(ctx, {
            "status": "success",
            "message": f"Bot {bot_id} created successfully",
            "deploy_gate": gate,
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
    if await _maybe_defer_backtest(ctx, req):
        return
    await _execute_backtest(ctx, **req)


@route(Action.RUN_BACKTEST_SWEEP, tags=["bots"])
async def run_backtest_sweep(ctx: RequestContext) -> None:
    req = _parse_backtest_request(ctx.message)
    sweep = req.pop("sweep", None) or {
        "trailing_stop_percent": ctx.message.get("trailing_stop_values") or [1, 2, 3],
        "take_profit_percent": ctx.message.get("take_profit_values") or [2, 3, 5],
    }
    req["sweep"] = sweep
    if await _maybe_defer_backtest(ctx, req):
        return
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
