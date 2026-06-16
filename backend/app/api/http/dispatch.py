"""Invoke centralized WS actions over HTTP."""

from __future__ import annotations

from app.api.context import RequestContext
from app.api.http.collector import HttpConnectionManager
from app.api.protocol import MessageType
from app.api.router import dispatch, ensure_routes_loaded
from app.api.state import AppState

ensure_routes_loaded()


async def invoke_action(state: AppState, action: str, message: dict | None = None) -> list[dict]:
    """Run a registered action and return all outbound message payloads."""
    http_manager = HttpConnectionManager()
    payload = {"action": action, **(message or {})}
    ctx = RequestContext(
        websocket=None,
        manager=http_manager,
        oms=state.oms,
        bot_manager=state.bot_manager,
        backtester=state.backtester,
        chart_analyst=state.chart_analyst,
        message=payload,
        action=action,
    )
    await dispatch(ctx)
    return http_manager.messages


def _pick_primary_message(messages: list[dict]) -> dict:
    """Prefer command result frames over follow-up account/history pushes."""
    priority = (
        MessageType.ORDER_RESULT,
        MessageType.BOT_DETAIL,
        MessageType.BACKTEST_RESULT,
        MessageType.AGENT_INSIGHT,
        MessageType.BOTS_UPDATE,
        MessageType.ACCOUNT_UPDATE,
        MessageType.TRADE_HISTORY,
        MessageType.HISTORY_UPDATE,
        MessageType.SYSTEM_STATS,
    )
    for msg_type in priority:
        for msg in messages:
            if msg.get("type") == msg_type:
                return msg
    return messages[-1]


def http_status_and_body(messages: list[dict]) -> tuple[int, dict]:
    """Map WS reply frames to an HTTP status + JSON body."""
    if not messages:
        return 500, {"ok": False, "error": "Handler produced no response"}

    for msg in messages:
        if msg.get("type") == MessageType.ERROR:
            return 400, {"ok": False, "error": msg.get("message", "Request failed")}

    for msg in messages:
        if msg.get("type") == MessageType.ORDER_RESULT:
            data = msg.get("data") or {}
            if data.get("status") == "error":
                return 400, {"ok": False, "error": data.get("message", "Request failed"), "messages": messages}

    primary = _pick_primary_message(messages)
    return 200, {
        "ok": True,
        "type": primary.get("type"),
        "data": primary.get("data"),
        "messages": messages,
    }
