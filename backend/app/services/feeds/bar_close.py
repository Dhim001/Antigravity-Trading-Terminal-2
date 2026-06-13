"""Bar-close event emitter for live feeds."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

BarCloseCallback = Callable[[str], Awaitable[None]]


class BarCloseEmitter:
    """Fire async callbacks when a 1m bar closes for a symbol."""

    def __init__(self) -> None:
        self._callbacks: list[BarCloseCallback] = []

    def register(self, callback: BarCloseCallback) -> None:
        self._callbacks.append(callback)

    async def emit(self, symbol: str) -> None:
        for callback in self._callbacks:
            try:
                await callback(symbol)
            except Exception as exc:
                logger.error("Bar close callback failed for %s: %s", symbol, exc)

    def notify(self, symbol: str) -> None:
        """Schedule bar-close callbacks from sync feed code."""
        if not self._callbacks:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.emit(symbol))
