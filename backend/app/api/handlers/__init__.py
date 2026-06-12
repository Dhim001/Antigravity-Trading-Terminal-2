"""Import handler modules to register all WebSocket routes."""

from app.api.handlers import account, admin, bots, market, trading

__all__ = ["account", "admin", "bots", "market", "trading"]
