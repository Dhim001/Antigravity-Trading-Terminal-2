import logging
from app.celery_app import celery_app
from app.services.bots.backtest_bayesian import run_bayesian_sweep
from app.services.bots.backtest import run_backtest

logger = logging.getLogger(__name__)

@celery_app.task(bind=True)
def async_run_bayesian_sweep(self, base_config: dict, sweep_config: dict, objective: str = "total_pnl", min_trades: int = 0):
    """
    Run the Bayesian sweep in the background using Celery.
    """
    logger.info(f"Starting Celery task for Bayesian sweep: {self.request.id}")
    
    # Re-hydrate evaluate_fn because we can't serialize functions.
    # The run_backtest function will be imported and used locally in the worker.
    def evaluate_fn(cfg: dict) -> dict:
        return run_backtest(cfg)
        
    try:
        rows, meta = run_bayesian_sweep(
            base_config=base_config,
            sweep=sweep_config,
            evaluate_fn=evaluate_fn,
            objective=objective,
            min_trades=min_trades,
            progress_cb=None,
            cancel_cb=None,
        )
        return {"rows": rows, "meta": meta}
    except Exception as exc:
        logger.exception("Failed running bayesian sweep in Celery")
        return {"error": str(exc)}
