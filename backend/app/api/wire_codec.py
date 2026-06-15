"""Optional MessagePack encoding for large WebSocket outbound frames."""

from __future__ import annotations

import json
from typing import Any

import msgpack

from app.api.protocol import MessageType
from app.config import WS_MSGPACK_ENABLED, WS_MSGPACK_MIN_BYTES

# Binary frame marker — clients treat 0x01 prefix as msgpack payload.
MSGPACK_MARKER = b"\x01"

_MSGPACK_ELIGIBLE = frozenset(
    {
        MessageType.HISTORY_UPDATE.value,
        MessageType.TICKS_UPDATE.value,
        MessageType.BOTS_HISTORY.value,
        MessageType.TRADE_HISTORY.value,
        MessageType.BOT_LOGS_HISTORY.value,
        MessageType.BACKTEST_RESULT.value,
    }
)


def encode_wire_payload(payload: dict[str, Any]) -> str | bytes:
    """Return JSON text or binary msgpack frame for a wire payload."""
    json_text = json.dumps(payload, separators=(",", ":"))
    if not WS_MSGPACK_ENABLED:
        return json_text

    msg_type = payload.get("type")
    if msg_type not in _MSGPACK_ELIGIBLE:
        return json_text

    json_len = len(json_text.encode("utf-8"))
    if json_len < WS_MSGPACK_MIN_BYTES:
        return json_text

    packed = msgpack.packb(payload, use_bin_type=True)
    return MSGPACK_MARKER + packed
