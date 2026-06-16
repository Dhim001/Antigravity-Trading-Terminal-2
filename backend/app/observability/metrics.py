"""Lightweight in-process metrics (Prometheus text exposition)."""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_counters: dict[str, float] = {}
_histograms: dict[str, list[float]] = {}


def inc(name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
    key = _key(name, labels)
    with _lock:
        _counters[key] = _counters.get(key, 0.0) + value


def observe(name: str, value: float, labels: dict[str, str] | None = None) -> None:
    key = _key(name, labels)
    with _lock:
        bucket = _histograms.setdefault(key, [])
        bucket.append(float(value))
        if len(bucket) > 500:
            _histograms[key] = bucket[-500:]


def _key(name: str, labels: dict[str, str] | None) -> str:
    if not labels:
        return name
    parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f"{name}{{{parts}}}"


def render_prometheus() -> str:
    lines: list[str] = []
    with _lock:
        for key, val in sorted(_counters.items()):
            metric, labels = _split_key(key)
            lines.append(f"# TYPE {metric} counter")
            lines.append(f"{key} {val}")
        for key, samples in sorted(_histograms.items()):
            if not samples:
                continue
            metric, _ = _split_key(key)
            lines.append(f"# TYPE {metric} summary")
            lines.append(f"{key}_count {len(samples)}")
            lines.append(f"{key}_sum {sum(samples)}")
            sorted_s = sorted(samples)
            for q in (0.5, 0.9, 0.99):
                idx = min(len(sorted_s) - 1, int(q * len(sorted_s)))
                lines.append(f'{key}{{quantile="{q}"}} {sorted_s[idx]}')
    lines.append(f"process_uptime_seconds {time.time() - _START}")
    return "\n".join(lines) + "\n"


def _split_key(key: str) -> tuple[str, str]:
    if "{" in key:
        return key.split("{", 1)[0], key
    return key, key


_START = time.time()
