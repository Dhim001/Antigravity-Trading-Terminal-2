import asyncio
import logging
import time

import websockets.exceptions

from app.api.wire_codec import encode_wire_payload

logger = logging.getLogger(__name__)

# Slow/dead Electron renderers (or abandoned smoke clients) must not stall the
# feed broadcast loop — serial await client.send() backpressure freezes live UI.
_BROADCAST_SEND_TIMEOUT_SEC = 0.5
_SEND_TO_TIMEOUT_SEC = 2.0


def _is_disconnect_error(exc: BaseException) -> bool:
    return isinstance(
        exc,
        (
            websockets.exceptions.ConnectionClosed,
            websockets.exceptions.ConnectionClosedError,
            websockets.exceptions.ConnectionClosedOK,
            asyncio.TimeoutError,
            TimeoutError,
        ),
    )


class ConnectionManager:
    def __init__(self):
        self.connected_clients = set()
        self.client_symbols = {}  # websocket -> subscribed chart symbol
        # Strong refs required — bare create_task() can be GC'd before send runs.
        self._send_tasks: set[asyncio.Task] = set()
        self._broadcast_stats = {
            "clients": 0,
            "last_enqueue_ts": None,
            "last_drop_ts": None,
            "enqueued": 0,
            "drops": 0,
            "in_flight": 0,
        }

    @property
    def broadcast_stats(self) -> dict:
        self._broadcast_stats["clients"] = len(self.connected_clients)
        self._broadcast_stats["in_flight"] = len(self._send_tasks)
        return dict(self._broadcast_stats)

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

    def _spawn_send(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._send_tasks.add(task)
        task.add_done_callback(self._send_tasks.discard)
        return task

    async def broadcast(self, payload):
        """Fan-out without blocking the feed on any single slow peer.

        Sends run as tracked background tasks with a short timeout so the Alpaca
        coalescing loop never waits on Electron/TCP backpressure.
        """
        clients = list(self.connected_clients)
        if not clients:
            return
        message = encode_wire_payload(payload)
        self._broadcast_stats["last_enqueue_ts"] = time.time()
        self._broadcast_stats["clients"] = len(clients)
        self._broadcast_stats["enqueued"] = int(self._broadcast_stats.get("enqueued") or 0) + 1

        for client in clients:
            self._spawn_send(self._send_one(client, message))

    async def _send_one(self, client, message) -> None:
        try:
            await asyncio.wait_for(client.send(message), timeout=_BROADCAST_SEND_TIMEOUT_SEC)
        except Exception as exc:
            if not _is_disconnect_error(exc):
                logger.warning("Broadcast send failed: %s", exc)
            else:
                logger.debug("Broadcast dropped slow/dead client: %s", exc)
            self._broadcast_stats["drops"] = int(self._broadcast_stats.get("drops") or 0) + 1
            self._broadcast_stats["last_drop_ts"] = time.time()
            self.unregister(client)

    async def send_to(self, websocket, payload) -> bool:
        """Sends a wire payload to a specific client. Returns False if disconnected."""
        try:
            await asyncio.wait_for(
                websocket.send(encode_wire_payload(payload)),
                timeout=_SEND_TO_TIMEOUT_SEC,
            )
            return True
        except Exception as exc:
            if _is_disconnect_error(exc):
                logger.debug("Client disconnected/slow before send completed.")
            else:
                logger.warning("Send to client failed: %s", exc)
            self.unregister(websocket)
            return False
