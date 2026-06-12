"""Central WebSocket action registry and dispatcher."""

from __future__ import annotations

import logging

from app.api.context import RequestContext
from app.api.middleware import dispatch_with_middleware
from app.api.responses import send_error, send_order_result
from app.api.types import ActionHandler, RouteMeta
from app.config import TERMINAL_MODE

logger = logging.getLogger(__name__)

ROUTES: dict[str, RouteMeta] = {}


def route(
    action: str,
    *,
    sim_only: bool = False,
    sim_denied_message: str = "Action is disabled in live trading mode.",
    tags: list[str] | None = None,
):
    """Register a WebSocket action handler."""

    def decorator(fn: ActionHandler) -> ActionHandler:
        if action in ROUTES:
            raise ValueError(f"Duplicate route registration for action: {action}")
        ROUTES[action] = RouteMeta(
            handler=fn,
            sim_only=sim_only,
            sim_denied_message=sim_denied_message,
            tags=tags or [],
        )
        return fn

    return decorator


def list_routes() -> dict[str, RouteMeta]:
    """Return registered routes (copy for introspection/tests)."""
    return dict(ROUTES)


async def dispatch(ctx: RequestContext) -> None:
    action = ctx.action
    if not action:
        await send_error(ctx, "Missing action")
        return

    meta = ROUTES.get(action)
    if meta is None:
        await send_error(ctx, f"Unknown action: {action}")
        return

    if meta.sim_only and TERMINAL_MODE != "SIMULATED":
        await send_order_result(ctx, {"status": "error", "message": meta.sim_denied_message})
        return

    async def _run(ctx_: RequestContext, meta_: RouteMeta) -> None:
        await meta_.handler(ctx_)

    await dispatch_with_middleware(ctx, meta, handler=_run)


def ensure_routes_loaded() -> None:
    """Import handler modules so @route decorators populate ROUTES."""
    from app.api import handlers  # noqa: F401
