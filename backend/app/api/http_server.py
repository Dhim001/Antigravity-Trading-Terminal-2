"""Run the Starlette HTTP API alongside the WebSocket server."""

from __future__ import annotations

import logging

import uvicorn

from app.api.http.app import create_http_app
from app.api.state import AppState
from app.config import HTTP_HOST, HTTP_PORT

logger = logging.getLogger(__name__)


async def run_http_server(state: AppState) -> None:
    app = create_http_app(state)
    config = uvicorn.Config(
        app,
        host=HTTP_HOST,
        port=HTTP_PORT,
        log_level="warning",
        access_log=False,
        loop="asyncio",
        timeout_keep_alive=5,
    )
    server = uvicorn.Server(config)
    logger.info("HTTP API listening on http://%s:%s", HTTP_HOST, HTTP_PORT)
    await server.serve()
