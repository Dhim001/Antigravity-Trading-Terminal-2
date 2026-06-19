"""Background worker — resumes pending backtest jobs after server restart."""

from __future__ import annotations

import asyncio
import logging

from app.api.context import RequestContext
from app.services.bots.backtest_job_store import claim_next_pending_job, recover_stale_running_jobs

logger = logging.getLogger(__name__)


async def backtest_job_worker_loop(state) -> None:
    """Poll for pending jobs and execute them (recovered after restart)."""
    recovered = recover_stale_running_jobs()
    if recovered:
        logger.info("Recovered %s interrupted backtest job(s) for resume", recovered)

    while True:
        try:
            job = await asyncio.to_thread(claim_next_pending_job)
            if not job:
                await asyncio.sleep(2.0)
                continue
            logger.info("Resuming backtest job %s", job["id"])
            await _run_recovered_job(state, job)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Backtest job worker error")
            await asyncio.sleep(5.0)


async def _run_recovered_job(state, job: dict) -> None:
    from app.api.handlers.bots import _execute_backtest

    req = job.get("request") or {}
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
        sweep=req.get("sweep"),
        walk_forward=bool(req.get("walk_forward")),
        train_pct=float(req.get("train_pct") or 70),
    )
