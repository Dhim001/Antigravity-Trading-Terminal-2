"""Shared application dependencies for WebSocket and HTTP transports."""

from dataclasses import dataclass
from typing import Any

from app.services.base_oms import BaseOMSService
from app.services.bots.manager import BotManagerService
from app.websocket.connection_manager import ConnectionManager


@dataclass
class AppState:
    oms: BaseOMSService
    manager: ConnectionManager
    bot_manager: BotManagerService
    backtester: Any | None = None
    feed: Any | None = None
    event_bus: Any | None = None
    screener: Any | None = None

    def context_kwargs(self, backtester=None) -> dict:
        """Keyword args for RequestContext / handle_client_message."""
        return {
            "oms": self.oms,
            "manager": self.manager,
            "bot_manager": self.bot_manager,
            "backtester": backtester if backtester is not None else self.backtester,
        }
