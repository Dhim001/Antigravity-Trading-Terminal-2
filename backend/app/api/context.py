from dataclasses import dataclass
from typing import Any

from app.services.base_oms import BaseOMSService
from app.services.bots.manager import BotManagerService
from app.websocket.connection_manager import ConnectionManager


@dataclass
class RequestContext:
    websocket: Any
    manager: ConnectionManager
    oms: BaseOMSService
    bot_manager: BotManagerService
    backtester: Any | None
    chart_analyst: Any | None
    message: dict
    action: str | None
    aborted: bool = False
