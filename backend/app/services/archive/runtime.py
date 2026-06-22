"""Background archive capture, flush, and rollup loops."""

from __future__ import annotations

import asyncio
import logging

from app.config import (
    ARCHIVE_ENABLED,
    ARCHIVE_FLUSH_INTERVAL,
    ARCHIVE_ROLLUP_INTERVAL,
    ARCHIVE_BACKFILL_ON_STARTUP,
    TERMINAL_MODE,
)
from app.services.archive.bar_hook import ArchiveBarHook
from app.services.archive.backfill import run_archive_backfill
from app.services.archive.rollup import run_rollup_job
from app.services.archive.writer import get_archive_writer

logger = logging.getLogger(__name__)


def _archive_source() -> str:
    return TERMINAL_MODE or "SIMULATED"


async def archive_startup_backfill(feed) -> None:
    """Seed market_bars_1m from parquet / feed buffer once at startup (Option A DB)."""
    if not ARCHIVE_ENABLED:
        return

    from app.services.archive.wal import replay_wal
    from app.services.archive.writer import _upsert_1m_rows

    replay_wal(_upsert_1m_rows)

    if not ARCHIVE_BACKFILL_ON_STARTUP:
        return
    try:
        result = await asyncio.to_thread(
            run_archive_backfill,
            list(feed.symbols),
            feed=feed,
            source=_archive_source(),
            skip_existing=True,
        )
        if result.get("rows_written"):
            logger.info("Archive startup backfill: %s", result)
    except Exception as exc:
        logger.warning("Archive startup backfill failed: %s", exc)


async def archive_capture_loop(feed) -> None:
    """Poll feeds for bar closes and upsert the in-progress bar on flush."""
    if not ARCHIVE_ENABLED:
        logger.info("Market archive disabled (ARCHIVE_ENABLED=false)")
        return

    hook = ArchiveBarHook()
    writer = get_archive_writer()
    source = _archive_source()
    interval = getattr(feed, "tick_interval", 1.0)
    logger.info(
        "Market archive capture loop active (source=%s, flush=%ss)",
        source,
        ARCHIVE_FLUSH_INTERVAL,
    )

    while True:
        try:
            for symbol in feed.symbols:
                candles = feed.get_candles(symbol)
                if not candles:
                    continue

                closed = hook.closed_bar(symbol, candles)
                if closed:
                    writer.record_bar(symbol, closed, source)

                writer.record_bar(symbol, candles[-1], source)

            writer.maybe_flush()
        except Exception as exc:
            logger.warning("Archive capture loop error: %s", exc)

        await asyncio.sleep(interval)


async def archive_rollup_loop(feed) -> None:
    if not ARCHIVE_ENABLED:
        return

    logger.info(
        "Market archive rollup loop active (interval=%ss)",
        ARCHIVE_ROLLUP_INTERVAL,
    )
    while True:
        await asyncio.sleep(ARCHIVE_ROLLUP_INTERVAL)
        try:
            get_archive_writer().flush()
            result = run_rollup_job(list(feed.symbols))
            if result.get("minutes_deleted") or result.get("hours_purged"):
                logger.info("Archive rollup: %s", result)
        except Exception as exc:
            logger.warning("Archive rollup loop error: %s", exc)
