"""Trim backtest payloads for WebSocket / browser — full runs stay in DB."""

from __future__ import annotations

import copy
from typing import Any

MAX_WIRE_EQUITY_POINTS = 2000
MAX_WIRE_TRADES = 100
MAX_WIRE_DRAWDOWN_POINTS = 2000
MAX_WIRE_REASONING_TRADES = 50
MAX_WIRE_BLOCKED_EVENTS = 50
MAX_PERSIST_EQUITY_POINTS = 3000
MAX_PERSIST_DRAWDOWN_POINTS = 3000
MAX_PERSIST_TRADES = 2000
MAX_PERSIST_REASONING_TRADES = 100
MAX_PERSIST_BLOCKED_EVENTS = 200


def _downsample_series(items: list, max_points: int) -> list:
    if not items or len(items) <= max_points:
        return items
    step = max(1, (len(items) + max_points - 1) // max_points)
    out = [items[i] for i in range(0, len(items), step)]
    if out[-1] is not items[-1]:
        out.append(items[-1])
    return out


def trim_results_for_wire(results: dict[str, Any] | None) -> dict[str, Any]:
    """Return a shallow copy safe to send to the browser."""
    if not results:
        return {}

    out = copy.copy(results)

    if isinstance(out.get("equity_curve"), list):
        out["equity_curve"] = _downsample_series(out["equity_curve"], MAX_WIRE_EQUITY_POINTS)

    if isinstance(out.get("drawdown_curve"), list):
        out["drawdown_curve"] = _downsample_series(out["drawdown_curve"], MAX_WIRE_DRAWDOWN_POINTS)

    trades = out.get("trades")
    if isinstance(trades, list):
        total = out.get("trades_total") or len(trades)
        out["trades_total"] = total
        if len(trades) > MAX_WIRE_TRADES:
            out["trades"] = trades[-MAX_WIRE_TRADES:]
        else:
            out["trades"] = list(trades)

    summary = out.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("blocked_events"), list):
        events = summary["blocked_events"]
        total_events = summary.get("blocked_events_total") or len(events)
        if len(events) > MAX_WIRE_BLOCKED_EVENTS:
            out["summary"] = {
                **summary,
                "blocked_events": events[-MAX_WIRE_BLOCKED_EVENTS:],
                "blocked_events_total": total_events,
                "blocked_events_truncated": total_events > MAX_WIRE_BLOCKED_EVENTS,
            }

    reasoning = out.get("reasoning")
    if isinstance(reasoning, dict) and isinstance(reasoning.get("trades"), list):
        r_trades = reasoning["trades"]
        reasoning = {**reasoning, "trades": r_trades[:MAX_WIRE_REASONING_TRADES]}
        out["reasoning"] = reasoning

    mc = out.get("monte_carlo")
    if isinstance(mc, dict) and isinstance(mc.get("fan_bands"), list):
        out["monte_carlo"] = {**mc, "fan_bands": mc["fan_bands"][-40:]}

    if out.get("portfolio"):
        symbol_results = out.get("symbol_results")
        if isinstance(symbol_results, list):
            trimmed_rows = []
            for row in symbol_results:
                if not isinstance(row, dict):
                    continue
                trimmed_rows.append({k: v for k, v in row.items() if k != "equity_curve"})
            out["symbol_results"] = trimmed_rows

    bench = out.get("benchmark_overlays")
    if isinstance(bench, dict):
        trimmed_bench = {}
        for key, val in bench.items():
            if isinstance(val, dict) and isinstance(val.get("curve"), list):
                trimmed_bench[key] = {
                    **val,
                    "curve": _downsample_series(val["curve"], MAX_WIRE_EQUITY_POINTS),
                }
            elif isinstance(val, list):
                trimmed_bench[key] = _downsample_series(val, MAX_WIRE_EQUITY_POINTS)
            else:
                trimmed_bench[key] = val
        out["benchmark_overlays"] = trimmed_bench

    return out


def _strip_trade_bloat(trades: list) -> list:
    """Keep compact per-trade explain payloads; drop only empty snapshots."""
    out: list = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        slim = dict(t)
        snap = slim.get("insight_snapshot")
        if snap is not None and not snap:
            slim.pop("insight_snapshot", None)
        out.append(slim)
    return out


def trim_results_for_persist(results: dict[str, Any] | None) -> dict[str, Any]:
    """Downsample arrays for DB storage — trades_total preserves full count."""
    if not results:
        return {}

    out = copy.deepcopy(results)

    if isinstance(out.get("equity_curve"), list):
        out["equity_curve"] = _downsample_series(out["equity_curve"], MAX_PERSIST_EQUITY_POINTS)

    if isinstance(out.get("drawdown_curve"), list):
        out["drawdown_curve"] = _downsample_series(out["drawdown_curve"], MAX_PERSIST_DRAWDOWN_POINTS)

    trades = out.get("trades")
    if isinstance(trades, list):
        total = out.get("trades_total") or len(trades)
        out["trades_total"] = total
        cleaned = _strip_trade_bloat(trades)
        out["trades"] = cleaned[-MAX_PERSIST_TRADES:] if len(cleaned) > MAX_PERSIST_TRADES else cleaned

    reasoning = out.get("reasoning")
    if isinstance(reasoning, dict) and isinstance(reasoning.get("trades"), list):
        out["reasoning"] = {
            **reasoning,
            "trades": reasoning["trades"][:MAX_PERSIST_REASONING_TRADES],
        }

    if out.get("portfolio"):
        per_symbol = out.get("per_symbol")
        if isinstance(per_symbol, dict):
            out["per_symbol"] = {
                sym: {k: v for k, v in row.items() if k != "equity_curve"}
                for sym, row in per_symbol.items()
                if isinstance(row, dict)
            }
        symbol_results = out.get("symbol_results")
        if isinstance(symbol_results, list):
            out["symbol_results"] = [
                {k: v for k, v in row.items() if k != "equity_curve"}
                for row in symbol_results
                if isinstance(row, dict)
            ]

    bench = out.get("benchmark_overlays")
    if isinstance(bench, dict):
        trimmed_bench = {}
        for key, val in bench.items():
            if isinstance(val, dict) and isinstance(val.get("curve"), list):
                trimmed_bench[key] = {
                    **val,
                    "curve": _downsample_series(val["curve"], MAX_PERSIST_EQUITY_POINTS),
                }
            elif isinstance(val, list):
                trimmed_bench[key] = _downsample_series(val, MAX_PERSIST_EQUITY_POINTS)
            else:
                trimmed_bench[key] = val
        out["benchmark_overlays"] = trimmed_bench

    if isinstance(out.get("summary"), dict) and isinstance(out["summary"].get("blocked_events"), list):
        events = out["summary"]["blocked_events"]
        total_events = out["summary"].get("blocked_events_total") or len(events)
        if len(events) > MAX_PERSIST_BLOCKED_EVENTS:
            out["summary"] = {
                **out["summary"],
                "blocked_events": events[-MAX_PERSIST_BLOCKED_EVENTS:],
                "blocked_events_total": total_events,
                "blocked_events_truncated": total_events > MAX_PERSIST_BLOCKED_EVENTS,
            }

    return out
