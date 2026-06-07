from abc import ABC, abstractmethod
from typing import Dict, List, Any

class BaseOMSService(ABC):
    @abstractmethod
    async def initialize(self) -> None:
        """Configure credentials and sync starting balances/positions."""
        pass

    @abstractmethod
    async def place_order(self, order_req: dict) -> dict:
        """Submit a new order to the broker (Market or Limit)."""
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> dict:
        """Request cancellation of an active pending order."""
        pass

    @abstractmethod
    def get_positions(self) -> List[dict]:
        """Fetch active open positions."""
        pass

    @abstractmethod
    def get_balances(self) -> Dict[str, dict]:
        """Fetch current buying power and asset balances."""
        pass

    @abstractmethod
    def get_trades(self, limit: int = 100) -> List[dict]:
        """Retrieve historical execution reports."""
        pass

    @abstractmethod
    async def update_position_sl_tp(self, symbol: str, sl_pct: float, tp_pct: float) -> dict:
        """Modify or set stop loss or take profit targets."""
        pass

    @abstractmethod
    def get_account_data(self) -> dict:
        """Return standardized account snapshot (balances, positions, active orders)."""
        pass

    @abstractmethod
    def get_trade_history(self) -> List[dict]:
        """Return full matched trade execution logs."""
        pass

    @abstractmethod
    async def emergency_stop(self) -> dict:
        """Cancel all orders and liquidate all open positions."""
        pass

