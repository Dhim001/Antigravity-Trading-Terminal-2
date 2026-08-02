"""Background worker — resumes pending backtest jobs after server restart."""

from __future__ import annotations

import asyncio
import logging

from app.api.context import RequestContext
from app.services.bots.backtest_job_store import (
    claim_next_pending_job,
    fail_stale_pending_jobs,
    recover_dead_worker_jobs,
    recover_stale_running_jobs,
)
from app.services.bots.heavy_job_worker import api_worker_should_claim, sidecar_enabled

logger = logging.getLogger(__name__)


async def backtest_job_worker_loop(state) -> None:
    """Poll for pending jobs and execute them (recovered after restart).

    When the heavy-job sidecar is enabled, this loop only claims light jobs
    (or none if BACKTEST_SIDECAR_ALL=1) so ML/RL work stays off the API GIL.
    """
    recovered = recover_stale_running_jobs()
    if recovered:
        logger.info("Recovered %s interrupted backtest job(s) for resume", recovered)
    abandoned = fail_stale_pending_jobs(max_age_hours=6.0)
    if abandoned:
        logger.warning("Failed %s stale pending backtest job(s) older than 6h", abandoned)

    _dead_check_at = 0.0
    while True:
        try:
            now = asyncio.get_running_loop().time()
            if now - _dead_check_at >= 15.0:
                _dead_check_at = now
                dead = await asyncio.to_thread(recover_dead_worker_jobs)
                if dead:
                    logger.warning("Re-queued %s job(s) with dead worker_pid", dead)
            accept = api_worker_should_claim if sidecar_enabled() else None
            job = await asyncio.to_thread(claim_next_pending_job, accept=accept)
            if not job:
                await asyncio.sleep(2.0)
                continue
            logger.info("Resuming backtest job %s", job["id"])
            await _run_recovered_job(state, job)
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Backtest job worker error")
            await asyncio.sleep(5.0)


async def _run_recovered_job(state, job: dict) -> None:
    from app.api.handlers.bots import _execute_backtest
    from app.services.bots.backtest_job_store import update_job_progress
    import os

    req = job.get("request") or {}
    sweep = req.get("sweep")
    sweep_objective = req.get("sweep_objective") or (sweep or {}).get("sweep_objective") or "total_pnl"
    min_trades = req.get("min_trades")
    if min_trades is None and isinstance(sweep, dict):
        min_trades = sweep.get("min_trades")
    try:
        min_trades = max(0, int(min_trades if min_trades is not None else 0))
    except (TypeError, ValueError):
        min_trades = 0

    update_job_progress(job["id"], {
        "pct": max(1, int(((job.get("progress") or {}).get("pct") or 1))),
        "phase": (job.get("progress") or {}).get("phase") or "recover",
        "message": f"API worker pid={os.getpid()} claimed job…",
        "worker_pid": os.getpid(),
    })

    ctx = RequestContext(
        websocket=None,
        manager=state.manager,
        oms=state.oms,
        bot_manager=state.bot_manager,
        backtester=state.backtester,
        chart_analyst=state.chart_analyst,
        message=req,
        action="run_backtest",
    )
    await _execute_backtest(
        ctx,
        job_id=job["id"],
        symbol=req.get("symbol"),
        strategy=req.get("strategy"),
        config=req.get("config") or {},
        days=req.get("days") or 7,
        interval=req.get("interval"),
        timeframe=req.get("timeframe", "1m"),
        oos_pct=req.get("oos_pct"),
        sweep=sweep,
        walk_forward=bool(req.get("walk_forward")),
        rolling_folds=int(req.get("rolling_folds") or 1),
        train_pct=float(req.get("train_pct") or 70),
        sweep_objective=sweep_objective,
        min_trades=min_trades,
        reasoning=bool(req.get("reasoning")),
        llm_model=(req.get("llm_model") or req.get("model") or "").strip() or None,
        portfolio_symbols=req.get("portfolio_symbols"),
        auto_deploy=bool(req.get("auto_deploy")),
        auto_deploy_allocation=float(req.get("auto_deploy_allocation") or req.get("allocation") or 1000),
        auto_deploy_min_oos_pnl=float(req.get("auto_deploy_min_oos_pnl") or 0),
        auto_deploy_min_oos_trades=int(req.get("auto_deploy_min_oos_trades") or 1),
        auto_deploy_skip_existing=bool(req.get("auto_deploy_skip_existing", True)),
    )
