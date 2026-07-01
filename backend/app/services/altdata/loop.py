"""Background alternative-data refresh."""

from __future__ import annotations

import asyncio
import logging

from app.config import ALTDATA_ENABLED, ALTDATA_REFRESH_INTERVAL_SEC, MASSIVE_API_KEY, TERMINAL_MODE

logger = logging.getLogger(__name__)


def _run_refresh(symbols: list[str] | None) -> dict:
    from app.config import SENTIMENT_ENABLED

    if MASSIVE_API_KEY:
        from app.services.altdata.massive_provider import refresh_altdata

        result = refresh_altdata(symbols)
    elif TERMINAL_MODE == "LIVE_ALPACA":
        from app.services.altdata.alpaca_provider import refresh_altdata

        result = refresh_altdata(symbols)
    else:
        result = {"enabled": False, "reason": "no calendar provider configured"}

    if SENTIMENT_ENABLED:
        from app.services.altdata.sentiment_provider import refresh_sentiment

        try:
            result["sentiment"] = refresh_sentiment(symbols)
        except Exception as exc:
            logger.warning("Sentiment refresh failed: %s", exc)
            result["sentiment"] = {"enabled": True, "error": str(exc)}
    return result


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
            if first or result.get("economic_written") or result.get("corporate_written") or (
                (result.get("sentiment") or {}).get("events_written")
            ):
                if not first or result.get("enabled", True):
                    logger.info("Alt-data refresh: %s", result)
            first = False
        except Exception as exc:
            logger.error("Alt-data refresh error: %s", exc)
        await asyncio.sleep(ALTDATA_REFRESH_INTERVAL_SEC)
