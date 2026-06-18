"""Bar-close detection for mode-agnostic bot tick routing."""


def _tracker_key(symbol: str, timeframe: str = "1m") -> str:
    return f"{symbol.upper()}:{timeframe}"


class BarCloseTracker:
    """Fire once per completed bar when a new bar starts on the given series."""

    def __init__(self):
        self._last_seen_bar: dict[str, int] = {}
        self._last_processed_close: dict[str, int] = {}

    def check(self, symbol: str, candles: list, *, timeframe: str = "1m") -> bool:
        if not candles or len(candles) < 2:
            return False

        key = _tracker_key(symbol, timeframe)
        current = candles[-1].get("time")
        if current is None:
            return False

        prev_seen = self._last_seen_bar.get(key)
        if prev_seen is None:
            self._last_seen_bar[key] = current
            return False

        if current == prev_seen:
            return False

        self._last_seen_bar[key] = current
        closed_time = candles[-2].get("time")
        if closed_time is None:
            return False

        if self._last_processed_close.get(key) == closed_time:
            return False

        self._last_processed_close[key] = closed_time
        return True
