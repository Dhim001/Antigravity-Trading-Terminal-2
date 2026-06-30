"""Shared retry/backoff for notification delivery adapters."""

from __future__ import annotations

import asyncio
import smtplib
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from app.config import NOTIFICATION_DELIVERY_MAX_RETRIES

T = TypeVar("T")

DEFAULT_DELAYS = (1.0, 3.0, 8.0)


def default_delay(_exc: Exception, attempt: int) -> float:
    return DEFAULT_DELAYS[min(attempt, len(DEFAULT_DELAYS) - 1)]


def rate_limit_delay(exc: Exception, attempt: int) -> float:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        return DEFAULT_DELAYS[min(attempt, len(DEFAULT_DELAYS) - 1)] * 2
    return default_delay(exc, attempt)


def is_transient_http(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError))


def is_transient_smtp(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            smtplib.SMTPServerDisconnected,
            smtplib.SMTPConnectError,
            smtplib.SMTPDataError,
            smtplib.SMTPResponseException,
            TimeoutError,
            OSError,
            ConnectionError,
        ),
    )


def is_transient_push(exc: Exception) -> bool:
    code = None
    try:
        from pywebpush import WebPushException

        if isinstance(exc, WebPushException) and exc.response is not None:
            code = exc.response.status_code
    except Exception:
        pass
    if code in (404, 410):
        return False
    if code in (429, 500, 502, 503, 504):
        return True
    return isinstance(exc, (TimeoutError, OSError, ConnectionError))


async def with_delivery_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    max_retries: int | None = None,
    is_retryable: Callable[[Exception], bool] | None = None,
    delay_for: Callable[[Exception, int], float] | None = None,
) -> T:
    attempts = NOTIFICATION_DELIVERY_MAX_RETRIES if max_retries is None else max(1, max_retries)
    retryable = is_retryable or (lambda _e: True)
    delay_fn = delay_for or default_delay
    last_exc: Exception | None = None

    for attempt in range(attempts):
        try:
            return await operation()
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts - 1 or not retryable(exc):
                raise
            await asyncio.sleep(delay_fn(exc, attempt))

    raise last_exc or RuntimeError("Delivery failed")
