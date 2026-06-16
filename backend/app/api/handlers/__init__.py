"""Import handler modules to register all WebSocket routes."""

from app.api.handlers import account, admin, agent, bots, market, scanner, trading, vision

__all__ = ["account", "admin", "agent", "bots", "market", "trading"]
