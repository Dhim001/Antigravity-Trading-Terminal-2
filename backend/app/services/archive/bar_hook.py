"""Bar-close detection wrapper for archive capture."""

from __future__ import annotations

from app.services.bots.bar_events import BarCloseTracker


class ArchiveBarHook:
    def __init__(self) -> None:
        self._tracker = BarCloseTracker()

    def closed_bar(self, symbol: str, candles: list) -> dict | None:
        """Return the just-closed 1m bar when a new bar starts, else None."""
        if not self._tracker.check(symbol, candles):
            return None
        if len(candles) < 2:
            return None
        return candles[-2]
