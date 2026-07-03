"""Import handler modules to register all WebSocket routes."""

from app.api.handlers import (
    account, admin, agent, alert_rules, analytics, bots, chart_drawings, journal, market,
    notifications, risk, scanner, trading, vision,
)

__all__ = [
    "account", "admin", "agent", "alert_rules", "analytics", "bots", "chart_drawings", "journal",
    "market", "notifications", "risk", "scanner", "trading", "vision",
]
