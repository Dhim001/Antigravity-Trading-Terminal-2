from __future__ import annotations
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from app.services.base_oms import BaseOMSService
from app.websocket.connection_manager import ConnectionManager

if TYPE_CHECKING:
    from app.services.bots.manager import BotManagerService


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
