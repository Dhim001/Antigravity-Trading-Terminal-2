"""Daily digest scheduler."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import (
    NOTIFICATION_DIGEST_ENABLED,
    NOTIFICATION_DIGEST_HOUR,
    NOTIFICATION_DIGEST_TZ,
)

logger = logging.getLogger(__name__)


async def notification_digest_loop(oms):
    if not NOTIFICATION_DIGEST_ENABLED:
        logger.info("Notification digest disabled — loop idle.")
        while True:
            await asyncio.sleep(3600)
        return

    from app.services.notifications.digest import send_daily_digest
    from app.services.runtime import system_state

    logger.info(
        "Notification digest loop started (hour=%02d, tz=%s)",
        NOTIFICATION_DIGEST_HOUR,
        NOTIFICATION_DIGEST_TZ,
    )
    last_digest_date = system_state.get_last_digest_date()

    while True:
        try:
            tz = ZoneInfo(NOTIFICATION_DIGEST_TZ)
            now = datetime.now(tz)
            today = now.date().isoformat()
            if now.hour >= NOTIFICATION_DIGEST_HOUR and last_digest_date != today:
                sent = await send_daily_digest(oms)
                last_digest_date = today
                system_state.set_last_digest_date(today)
                if sent:
                    logger.info("Daily digest queued to %d channel(s)", sent)
        except Exception as exc:
            logger.error("Digest loop error: %s", exc)
        await asyncio.sleep(60)
