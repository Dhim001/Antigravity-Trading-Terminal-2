"""Background alternative-data refresh."""

from __future__ import annotations

import asyncio
import logging

from app.config import ALTDATA_ENABLED, ALTDATA_REFRESH_INTERVAL_SEC, MASSIVE_API_KEY, TERMINAL_MODE

logger = logging.getLogger(__name__)


def _run_refresh(symbols: list[str] | None) -> dict:
    if MASSIVE_API_KEY:
        from app.services.altdata.massive_provider import refresh_altdata

        return refresh_altdata(symbols)
    if TERMINAL_MODE == "LIVE_ALPACA":
        from app.services.altdata.alpaca_provider import refresh_altdata

        return refresh_altdata(symbols)
    return {"enabled": False, "reason": "no provider configured"}


async def altdata_refresh_loop(feed):
    if not ALTDATA_ENABLED:
        logger.info("Alt-data refresh disabled — loop idle.")
        while True:
            await asyncio.sleep(3600)
        return

    logger.info(
        "Alt-data refresh loop started (interval=%.0fs)...",
        ALTDATA_REFRESH_INTERVAL_SEC,
    )
    first = True
    while True:
        try:
            symbols = list(getattr(feed, "symbols", []) or [])
            result = await asyncio.to_thread(_run_refresh, symbols or None)
            if first or result.get("economic_written") or result.get("corporate_written"):
                if not first or result.get("enabled", True):
                    logger.info("Alt-data refresh: %s", result)
            first = False
        except Exception as exc:
            logger.error("Alt-data refresh error: %s", exc)
        await asyncio.sleep(ALTDATA_REFRESH_INTERVAL_SEC)
