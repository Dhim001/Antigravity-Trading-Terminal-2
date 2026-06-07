from abc import ABC, abstractmethod
from typing import Callable, Awaitable, List

class BaseFeedService(ABC):
    @abstractmethod
    async def start(self) -> None:
        """Initialize connection to broker data streams."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Safely terminate stream connections."""
        pass

    @abstractmethod
    async def subscribe(self, symbol: str) -> None:
        """Subscribe to real-time candles & L2 orderbook for a symbol."""
        pass

    @abstractmethod
    async def unsubscribe(self, symbol: str) -> None:
        """Unsubscribe from a symbol's feeds."""
        pass

    @abstractmethod
    def register_broadcast_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        """Register handler to broadcast messages to connected clients."""
        pass

    @property
    @abstractmethod
    def symbols(self) -> List[str]:
        """Return the list of active trading symbols."""
        pass

    @abstractmethod
    def get_market_data(self, symbol: str) -> dict:
        """Return a snapshot of current market data (L1, L2, volume) for the symbol."""
        pass

    @abstractmethod
    def get_candles(self, symbol: str) -> List[dict]:
        """Return historical/live candle list for a symbol."""
        pass
