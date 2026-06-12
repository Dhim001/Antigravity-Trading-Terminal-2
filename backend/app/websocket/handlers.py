import json
import logging

from app.api.context import RequestContext
from app.api.responses import send_error
from app.api.router import dispatch, ensure_routes_loaded
from app.api.state import AppState
from app.websocket.connection_manager import _is_disconnect_error

ensure_routes_loaded()

logger = logging.getLogger(__name__)


async def handle_client_message(websocket, message_str, app_state: AppState):
    """Parse client JSON and dispatch to the centralized action router."""
    try:
        message = json.loads(message_str)
        action = message.get("action")
        logger.info("Received action: %s from client", action)
        message["_rate_key"] = f"ws:{id(websocket)}"

        ctx = RequestContext(
            websocket=websocket,
            message=message,
            action=action,
            **app_state.context_kwargs(),
        )
        await dispatch(ctx)
    except Exception as exc:
        if _is_disconnect_error(exc):
            logger.debug("Client disconnected while handling message.")
            return
        logger.error("Error processing client message: %s", exc)
        ctx = RequestContext(
            websocket=websocket,
            message={"_rate_key": f"ws:{id(websocket)}"},
            action=None,
            **app_state.context_kwargs(),
        )
        await send_error(ctx, f"Request processing failed: {exc}")
