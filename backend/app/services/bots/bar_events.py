"""Bar-close detection for mode-agnostic bot tick routing."""


class BarCloseTracker:
    """Fire once per completed 1m bar when a new bar starts."""

    def __init__(self):
        self._last_seen_bar: dict[str, int] = {}
        self._last_processed_close: dict[str, int] = {}

    def check(self, symbol: str, candles: list) -> bool:
        if not candles or len(candles) < 2:
            return False

        current = candles[-1].get("time")
        if current is None:
            return False

        prev_seen = self._last_seen_bar.get(symbol)
        if prev_seen is None:
            self._last_seen_bar[symbol] = current
            return False

        if current == prev_seen:
            return False

        self._last_seen_bar[symbol] = current
        closed_time = candles[-2].get("time")
        if closed_time is None:
            return False

        if self._last_processed_close.get(symbol) == closed_time:
            return False

        self._last_processed_close[symbol] = closed_time
        return True
