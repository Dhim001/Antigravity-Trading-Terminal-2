"""Typed outbound WebSocket message builders and publishers.

All server -> client frames should be constructed here so the wire protocol
has a single source of truth alongside ``app.api.protocol.MessageType``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.api.protocol import MessageType

BroadcastFn = Callable[[dict], Awaitable[None]]


def frame(msg_type: MessageType, data: Any = None, *, message: str | None = None) -> dict:
    """Build a wire payload for a given message type."""
    if msg_type == MessageType.ERROR:
        return {"type": msg_type.value, "message": message or ""}
    return {"type": msg_type.value, "data": data}


def terminal_config(data: dict) -> dict:
    return frame(MessageType.TERMINAL_CONFIG, data)


def market_update(data: dict) -> dict:
    return frame(MessageType.MARKET_UPDATE, data)


def orderbook_update(data: dict) -> dict:
    return frame(MessageType.ORDERBOOK_UPDATE, data)


def account_update(data: dict) -> dict:
    return frame(MessageType.ACCOUNT_UPDATE, data)


def trade_history(data: list) -> dict:
    return frame(MessageType.TRADE_HISTORY, data)


def order_result(data: dict) -> dict:
    return frame(MessageType.ORDER_RESULT, data)


def history_update(data: dict) -> dict:
    return frame(MessageType.HISTORY_UPDATE, data)


def system_stats(data: dict) -> dict:
    return frame(MessageType.SYSTEM_STATS, data)


def bots_update(data: list) -> dict:
    return frame(MessageType.BOTS_UPDATE, data)


def bot_detail(data: dict) -> dict:
    return frame(MessageType.BOT_DETAIL, data)


def bot_log(bot_id: str, level: str, message: str) -> dict:
    return frame(MessageType.BOT_LOG, {"bot_id": bot_id, "level": level, "message": message})


def bot_logs_history(data: list) -> dict:
    return frame(MessageType.BOT_LOGS_HISTORY, data)


def backtest_result(data: dict) -> dict:
    return frame(MessageType.BACKTEST_RESULT, data)


def backtest_progress(data: dict) -> dict:
    return frame(MessageType.BACKTEST_PROGRESS, data)


def ticks_update(data: dict, *, meta: dict | None = None) -> dict:
    payload = frame(MessageType.TICKS_UPDATE, data)
    if meta is not None:
        payload["meta"] = meta
    return payload


def bots_history(data: list) -> dict:
    return frame(MessageType.BOTS_HISTORY, data)


def agent_insight(data: dict) -> dict:
    return frame(MessageType.AGENT_INSIGHT, data)


def error(message: str) -> dict:
    return frame(MessageType.ERROR, message=message)


async def publish(broadcast_fn: BroadcastFn | None, payload: dict) -> None:
    if broadcast_fn:
        await broadcast_fn(payload)


async def publish_market_update(broadcast_fn: BroadcastFn | None, data: dict) -> None:
    await publish(broadcast_fn, market_update(data))


async def publish_orderbook_update(broadcast_fn: BroadcastFn | None, data: dict) -> None:
    await publish(broadcast_fn, orderbook_update(data))


async def publish_account_update(broadcast_fn: BroadcastFn | None, data: dict) -> None:
    await publish(broadcast_fn, account_update(data))


async def publish_trade_history(broadcast_fn: BroadcastFn | None, data: list) -> None:
    await publish(broadcast_fn, trade_history(data))


async def publish_system_stats(broadcast_fn: BroadcastFn | None, data: dict) -> None:
    await publish(broadcast_fn, system_stats(data))


async def publish_bot_log(
    broadcast_fn: BroadcastFn | None,
    bot_id: str,
    level: str,
    message: str,
) -> None:
    await publish(broadcast_fn, bot_log(bot_id, level, message))


async def publish_bots_update(broadcast_fn: BroadcastFn | None, data: list) -> None:
    await publish(broadcast_fn, bots_update(data))


async def publish_agent_insight(broadcast_fn: BroadcastFn | None, data: dict) -> None:
    from app.api.outbound import agent_insight

    await publish(broadcast_fn, agent_insight(data))


async def publish_bot_detail(broadcast_fn: BroadcastFn | None, data: dict) -> None:
    await publish(broadcast_fn, bot_detail(data))


async def publish_post_trade_bundle(
    broadcast_fn: BroadcastFn | None,
    account_data: dict,
    history_data: list,
) -> None:
    """Standard OMS push after fills: account + trade history."""
    await publish_account_update(broadcast_fn, account_data)
    await publish_trade_history(broadcast_fn, history_data)
