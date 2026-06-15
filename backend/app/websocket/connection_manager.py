import logging

import websockets.exceptions

from app.api.wire_codec import encode_wire_payload

logger = logging.getLogger(__name__)

def _is_disconnect_error(exc: BaseException) -> bool:
    return isinstance(
        exc,
        (
            websockets.exceptions.ConnectionClosed,
            websockets.exceptions.ConnectionClosedError,
            websockets.exceptions.ConnectionClosedOK,
        ),
    )

class ConnectionManager:
    def __init__(self):
        self.connected_clients = set()
        self.client_symbols = {}  # websocket -> subscribed chart symbol

    def register(self, websocket):
        logging.info("New client connection registered.")
        self.connected_clients.add(websocket)

    def unregister(self, websocket):
        logging.info("Client connection unregistered.")
        if websocket in self.connected_clients:
            self.connected_clients.remove(websocket)
        self.client_symbols.pop(websocket, None)

    def set_client_symbol(self, websocket, symbol: str):
        if symbol:
            self.client_symbols[websocket] = symbol

    async def broadcast(self, payload):
        """Broadcasts a wire payload to all registered clients (JSON or MessagePack)."""
        if not self.connected_clients:
            return
        message = encode_wire_payload(payload)
        dead = []
        for client in list(self.connected_clients):
            try:
                await client.send(message)
            except Exception as exc:
                if not _is_disconnect_error(exc):
                    logger.warning("Broadcast send failed: %s", exc)
                dead.append(client)
        for client in dead:
            self.unregister(client)

    async def send_to(self, websocket, payload) -> bool:
        """Sends a wire payload to a specific client. Returns False if disconnected."""
        try:
            await websocket.send(encode_wire_payload(payload))
            return True
        except Exception as exc:
            if _is_disconnect_error(exc):
                logger.debug("Client disconnected before send completed.")
            else:
                logger.warning("Send to client failed: %s", exc)
            self.unregister(websocket)
            return False
