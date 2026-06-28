"""Import handler modules to register all WebSocket routes."""

from app.api.handlers import (
    account, admin, agent, analytics, bots, chart_drawings, journal, market, scanner, trading, vision,
)

__all__ = [
    "account", "admin", "agent", "analytics", "bots", "chart_drawings", "journal", "market", "scanner",
    "trading", "vision",
]
