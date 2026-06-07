import logging
import json
import asyncio

class ConnectionManager:
    def __init__(self):
        self.connected_clients = set()

    def register(self, websocket):
        logging.info("New client connection registered.")
        self.connected_clients.add(websocket)

    def unregister(self, websocket):
        logging.info("Client connection unregistered.")
        if websocket in self.connected_clients:
            self.connected_clients.remove(websocket)

    async def broadcast(self, payload):
        """Broadcasts a JSON-serializable payload to all registered clients."""
        if not self.connected_clients:
            return
        message = json.dumps(payload)
        await asyncio.gather(
            *[client.send(message) for client in self.connected_clients],
            return_exceptions=True
        )

    async def send_to(self, websocket, payload):
        """Sends a JSON-serializable payload to a specific client."""
        await websocket.send(json.dumps(payload))
