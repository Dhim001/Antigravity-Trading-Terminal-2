"""Bounded in-memory cache for loaded ML models / ONNX sessions.

MEMORY_CENTRIC_REVIEW #2 — process-global stores must not retain every
symbol×version forever. Reload from disk on miss is cheap (joblib/ONNX).
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any


class ModelCacheLru:
    """Track cache keys with LRU eviction + optional idle TTL.

    Call :meth:`touch` after a successful get/put. When a key is dropped,
    ``on_evict(key)`` removes the payload from the store's parallel dicts.
    """

    def __init__(
        self,
        max_entries: int = 12,
        ttl_sec: float | None = 3600.0,
        on_evict: Callable[[str], None] | None = None,
    ) -> None:
        self.max_entries = max(1, int(max_entries))
        ttl = float(ttl_sec) if ttl_sec is not None else 0.0
        self.ttl_sec: float | None = ttl if ttl > 0 else None
        self.on_evict = on_evict
        self._order: OrderedDict[str, float] = OrderedDict()

    def __len__(self) -> int:
        return len(self._order)

    def touch(self, key: str) -> None:
        now = time.time()
        self._order.pop(key, None)
        self._order[key] = now
        self._trim(now)

    def discard(self, key: str) -> None:
        """Remove tracking only (caller already deleted payloads)."""
        self._order.pop(key, None)

    def discard_prefix(self, prefix: str) -> list[str]:
        """Drop keys equal to ``prefix`` or ``prefix|…`` (avoid BTC matching BTCUSDT)."""
        needle = str(prefix or "")
        if not needle:
            return []
        removed = [
            k for k in list(self._order)
            if k == needle or k.startswith(needle + "|")
        ]
        for k in removed:
            self._evict_one(k)
        return removed

    def clear(self) -> None:
        keys = list(self._order)
        self._order.clear()
        if self.on_evict:
            for k in keys:
                try:
                    self.on_evict(k)
                except Exception:
                    pass

    def _trim(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if self.ttl_sec is not None:
            expired = [k for k, ts in self._order.items() if now - ts > self.ttl_sec]
            for k in expired:
                self._evict_one(k)
        while len(self._order) > self.max_entries:
            oldest = next(iter(self._order))
            self._evict_one(oldest)

    def _evict_one(self, key: str) -> None:
        self._order.pop(key, None)
        if self.on_evict:
            try:
                self.on_evict(key)
            except Exception:
                pass


def bind_dict_cache(
    *dicts: dict[str, Any],
    max_entries: int = 12,
    ttl_sec: float | None = 3600.0,
) -> ModelCacheLru:
    """LRU that pops ``key`` from every parallel store dict on eviction.

    Best-effort: closes ONNX Runtime ``InferenceSession`` objects so native
    RSS is released (MEMORY_CENTRIC_REVIEW #2).
    """

    def _close_session(obj: Any) -> None:
        if obj is None:
            return
        for meth in ("end_session", "close"):
            fn = getattr(obj, meth, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
                return

    def _evict(key: str) -> None:
        for d in dicts:
            try:
                val = d.pop(key, None)
            except Exception:
                val = None
            if val is None:
                continue
            # Common shapes: session, (model, session), {"session": ...}
            if isinstance(val, tuple):
                for part in val:
                    _close_session(part)
            elif isinstance(val, dict):
                _close_session(val.get("session") or val.get("onnx") or val.get("ort"))
            else:
                _close_session(val)

    return ModelCacheLru(max_entries=max_entries, ttl_sec=ttl_sec, on_evict=_evict)
