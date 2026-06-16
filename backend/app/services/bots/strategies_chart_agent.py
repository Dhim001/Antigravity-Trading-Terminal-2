"""CHART_AGENT built-in strategy — consumes cached analyst insights."""

from __future__ import annotations

from app.services.agent.chart_analyst import get_chart_analyst
from app.services.bots.indicators import merge_strategy_config


class ChartAgentStrategy:
    def __init__(self, config: dict):
        self.config = config or {}

    def evaluate(self, df_row: dict) -> dict:
        cfg = merge_strategy_config("CHART_AGENT", self.config)
        symbol = cfg.get("symbol") or self.config.get("symbol", "")
        min_confidence = float(cfg.get("min_confidence", 0.55))
        bar_time = df_row.get("time")

        try:
            analyst = get_chart_analyst()
            insight = analyst.get_cached(symbol)
        except RuntimeError:
            return {"signal": "NONE"}

        if not insight or insight.get("bar_time") != bar_time:
            return {"signal": "NONE"}

        if float(insight.get("confidence", 0)) < min_confidence:
            return {"signal": "NONE"}

        signal = insight.get("signal", "NONE")
        if signal not in ("BUY", "SELL"):
            return {"signal": "NONE"}

        levels = insight.get("levels") or {}
        out: dict = {"signal": signal}
        if levels.get("stop_loss_distance") is not None:
            out["stop_loss_distance"] = levels["stop_loss_distance"]
        if levels.get("take_profit_price") is not None:
            out["take_profit_price"] = levels["take_profit_price"]
        return out
