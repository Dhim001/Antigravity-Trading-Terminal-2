"""Graceful shutdown coordination — SIGTERM/SIGINT, checkpoint flush."""

from __future__ import annotations

import asyncio
import logging
import signal

from app.services.runtime import system_state

logger = logging.getLogger(__name__)

_shutdown_event: asyncio.Event | None = None


def install_signal_handlers(loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
    """Register SIGTERM/SIGINT to trigger graceful shutdown."""
    global _shutdown_event
    _shutdown_event = event

    def _request_shutdown() -> None:
        logger.info("Shutdown signal received — initiating graceful shutdown...")
        if event.is_set():
            return
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            event.set()

    for sig in (getattr(signal, "SIGTERM", None), signal.SIGINT):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda _s, _f: _request_shutdown())


async def _cancel_background_tasks(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def graceful_shutdown(
    *,
    bot_manager=None,
    oms=None,
    feed=None,
    event_bus=None,
    agent_event_bus=None,
) -> None:
    """Persist bot state, pause bots, stop services, mark clean shutdown."""
    try:
        from app.api.http_server import stop_http_server

        await stop_http_server()
    except Exception as exc:
        logger.debug("HTTP server shutdown: %s", exc)

    if bot_manager is not None:
        try:
            checkpoint = {}
            for bot_id, bot in bot_manager.active_bots.items():
                checkpoint[bot_id] = {
                    "last_signal_bar_time": bot.get("last_signal_bar_time"),
                    "last_signal_at": bot.get("last_signal_at"),
                    "last_tick_signal_at": bot.get("last_tick_signal_at"),
                    "status": bot.get("status"),
                }
            if checkpoint:
                system_state.save_bot_runtime_checkpoint(checkpoint)
            await bot_manager._flush_log_buffer()
            await bot_manager.pause_all_running_bots()
        except Exception as exc:
            logger.warning("Bot shutdown checkpoint failed: %s", exc)

    try:
        if oms is not None and hasattr(oms, "stop"):
            await oms.stop()
    except Exception as exc:
        logger.debug("OMS shutdown: %s", exc)

    try:
        if feed is not None and hasattr(feed, "stop"):
            await feed.stop()
    except Exception as exc:
        logger.debug("Feed shutdown: %s", exc)

    try:
        if agent_event_bus is not None and hasattr(agent_event_bus, "stop"):
            await agent_event_bus.stop()
    except Exception as exc:
        logger.debug("Agent event bus shutdown: %s", exc)

    try:
        if event_bus is not None:
            await event_bus.stop()
    except Exception as exc:
        logger.debug("Event bus shutdown: %s", exc)

    if not system_state.is_safe_mode_active():
        system_state.mark_shutdown_clean()
    logger.info("Graceful shutdown complete.")


async def wait_for_shutdown_or_tasks(
    tasks: list[asyncio.Task],
    shutdown_event: asyncio.Event,
) -> None:
    """Block until shutdown signal — one-shot tasks finishing must not stop the server."""
    async def _log_task_failures() -> None:
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.error("Background task %s failed: %s", task.get_name(), exc)

    monitor = asyncio.create_task(_log_task_failures(), name="task-monitor")
    try:
        await shutdown_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        shutdown_event.set()
        raise
    finally:
        from app.api.http_server import stop_http_server

        try:
            await stop_http_server()
        except Exception:
            pass
        await _cancel_background_tasks(tasks)
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
