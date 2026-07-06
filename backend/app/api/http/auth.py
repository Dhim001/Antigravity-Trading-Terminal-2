"""Optional API-key gate for the HTTP REST layer."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Require X-API-Key when HTTP_API_KEY is configured. /health stays public."""

    PUBLIC_PATHS = frozenset({"/health", "/health/live", "/health/massive"})

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        if not self.api_key or request.url.path in self.PUBLIC_PATHS:
            return await call_next(request)

        provided = request.headers.get("x-api-key", "")
        if provided != self.api_key:
            return JSONResponse(
                {"ok": False, "error": "Unauthorized — invalid or missing X-API-Key"},
                status_code=401,
            )

        return await call_next(request)
