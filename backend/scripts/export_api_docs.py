#!/usr/bin/env python3
"""Print WebSocket API markdown from the centralized route registry."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.protocol import Action, MessageType  # noqa: E402
from app.api.router import ensure_routes_loaded, list_routes  # noqa: E402

ACTION_DESCRIPTIONS: dict[str, str] = {
    Action.PLACE_ORDER: "Place market or limit order with optional SL/TP",
    Action.CANCEL_ORDER: "Cancel a pending limit order by `order_id`",
    Action.UPDATE_POSITION_SL_TP: "Update stop-loss / take-profit on an open position",
    Action.GET_ACCOUNT: "Request account balances and positions snapshot",
    Action.GET_HISTORY: "Request full trade history blotter",
    Action.SUBSCRIBE_SYMBOL: "Subscribe chart symbol and receive candle history",
    Action.ADMIN_SET_SIMULATION: "Adjust sim tick speed, volatility, and symbol bias",
    Action.ADMIN_SEED_BALANCE: "Add balance to an asset account (SIM only)",
    Action.ADMIN_RESET_SYSTEM: "Wipe DB to defaults - orders, positions, bots (SIM only)",
    Action.ADMIN_EMERGENCY_STOP: "Cancel all orders, halt all bots",
    Action.ADMIN_GET_STATS: "Database and simulation engine statistics",
    Action.BOT_CREATE: "Deploy a new algo bot from strategy template",
    Action.BOT_STOP: "Stop a single bot by `bot_id`",
    Action.BOT_PAUSE: "Pause a running bot",
    Action.BOT_RESUME: "Resume a paused bot",
    Action.BOT_STOP_ALL: "Stop every active bot",
    Action.BOT_GET_DETAIL: "Fetch trades, snapshots, and config for one bot",
    Action.BOT_GET_ALL: "List active bots",
    Action.BOT_LIST_ALL: "List all bots including stopped (with stats)",
    Action.RUN_BACKTEST: "Run offline backtest for symbol + strategy",
}

MESSAGE_DESCRIPTIONS: dict[str, str] = {
    MessageType.TERMINAL_CONFIG: "Handshake - mode, symbols, feature flags",
    MessageType.ORDER_RESULT: "Order/admin/bot command result",
    MessageType.ACCOUNT_UPDATE: "Balances, positions, unrealized P&L",
    MessageType.TRADE_HISTORY: "Full or incremental trade log",
    MessageType.HISTORY_UPDATE: "Candle history for subscribed symbol",
    MessageType.MARKET_UPDATE: "Live tick / quote broadcast",
    MessageType.ORDERBOOK_UPDATE: "Simulated order book depth (SIM mode)",
    MessageType.SYSTEM_STATS: "DB row counts and sim engine settings",
    MessageType.BOTS_UPDATE: "Bot registry list",
    MessageType.BOT_DETAIL: "Single bot analytics payload",
    MessageType.BOT_LOG: "Streaming bot log line",
    MessageType.BOT_LOGS_HISTORY: "Recent bot logs on connect",
    MessageType.BACKTEST_RESULT: "Backtest metrics and equity curve",
    MessageType.TICKS_UPDATE: "Sub-minute tick archive for a symbol",
    MessageType.BOTS_HISTORY: "Full bot registry including stopped bots",
    MessageType.ERROR: "Unknown action or handler exception",
}


def render_actions_table() -> str:
    ensure_routes_loaded()
    routes = list_routes()
    lines = [
        "| Action | Tags | SIM only | Description |",
        "|--------|------|----------|-------------|",
    ]
    for action in Action:
        meta = routes.get(action.value)
        tags = ", ".join(meta.tags) if meta else "—"
        sim = "Yes" if meta and meta.sim_only else "No"
        desc = ACTION_DESCRIPTIONS.get(action, "—")
        lines.append(f"| `{action.value}` | {tags} | {sim} | {desc} |")
    return "\n".join(lines)


def render_messages_table() -> str:
    lines = [
        "| Type | Description |",
        "|------|-------------|",
    ]
    for msg_type in MessageType:
        desc = MESSAGE_DESCRIPTIONS.get(msg_type, "—")
        lines.append(f"| `{msg_type.value}` | {desc} |")
    return "\n".join(lines)


def render_full_section() -> str:
    return "\n".join([
        "## WebSocket API",
        "",
        "Single endpoint: **`ws://127.0.0.1:8765`**. All client requests use JSON with an `action` field; server replies use a `type` field.",
        "",
        "### Client -> server (actions)",
        "",
        "```json",
        '{ "action": "place_order", "symbol": "AAPL", "side": "buy", "type": "market", "quantity": 10 }',
        "```",
        "",
        render_actions_table(),
        "",
        "### Server -> client (message types)",
        "",
        "```json",
        '{ "type": "account_update", "data": { ... } }',
        "```",
        "",
        render_messages_table(),
        "",
        "Route registry: `backend/app/api/router.py`. Frontend constants: `frontend/src/api/protocol.js`. Regenerate this section:",
        "",
        "```bash",
        "python backend/scripts/export_api_docs.py",
        "```",
        "",
        "### Adding a new action",
        "",
        "1. Add enum in `backend/app/api/protocol.py` and `frontend/src/api/protocol.js`",
        "2. Register handler with `@route(...)` in `backend/app/api/handlers/`",
        "3. Handle outbound `type` in `frontend/src/services/websocket.js` if needed",
        "",
    ])


if __name__ == "__main__":
    print(render_full_section())
