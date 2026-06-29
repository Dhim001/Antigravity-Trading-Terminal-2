"""Background data quality evaluation loop."""

from __future__ import annotations

import asyncio
import logging

from app.config import DATA_QUALITY_ENABLED, DATA_QUALITY_INTERVAL_SEC

logger = logging.getLogger(__name__)

_last_report: dict | None = None


def get_last_report() -> dict | None:
    return _last_report


async def data_quality_loop(bot_manager, feed):
    if not DATA_QUALITY_ENABLED:
        logger.info("Data quality monitor disabled — loop idle.")
        while True:
            await asyncio.sleep(3600)
        return

    from app.services.data_quality.monitor import evaluate_and_act

    logger.info("Starting data quality loop (interval=%.0fs)...", DATA_QUALITY_INTERVAL_SEC)
    global _last_report
    while True:
        try:
            _last_report = await evaluate_and_act(feed, bot_manager)
        except Exception as exc:
            logger.error("Data quality loop error: %s", exc)
        await asyncio.sleep(DATA_QUALITY_INTERVAL_SEC)
