"""Shared API routing types."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.api.context import RequestContext

ActionHandler = Callable[[RequestContext], Awaitable[None]]


@dataclass
class RouteMeta:
    handler: ActionHandler
    sim_only: bool = False
    sim_denied_message: str = "Action is disabled in live trading mode."
    tags: list[str] = field(default_factory=list)
