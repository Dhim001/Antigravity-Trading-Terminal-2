"""Load optional user strategy plugins from backend/strategies/.

Supports two formats:
  1. Legacy: module exports ``evaluate(row, config) -> dict``
  2. SDK v2: module exports a class inheriting ``StrategyV2``
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from types import ModuleType

from app.config import ALLOW_CUSTOM_STRATEGIES, BASE_DIR
from app.services.bots.strategies import BaseStrategy

logger = logging.getLogger(__name__)

STRATEGIES_DIR = os.path.join(BASE_DIR, "strategies")
_loaded: dict[str, type[BaseStrategy] | object] = {}
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
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        logger.error("Failed to load custom strategy module '%s': %s", module_name, exc)
        return None
    return mod


def _find_v2_class(mod: ModuleType):
    """Find a StrategyV2 subclass in the module, if any."""
    try:
        from app.services.bots.strategy_sdk import StrategyV2

        for _name, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, StrategyV2) and obj is not StrategyV2:
                return obj
    except ImportError:
        pass
    return None


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


def get_custom_strategy(module_name: str, config: dict, *, bot_id: str = "") -> BaseStrategy | None:
    if not ALLOW_CUSTOM_STRATEGIES:
        logger.warning("Custom strategies disabled (ALLOW_CUSTOM_STRATEGIES=false)")
        return None

    cache_key = module_name
    if cache_key not in _loaded:
        mod = _load_module(module_name)
        if not mod:
            return None

        # Check for SDK v2 class first
        v2_cls = _find_v2_class(mod)
        if v2_cls is not None:
            _loaded[cache_key] = v2_cls
            logger.info("Loaded StrategyV2 class '%s' from %s", v2_cls.__name__, module_name)
        elif callable(getattr(mod, "evaluate", None)):
            _loaded[cache_key] = mod.evaluate
            logger.info("Loaded legacy evaluate() from %s", module_name)
        else:
            logger.warning("Module %s has no StrategyV2 class or evaluate() function", module_name)
            return None

    entry = _loaded[cache_key]

    # V2 class → wrap in StrategyV2Adapter
    if isinstance(entry, type):
        try:
            from app.services.bots.strategy_sdk import StrategyV2, StrategyV2Adapter

            if issubclass(entry, StrategyV2):
                return StrategyV2Adapter(config, entry, bot_id=bot_id)
        except ImportError:
            pass

    # Legacy evaluate function
    if callable(entry):
        return CustomStrategyAdapter(config, entry)

    return None

