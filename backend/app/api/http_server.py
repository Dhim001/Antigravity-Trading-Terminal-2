"""Run the Starlette HTTP API alongside the WebSocket server."""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from app.api.http.app import create_http_app
from app.api.state import AppState
from app.config import HTTP_HOST, HTTP_PORT

logger = logging.getLogger(__name__)

_server: uvicorn.Server | None = None
_serve_task: asyncio.Task | None = None
_shutdown_watcher: asyncio.Task | None = None


def request_http_shutdown() -> None:
    """Ask uvicorn to exit its serve loop cleanly."""
    if _server is not None:
        _server.should_exit = True


async def stop_http_server(*, timeout: float = 8.0) -> None:
    """Wait for embedded uvicorn to finish after should_exit (never cancel unless forced)."""
    global _serve_task, _shutdown_watcher
    if _shutdown_watcher is not None and not _shutdown_watcher.done():
        _shutdown_watcher.cancel()
        await asyncio.gather(_shutdown_watcher, return_exceptions=True)
        _shutdown_watcher = None

    if _server is None:
        return

    request_http_shutdown()
    task = _serve_task
    if task is None or task.done():
        return

    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("HTTP server graceful shutdown timed out — forcing exit.")
        _server.force_exit = True
        _server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _watch_shutdown(shutdown_event: asyncio.Event) -> None:
    await shutdown_event.wait()
    request_http_shutdown()


async def run_http_server(state: AppState, shutdown_event: asyncio.Event | None = None) -> None:
    """Run uvicorn embedded in the parent asyncio loop (no duplicate signal handlers)."""
    global _server, _serve_task, _shutdown_watcher
    app = create_http_app(state)
    config = uvicorn.Config(
        app,
        host=HTTP_HOST,
        port=HTTP_PORT,
        log_level="warning",
        access_log=False,
        loop="asyncio",
        timeout_keep_alive=5,
        timeout_graceful_shutdown=3,
        lifespan="off",
    )
    server = uvicorn.Server(config)
    _server = server
    if shutdown_event is not None:
        _shutdown_watcher = asyncio.create_task(_watch_shutdown(shutdown_event), name="http-shutdown-watcher")
    logger.info("HTTP API listening on http://%s:%s", HTTP_HOST, HTTP_PORT)
    try:
        # _serve() skips uvicorn's capture_signals() — parent owns SIGINT/SIGTERM.
        await server._serve()
    except asyncio.CancelledError:
        request_http_shutdown()
        if server.started:
            try:
                await server.shutdown()
            except Exception as exc:
                logger.debug("HTTP server shutdown on cancel: %s", exc)
        raise
    finally:
        if _shutdown_watcher is not None:
            _shutdown_watcher.cancel()
            await asyncio.gather(_shutdown_watcher, return_exceptions=True)
            _shutdown_watcher = None
        if _server is server:
            _server = None
        if _serve_task is asyncio.current_task():
            _serve_task = None


def start_http_server(state: AppState, shutdown_event: asyncio.Event | None = None) -> asyncio.Task:
    """Create the HTTP server task and retain a handle for graceful shutdown."""
    global _serve_task
    _serve_task = asyncio.create_task(
        run_http_server(state, shutdown_event),
        name="http-server",
    )
    return _serve_task
