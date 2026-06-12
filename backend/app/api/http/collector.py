"""Collect WebSocket-style replies for HTTP transport."""

from __future__ import annotations


class HttpConnectionManager:
    """Minimal ConnectionManager stand-in that records outbound payloads."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.client_symbols: dict = {}

    async def send_to(self, _websocket, payload: dict) -> bool:
        self.messages.append(payload)
        return True

    async def broadcast(self, payload: dict) -> None:
        self.messages.append(payload)

    def set_client_symbol(self, _websocket, symbol: str) -> None:
        if symbol:
            self.client_symbols[symbol] = symbol
