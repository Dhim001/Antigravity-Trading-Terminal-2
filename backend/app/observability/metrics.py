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


def histogram_quantile(name: str, quantile: float, labels: dict[str, str] | None = None) -> float | None:
    """Return a quantile for the named histogram (in-process samples)."""
    key = _key(name, labels)
    with _lock:
        samples = _histograms.get(key)
        if not samples:
            return None
        sorted_s = sorted(samples)
        idx = min(len(sorted_s) - 1, int(quantile * len(sorted_s)))
        return sorted_s[idx]


def counter_sum(prefix: str) -> float:
    """Sum all counter samples whose key starts with `prefix`."""
    with _lock:
        total = 0.0
        for key, val in _counters.items():
            metric, _ = _split_key(key)
            if metric == prefix or key.startswith(f"{prefix}{{"):
                total += val
        return total


def observability_snapshot() -> dict[str, float | None]:
    """Compact metrics summary for /health and admin UI."""
    return {
        "agent_analyze_p99_sec": histogram_quantile("agent_analyze_duration_seconds", 0.99),
        "agent_analyze_p50_sec": histogram_quantile("agent_analyze_duration_seconds", 0.5),
        "bot_signals_total": counter_sum("bot_signals_total"),
        "bot_orders_blocked_total": counter_sum("bot_orders_blocked_total"),
        "orders_place_total": counter_sum("orders_place_total"),
        "orders_preview_allowed_total": counter_sum("orders_preview_allowed_total"),
        "orders_preview_blocked_total": counter_sum("orders_preview_blocked_total"),
        "ib_bars_received_total": counter_sum("ib_bars_received_total"),
        "ib_reconnects_total": counter_sum("ib_reconnects_total"),
        "ib_stream_errors_total": counter_sum("ib_stream_errors_total"),
        "ib_l1_ticks_total": counter_sum("ib_l1_ticks_total"),
        "massive_bars_received_total": counter_sum("massive_bars_received_total"),
        "massive_trades_received_total": counter_sum("massive_trades_received_total"),
        "massive_quotes_received_total": counter_sum("massive_quotes_received_total"),
        "massive_reconnects_total": counter_sum("massive_reconnects_total"),
        "massive_stream_errors_total": counter_sum("massive_stream_errors_total"),
        "massive_poll_updates_total": counter_sum("massive_poll_updates_total"),
        "ws_client_connects_total": counter_sum("ws_client_connects_total"),
        "ws_client_disconnects_total": counter_sum("ws_client_disconnects_total"),
        "meta_label_blocked_total": counter_sum("meta_label_blocked_total"),
        "meta_label_passed_total": counter_sum("meta_label_passed_total"),
        "meta_label_shadow_would_block_total": counter_sum("meta_label_shadow_would_block_total"),
    }


_START = time.time()
