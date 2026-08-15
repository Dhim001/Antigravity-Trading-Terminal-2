"""Sidecar process — runs deferred heavy backtest/optimizer jobs off the API GIL.

Started by the API lifespan (or manually): ``python -m app.services.bots.heavy_job_worker``.

Claims ``pending`` jobs from SQLite, executes ``_execute_backtest`` with a local
Backtester + candle feed, and heartbeats via the shared job store so the API
process stays responsive under long ML/RL sweeps.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from typing import Any

logger = logging.getLogger(__name__)

_STOP = False
_sidecar_proc: subprocess.Popen | None = None

# Health/observability state for /health.
_last_claim_at: float | None = None
_recovered_failed_total: int = 0


def sidecar_health_snapshot() -> dict[str, Any]:
    """Sidecar liveness snapshot for /health.

    Reports whether the sidecar subprocess is enabled/alive, its PID, and the
    last job-claim time so a dead or stalled worker is visible rather than a
    silently-growing pending queue.
    """
    enabled = sidecar_enabled()
    pid: int | None = None
    alive = False
    if _sidecar_proc is not None:
        alive = _sidecar_proc.poll() is None
        pid = _sidecar_proc.pid if alive else _sidecar_proc.pid
    return {
        "enabled": enabled,
        "alive": alive,
        "pid": pid,
        "last_claim_at": _last_claim_at,
        "recovered_failed_total": _recovered_failed_total,
    }


def sidecar_enabled() -> bool:
    return os.environ.get("BACKTEST_HEAVY_SIDECAR", "1").strip().lower() in (
        "1", "true", "yes", "on",
    )


def sidecar_claims_all() -> bool:
    return os.environ.get("BACKTEST_SIDECAR_ALL", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def request_is_heavy(req: dict | None) -> bool:
    """True for ML / RL / ensemble strategies that should leave the API process."""
    strat = str((req or {}).get("strategy") or "")
    try:
        from app.services.bots.ml_walk_forward_validator import (
            is_ensemble_strategy,
            is_ml_strategy,
        )

        if is_ml_strategy(strat) or is_ensemble_strategy(strat):
            return True
    except Exception:
        pass
    # RL agents share the same GIL / torch hazard as ML.
    upper = strat.upper()
    if "RL_" in upper or upper.endswith("_RL") or "PPO" in upper:
        return True
    return False


def job_is_heavy(job: dict | None) -> bool:
    return request_is_heavy((job or {}).get("request") or {})


def api_worker_should_claim(job: dict) -> bool:
    """Filter for the in-process API backtest worker when sidecar is enabled."""
    if not sidecar_enabled():
        return True
    if sidecar_claims_all():
        return False
    return not job_is_heavy(job)


def sidecar_should_claim(job: dict) -> bool:
    if sidecar_claims_all():
        return True
    return job_is_heavy(job)


def defer_to_sidecar_only(req: dict | None) -> bool:
    """When True, API must enqueue pending only (no asyncio.create_task)."""
    if not sidecar_enabled():
        return False
    if sidecar_claims_all():
        return True
    return request_is_heavy(req)


def _build_context():
    from app.api.context import RequestContext
    from app.services.bots.backtester import BacktesterService
    from app.services.bots.screener import MarketScreenerService
    from app.services.candle_feed_stub import CandleFeedStub
    from app.services.sim_oms import SimulatedOMSService
    from app.websocket.connection_manager import ConnectionManager

    feed = CandleFeedStub()
    oms = SimulatedOMSService(feed)
    # Same screener as runtime.create_bot_stack — BacktesterService calls process_candles.
    screener = MarketScreenerService()
    backtester = BacktesterService(screener)
    manager = ConnectionManager()

    class _BotManagerStub:
        def get_account_balance(self, symbol=None):
            return 100_000.0

        async def create_bot(self, *args, **kwargs):
            logger.warning("Sidecar auto-deploy skipped (no live BotManager)")
            return None

    return RequestContext(
        websocket=None,
        manager=manager,
        oms=oms,
        bot_manager=_BotManagerStub(),
        backtester=backtester,
        chart_analyst=None,
        message={},
        action="run_backtest",
    ), feed, oms


async def _run_one(ctx, job: dict) -> None:
    from app.api.handlers.bots import _execute_backtest
    from app.services.bots.backtest_job_store import update_job_progress

    req = job.get("request") or {}
    job_id = job["id"]
    update_job_progress(job_id, {
        "pct": 1,
        "phase": "recover",
        "message": f"Sidecar worker pid={os.getpid()} claimed job…",
        "worker_pid": os.getpid(),
    })
    sweep = req.get("sweep")
    sweep_objective = req.get("sweep_objective") or (sweep or {}).get("sweep_objective") or "total_pnl"
    min_trades = req.get("min_trades")
    if min_trades is None and isinstance(sweep, dict):
        min_trades = sweep.get("min_trades")
    try:
        min_trades = max(0, int(min_trades if min_trades is not None else 0))
    except (TypeError, ValueError):
        min_trades = 0

    ctx.message = req
    await _execute_backtest(
        ctx,
        job_id=job_id,
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


async def worker_loop() -> None:
    from app.database import init_db
    from app.services.bots.backtest_job_store import (
        claim_next_pending_job,
        fail_stale_pending_jobs,
        recover_dead_worker_jobs,
        recover_stale_running_jobs,
    )

    init_db()
    recovered = recover_stale_running_jobs()
    if recovered:
        logger.info("Sidecar recovered %s interrupted job(s)", recovered)
    abandoned = fail_stale_pending_jobs(max_age_hours=6.0)
    if abandoned:
        logger.warning("Sidecar failed %s stale pending job(s)", abandoned)

    ctx, feed, oms = _build_context()
    if hasattr(feed, "start"):
        await feed.start()
    if hasattr(oms, "initialize"):
        await oms.initialize()

    logger.info(
        "Heavy job sidecar started pid=%s claims_all=%s",
        os.getpid(),
        sidecar_claims_all(),
    )

    _dead_check_at = 0.0
    global _last_claim_at, _recovered_failed_total
    while not _STOP:
        try:
            now = time.monotonic()
            if now - _dead_check_at >= 15.0:
                _dead_check_at = now
                dead = await asyncio.to_thread(recover_dead_worker_jobs)
                if dead:
                    _recovered_failed_total += dead
                    logger.warning("Sidecar re-queued %s dead-worker job(s)", dead)
            job = await asyncio.to_thread(
                claim_next_pending_job,
                accept=sidecar_should_claim,
            )
            if not job:
                await asyncio.sleep(2.0)
                continue
            _last_claim_at = time.time()
            logger.info(
                "Sidecar executing job %s (%s)",
                job["id"],
                (job.get("request") or {}).get("strategy"),
            )
            await _run_one(ctx, job)
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Sidecar worker error")
            await asyncio.sleep(5.0)


def _handle_signal(signum, frame):  # noqa: ARG001
    global _STOP
    _STOP = True


def spawn_sidecar_subprocess() -> subprocess.Popen | None:
    """Launch sidecar from the API process; returns Popen or None if disabled/failed."""
    global _sidecar_proc
    if not sidecar_enabled():
        return None
    if _sidecar_proc is not None and _sidecar_proc.poll() is None:
        return _sidecar_proc
    env = os.environ.copy()
    # Child is the worker; avoid nested spawn if it somehow imports server.
    env["BACKTEST_HEAVY_SIDECAR_CHILD"] = "1"
    cmd = [sys.executable, "-m", "app.services.bots.heavy_job_worker"]
    try:
        kwargs: dict[str, Any] = {
            "env": env,
            "stdout": None,
            "stderr": None,
        }
        if sys.platform == "win32":
            # Detach from console Ctrl+C of parent where possible; still kill on stop.
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        _sidecar_proc = subprocess.Popen(cmd, **kwargs)
        logger.info("Spawned heavy-job sidecar pid=%s", _sidecar_proc.pid)
        return _sidecar_proc
    except Exception:
        logger.exception("Failed to spawn heavy-job sidecar")
        _sidecar_proc = None
        return None


def stop_sidecar_subprocess(timeout: float = 5.0) -> None:
    global _sidecar_proc
    proc = _sidecar_proc
    _sidecar_proc = None
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
    except Exception:
        logger.debug("Sidecar shutdown error", exc_info=True)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [heavy-job-worker] %(message)s",
    )
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except Exception:
            pass
    try:
        asyncio.run(worker_loop())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
