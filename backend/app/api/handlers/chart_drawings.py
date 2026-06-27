"""Per-symbol chart drawing persistence (trendlines, levels, fib, rectangles)."""

from __future__ import annotations

import json
import time

from app.api.context import RequestContext
from app.api.outbound import error
from app.api.protocol import Action, MessageType
from app.api.responses import send_to
from app.api.router import route
from app.db.connection import get_connection

MAX_DRAWINGS_PER_SYMBOL = 500


def _load_drawings(symbol: str) -> list:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT drawings_json FROM chart_drawings WHERE symbol = ?",
            (symbol,),
        )
        row = cursor.fetchone()
    if not row:
        return []
    raw = row["drawings_json"] if isinstance(row, dict) else row[0]
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _save_drawings(symbol: str, drawings: list) -> None:
    payload = json.dumps(drawings[:MAX_DRAWINGS_PER_SYMBOL])
    now = time.time()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO chart_drawings (symbol, drawings_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                drawings_json = excluded.drawings_json,
                updated_at = excluded.updated_at
            """,
            (symbol, payload, now),
        )
        conn.commit()


@route(Action.CHART_DRAWINGS_GET, tags=["chart"])
async def chart_drawings_get(ctx: RequestContext) -> None:
    symbol = (ctx.message.get("symbol") or "").strip()
    if not symbol:
        await send_to(ctx, error("symbol is required"))
        return
    drawings = _load_drawings(symbol)
    await send_to(ctx, {
        "type": MessageType.CHART_DRAWINGS,
        "data": {"symbol": symbol, "drawings": drawings},
    })


@route(Action.CHART_DRAWINGS_SET, tags=["chart"])
async def chart_drawings_set(ctx: RequestContext) -> None:
    symbol = (ctx.message.get("symbol") or "").strip()
    if not symbol:
        await send_to(ctx, error("symbol is required"))
        return
    drawings = ctx.message.get("drawings")
    if not isinstance(drawings, list):
        await send_to(ctx, error("drawings must be a list"))
        return
    _save_drawings(symbol, drawings)
    await send_to(ctx, {
        "type": MessageType.CHART_DRAWINGS,
        "data": {"symbol": symbol, "drawings": drawings[:MAX_DRAWINGS_PER_SYMBOL]},
    })
