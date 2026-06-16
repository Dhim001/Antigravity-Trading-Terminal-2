"""Shared rate-limit helper — Redis when available, in-process fallback."""

from __future__ import annotations

import time
from typing import Callable

from app.config import REDIS_URL

_local_last: dict[str, float] = {}
_redis_client = None


def _redis():
    global _redis_client
    if not REDIS_URL:
        return None
    if _redis_client is None:
        try:
            import redis

            _redis_client = redis.from_url(REDIS_URL)
        except Exception:
            return None
    return _redis_client


def rate_limit_allow(key: str, min_interval_sec: float) -> bool:
    """
    Return True if the action is allowed (not rate-limited).
    Uses Redis SET NX with TTL when REDIS_URL is set; else per-process dict.
    """
    client = _redis()
    if client is not None:
        try:
            redis_key = f"terminal:ratelimit:{key}"
            return bool(client.set(redis_key, "1", nx=True, ex=max(1, int(min_interval_sec))))
        except Exception:
            pass

    now = time.monotonic()
    last = _local_last.get(key, 0.0)
    if now - last < min_interval_sec:
        return False
    _local_last[key] = now
    return True


def reset_local_rate_limits() -> None:
    """Test helper."""
    _local_last.clear()
