"""Sub-minute tick strategies — separate from bar-close indicator bots."""

from __future__ import annotations


def merge_tick_config(strategy: str, config: dict | None) -> dict:
    cfg = dict(config or {})
    defaults = {
        "TICK_MOMENTUM": {
            "lookback_ticks": 20,
            "momentum_threshold_pct": 0.04,
            "tick_cooldown_sec": 10,
            "stop_loss_distance_pct": 0.15,
        },
        "TICK_MEAN_REVERT": {
            "lookback_ticks": 30,
            "zscore_entry": 1.8,
            "tick_cooldown_sec": 15,
            "stop_loss_distance_pct": 0.12,
        },
        "TICK_BREAKOUT": {
            "lookback_ticks": 50,
            "breakout_pct": 0.08,
            "tick_cooldown_sec": 20,
            "stop_loss_distance_pct": 0.2,
        },
    }
    merged = {**defaults.get(strategy.upper(), {}), **cfg}
    return merged


class BaseTickStrategy:
    def __init__(self, config: dict):
        self.config = config

    def evaluate(self, ctx, price: float) -> dict:
        return {"signal": "NONE"}


class TickMomentumStrategy(BaseTickStrategy):
    def evaluate(self, ctx, price: float) -> dict:
        cfg = self.config
        threshold = float(cfg.get("momentum_threshold_pct", 0.04))
        sl_pct = float(cfg.get("stop_loss_distance_pct", 0.15))
        if ctx.momentum_pct >= threshold:
            return {
                "signal": "BUY",
                "stop_loss_distance": price * (sl_pct / 100.0),
            }
        if ctx.momentum_pct <= -threshold:
            return {
                "signal": "SELL",
                "stop_loss_distance": price * (sl_pct / 100.0),
            }
        return {"signal": "NONE"}


class TickMeanRevertStrategy(BaseTickStrategy):
    def evaluate(self, ctx, price: float) -> dict:
        cfg = self.config
        z_entry = float(cfg.get("zscore_entry", 1.8))
        sl_pct = float(cfg.get("stop_loss_distance_pct", 0.12))
        if ctx.zscore <= -z_entry:
            return {
                "signal": "BUY",
                "stop_loss_distance": price * (sl_pct / 100.0),
            }
        if ctx.zscore >= z_entry:
            return {
                "signal": "SELL",
                "stop_loss_distance": price * (sl_pct / 100.0),
            }
        return {"signal": "NONE"}


class TickBreakoutStrategy(BaseTickStrategy):
    def evaluate(self, ctx, price: float) -> dict:
        cfg = self.config
        lookback = int(cfg.get("lookback_ticks", 50))
        breakout = float(cfg.get("breakout_pct", 0.08))
        sl_pct = float(cfg.get("stop_loss_distance_pct", 0.2))
        if len(ctx.prices) < 5:
            return {"signal": "NONE"}
        prior = ctx.prices[:-1]
        hi = max(prior)
        lo = min(prior)
        if hi > 0 and price >= hi * (1 + breakout / 100.0):
            return {"signal": "BUY", "stop_loss_distance": price * (sl_pct / 100.0)}
        if lo > 0 and price <= lo * (1 - breakout / 100.0):
            return {"signal": "SELL", "stop_loss_distance": price * (sl_pct / 100.0)}
        return {"signal": "NONE"}


_TICK_STRATEGIES = {
    "TICK_MOMENTUM": TickMomentumStrategy,
    "TICK_MEAN_REVERT": TickMeanRevertStrategy,
    "TICK_BREAKOUT": TickBreakoutStrategy,
}


def normalize_tick_strategy(name: str) -> str:
    return name.upper()


def get_tick_strategy(strategy_name: str, config: dict | None) -> BaseTickStrategy:
    key = normalize_tick_strategy(strategy_name)
    cfg = merge_tick_config(key, config)
    cls = _TICK_STRATEGIES.get(key, BaseTickStrategy)
    return cls(cfg)


def is_tick_strategy(strategy_name: str) -> bool:
    return normalize_tick_strategy(strategy_name) in _TICK_STRATEGIES
