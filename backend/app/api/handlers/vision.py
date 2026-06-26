"""On-demand chart vision — describe structure only."""

from __future__ import annotations

import json
import logging
import time

from app.api.context import RequestContext
from app.api.outbound import error
from app.api.protocol import Action, MessageType
from app.api.responses import send_to
from app.api.router import route
from app.config import AGENT_VISION_CACHE_SEC, AGENT_VISION_ENABLED, REDIS_URL
from app.observability.metrics import inc
from app.services.agent.models import VisionReport
from app.services.agent.vision_client import describe_chart
from app.services.agent.vision_store import get_vision_exact, persist_vision_report

logger = logging.getLogger(__name__)

ALLOWED_TF = frozenset({"1h", "4h", "1H", "4H"})
_vision_last_at: dict[str, float] = {}
VISION_MIN_INTERVAL_SEC = 900.0

_redis = None
if REDIS_URL:
    try:
        import redis
        _redis = redis.from_url(REDIS_URL)
    except Exception:
        pass


def _cache_key(symbol: str, timeframe: str, bar_time: int) -> str:
    return f"vision:{symbol.upper()}:{timeframe.lower()}:{bar_time}"


def _warm_redis(cache_key: str, payload: dict) -> None:
    if not _redis:
        return
    try:
        _redis.setex(cache_key, AGENT_VISION_CACHE_SEC, json.dumps(payload))
    except Exception:
        pass


@route(Action.CHART_VISION, tags=["agent"])
async def chart_vision(ctx: RequestContext) -> None:
    if not AGENT_VISION_ENABLED:
        await send_to(ctx, error("Chart vision is disabled"))
        return

    msg = ctx.message
    symbol = (msg.get("symbol") or "").upper().strip()
    timeframe = (msg.get("timeframe") or "").lower()
    bar_time = int(msg.get("bar_time") or 0)
    image_b64 = msg.get("image_base64") or ""

    if not symbol or not image_b64:
        await send_to(ctx, error("symbol and image_base64 are required"))
        return
    if timeframe not in ("1h", "4h"):
        await send_to(ctx, error("timeframe must be 1h or 4h"))
        return

    rate_key = f"{symbol}:{timeframe}"
    now = time.monotonic()
    last = _vision_last_at.get(rate_key, 0.0)
    if now - last < VISION_MIN_INTERVAL_SEC:
        await send_to(ctx, error("Rate limited — wait before requesting vision again"))
        return

    cache_key = _cache_key(symbol, timeframe, bar_time)

    stored = get_vision_exact(symbol, timeframe, bar_time)
    if stored:
        stored["cached"] = True
        inc("agent_vision_sqlite_hit_total")
        _warm_redis(cache_key, stored)
        await send_to(ctx, {"type": MessageType.VISION_REPORT, "data": stored})
        return

    if _redis:
        try:
            raw = _redis.get(cache_key)
            if raw:
                data = json.loads(raw)
                data["cached"] = True
                inc("agent_vision_cache_hit_total")
                try:
                    persist_vision_report(VisionReport.from_dict({**data, "cached": False}))
                except Exception:
                    logger.debug("vision sqlite backfill failed", exc_info=True)
                await send_to(ctx, {"type": MessageType.VISION_REPORT, "data": data})
                return
        except Exception:
            pass

    _vision_last_at[rate_key] = now
    inc("agent_vision_requests_total")

    parsed = await describe_chart(symbol, timeframe, image_b64)
    if not parsed:
        await send_to(ctx, error("Vision analysis unavailable"))
        return

    report = VisionReport(
        symbol=symbol,
        timeframe=timeframe,
        bar_time=bar_time,
        structure=parsed.get("structure") or "",
        patterns=list(parsed.get("patterns") or []),
        notes=parsed.get("notes") or "",
        model=parsed.get("model"),
        cached=False,
    )
    payload = report.to_dict()
    try:
        persist_vision_report(report)
        inc("agent_vision_sqlite_write_total")
    except Exception:
        logger.warning("Failed to persist vision report %s", report.report_id, exc_info=True)

    _warm_redis(cache_key, payload)

    await send_to(ctx, {"type": MessageType.VISION_REPORT, "data": payload})
