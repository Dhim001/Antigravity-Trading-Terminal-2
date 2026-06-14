"""Load optional user strategy plugins from backend/strategies/."""

from __future__ import annotations

import importlib.util
import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from types import ModuleType

from app.config import ALLOW_CUSTOM_STRATEGIES, BASE_DIR
from app.services.bots.strategies import BaseStrategy

logger = logging.getLogger(__name__)

STRATEGIES_DIR = os.path.join(BASE_DIR, "strategies")
_loaded: dict[str, type[BaseStrategy]] = {}
_CUSTOM_EVAL_TIMEOUT_SEC = float(os.environ.get("CUSTOM_STRATEGY_TIMEOUT_SEC", "2.0"))
_eval_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="custom-strat")


def _load_module(module_name: str) -> ModuleType | None:
    path = os.path.join(STRATEGIES_DIR, f"{module_name}.py")
    if not os.path.isfile(path):
        logger.warning("Custom strategy module not found: %s", path)
        return None
    spec = importlib.util.spec_from_file_location(f"custom_strategy.{module_name}", path)
    if not spec or not spec.loader:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CustomStrategyAdapter(BaseStrategy):
    """Wraps a user-defined evaluate(row, config) function."""

    def __init__(self, config: dict, evaluate_fn):
        super().__init__(config)
        self._evaluate_fn = evaluate_fn

    def evaluate(self, df_row) -> dict:
        try:
            fut = _eval_executor.submit(self._evaluate_fn, df_row, self.config)
            result = fut.result(timeout=_CUSTOM_EVAL_TIMEOUT_SEC)
            if isinstance(result, dict) and result.get("signal") in ("BUY", "SELL", "CLOSE", "NONE"):
                return result
        except FuturesTimeout:
            logger.error("Custom strategy timed out after %.1fs", _CUSTOM_EVAL_TIMEOUT_SEC)
        except Exception as exc:
            logger.error("Custom strategy error: %s", exc)
        return {"signal": "NONE"}


def get_custom_strategy(module_name: str, config: dict) -> BaseStrategy | None:
    if not ALLOW_CUSTOM_STRATEGIES:
        logger.warning("Custom strategies disabled (ALLOW_CUSTOM_STRATEGIES=false)")
        return None

    cache_key = module_name
    if cache_key not in _loaded:
        mod = _load_module(module_name)
        if not mod or not callable(getattr(mod, "evaluate", None)):
            return None
        _loaded[cache_key] = mod.evaluate

    return CustomStrategyAdapter(config, _loaded[cache_key])
