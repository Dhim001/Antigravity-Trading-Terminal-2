"""Built-in agent tool catalog — the tools TRADE_COPILOT (and other clients) can call.

Schemas mirror the planner prompt in copilot_agent.py; keep the two in sync
when adding tools. ``required`` is enforced only at execution time — the HITL
plan step intentionally runs on raw args so chat context fallbacks still apply.
"""

from __future__ import annotations

from app.services.agent.tools import handlers
from app.services.agent.tools.registry import AgentTool, ToolRegistry

_SYMBOL = {"type": "string", "description": "Ticker or pair, e.g. BTCUSDT or AAPL"}
_TIMEFRAME = {"type": "string", "description": "Candle timeframe: 1m, 5m, 15m, 1h, 4h, 1d"}
_BOT_ID = {"type": "string", "description": "Bot UUID (falls back to symbol/message lookup)"}


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()

    reg.register(AgentTool(
        name="analyze_symbol",
        description="Analyze a symbol's market regime, directional signal, score and confidence.",
        input_schema={
            "type": "object",
            "properties": {"symbol": dict(_SYMBOL), "timeframe": dict(_TIMEFRAME)},
        },
        side_effect="read",
        handler=handlers.analyze_symbol_handler,
    ))
    reg.register(AgentTool(
        name="meta_insight",
        description="Answer follow-up questions about the previous analysis (timeframe, method, confidence, signal, regime).",
        input_schema={
            "type": "object",
            "properties": {
                "field": {"type": "string", "description": "timeframe | method | confidence | signal | regime"},
                "symbol": dict(_SYMBOL),
            },
        },
        side_effect="read",
        handler=handlers.meta_insight_handler,
    ))
    reg.register(AgentTool(
        name="recommend_strategy",
        description="Recommend a bot strategy for a symbol or stated market regime (advisory only).",
        input_schema={
            "type": "object",
            "properties": {
                "symbol": dict(_SYMBOL),
                "regime": {"type": "string", "description": "ranging | trending | elevated_vol | compressed"},
            },
        },
        side_effect="read",
        handler=handlers.recommend_strategy_handler,
    ))
    reg.register(AgentTool(
        name="scan_market",
        description="Scan the watchlist and return the top movers by confidence and score.",
        input_schema={
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "Max assets to return (default 5)"}},
        },
        side_effect="read",
        handler=handlers.scan_market_handler,
    ))
    reg.register(AgentTool(
        name="get_portfolio_status",
        description="Account equity, gross/symbol exposure and risk utilization.",
        input_schema={"type": "object", "properties": {}},
        side_effect="read",
        handler=handlers.portfolio_status_handler,
    ))
    reg.register(AgentTool(
        name="list_bots",
        description="List active bots with symbol, strategy, status, allocation and PnL.",
        input_schema={"type": "object", "properties": {}},
        side_effect="read",
        handler=handlers.list_bots_handler,
    ))
    reg.register(AgentTool(
        name="get_bot_performance",
        description="Bot rankings and stats, or stats for a single bot when bot_id is given.",
        input_schema={
            "type": "object",
            "properties": {"bot_id": dict(_BOT_ID)},
        },
        side_effect="read",
        handler=handlers.bot_performance_handler,
    ))
    reg.register(AgentTool(
        name="get_sentiment",
        description="Aggregate 24h news/social sentiment for a symbol.",
        input_schema={
            "type": "object",
            "properties": {"symbol": dict(_SYMBOL)},
        },
        side_effect="read",
        handler=handlers.sentiment_handler,
    ))
    reg.register(AgentTool(
        name="run_backtest",
        description="Backtest a strategy on a symbol over N days; long runs are queued as jobs.",
        input_schema={
            "type": "object",
            "properties": {
                "symbol": dict(_SYMBOL),
                "strategy": {"type": "string", "description": "Strategy name, e.g. CHART_AGENT"},
                "days": {"type": "integer", "description": "Lookback horizon in days (1-365)"},
                "timeframe": dict(_TIMEFRAME),
                "allocation": {"type": "number", "description": "Capital allocation in account currency"},
            },
        },
        side_effect="read",
        handler=handlers.run_backtest_handler,
    ))
    reg.register(AgentTool(
        name="explain_trade",
        description="Explain a bot's most recent exit trade (or a specific trade).",
        input_schema={
            "type": "object",
            "properties": {"bot_id": dict(_BOT_ID), "symbol": dict(_SYMBOL)},
        },
        side_effect="read",
        handler=handlers.explain_trade_handler,
    ))
    reg.register(AgentTool(
        name="explain_bot_events",
        description="Recent WARN/ERROR/INFO log events for a bot (why it paused / was blocked).",
        input_schema={
            "type": "object",
            "properties": {
                "bot_id": dict(_BOT_ID),
                "limit": {"type": "integer", "description": "Max events to return (default 5)"},
            },
        },
        side_effect="read",
        handler=handlers.explain_bot_events_handler,
    ))

    reg.register(AgentTool(
        name="deploy_bot",
        description="Deploy a new trading bot on a symbol (requires confirmation).",
        input_schema={
            "type": "object",
            "properties": {
                "symbol": dict(_SYMBOL),
                "strategy": {"type": "string", "description": "Strategy name, e.g. CHART_AGENT"},
                "allocation": {"type": "number", "description": "Capital allocation in account currency"},
                "timeframe": dict(_TIMEFRAME),
                "config": {"type": "object", "description": "Optional bot config overrides"},
            },
            "required": ["symbol"],
        },
        side_effect="trade",
        hitl_required=True,
        handler=handlers.deploy_bot_handler,
        plan=handlers.deploy_bot_plan,
    ))
    reg.register(AgentTool(
        name="pause_bot",
        description="Pause a running bot (requires confirmation).",
        input_schema={
            "type": "object",
            "properties": {"bot_id": dict(_BOT_ID), "symbol": dict(_SYMBOL)},
            "required": ["bot_id"],
        },
        side_effect="control",
        hitl_required=True,
        handler=handlers.pause_bot_handler,
        plan=handlers.pause_bot_plan,
    ))
    reg.register(AgentTool(
        name="stop_bot",
        description="Stop a bot (requires confirmation).",
        input_schema={
            "type": "object",
            "properties": {"bot_id": dict(_BOT_ID), "symbol": dict(_SYMBOL)},
            "required": ["bot_id"],
        },
        side_effect="control",
        hitl_required=True,
        handler=handlers.stop_bot_handler,
        plan=handlers.stop_bot_plan,
    ))
    reg.register(AgentTool(
        name="pause_all_bots",
        description="Pause every running bot (requires confirmation).",
        input_schema={"type": "object", "properties": {}},
        side_effect="control",
        hitl_required=True,
        handler=handlers.pause_all_bots_handler,
        plan=handlers.pause_all_bots_plan,
    ))
    reg.register(AgentTool(
        name="stop_all_bots",
        description="Stop every bot (requires confirmation).",
        input_schema={"type": "object", "properties": {}},
        side_effect="control",
        hitl_required=True,
        handler=handlers.stop_all_bots_handler,
        plan=handlers.stop_all_bots_plan,
    ))
    reg.register(AgentTool(
        name="update_bot_config",
        description="Patch a bot's config, e.g. stop_loss_percent or min_confidence (requires confirmation).",
        input_schema={
            "type": "object",
            "properties": {
                "bot_id": dict(_BOT_ID),
                "symbol": dict(_SYMBOL),
                "config_patch": {"type": "object", "description": "Config keys to merge"},
            },
            "required": ["bot_id"],
        },
        side_effect="control",
        hitl_required=True,
        handler=handlers.update_bot_config_handler,
        plan=handlers.update_bot_config_plan,
    ))

    reg.register(AgentTool(
        name="help",
        description="Show TRADE_COPILOT capabilities and example prompts.",
        input_schema={"type": "object", "properties": {}},
        side_effect="read",
        handler=handlers.help_handler,
    ))

    return reg


_REGISTRY: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_registry()
    return _REGISTRY
