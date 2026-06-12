"""Centralized API routing for WebSocket actions."""

from app.api.router import dispatch, list_routes, route

__all__ = ["dispatch", "list_routes", "route"]
