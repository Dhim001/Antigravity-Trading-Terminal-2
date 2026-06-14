"""Shared HTTP outcome classification for live broker OMS (at-most-once discipline)."""

from __future__ import annotations

from typing import Any

import requests

from app.config import TERMINAL_MODE
from app.services.reconciliation import record_ambiguous_order


def network_ambiguous(exc: Exception) -> dict[str, str]:
    return {
        "status": "ambiguous",
        "message": (
            f"Order outcome unknown (network error: {exc}). "
            "Do not resend — reconcile via broker portfolio before retrying."
        ),
    }


def classify_http_status(status_code: int, body_text: str = "") -> dict[str, str] | None:
    """Return error/ambiguous dict for non-success HTTP codes, or None if caller should parse body."""
    if status_code == 401:
        return {"status": "error", "message": "Broker credentials invalid — reconnect required."}
    if status_code == 429:
        return {"status": "error", "message": f"Broker rate-limited order: {body_text[:200]}"}
    if 400 <= status_code < 500:
        detail = body_text[:300] if body_text else f"HTTP {status_code}"
        return {"status": "error", "message": f"Broker rejected order ({status_code}): {detail}"}
    if status_code >= 500:
        return {
            "status": "ambiguous",
            "message": (
                f"Broker server error ({status_code}). Outcome unknown — "
                "do not resend; reconcile via open orders/positions."
            ),
        }
    return None


def record_ambiguous_if_needed(order_req: dict, result: dict[str, Any]) -> None:
    if result.get("status") != "ambiguous":
        return
    record_ambiguous_order(
        order_req,
        result.get("message", "Ambiguous broker order"),
        broker=TERMINAL_MODE,
        bot_id=order_req.get("bot_id"),
    )


def request_exception_outcome(exc: Exception) -> dict[str, str]:
    if isinstance(exc, requests.Timeout):
        return network_ambiguous(exc)
    if isinstance(exc, requests.RequestException):
        return network_ambiguous(exc)
    return {"status": "error", "message": str(exc)}
