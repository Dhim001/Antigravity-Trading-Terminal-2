"""Post-hoc LLM reasoning for completed backtests — never affects signals."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from app.config import BACKTEST_REASONING_MAX_TRADES
from app.services.agent.llm.router import is_llm_available, summarize_backtest_entry

logger = logging.getLogger(__name__)

RUN_KIND_LABELS = {
    "single": "Standard backtest",
    "sweep": "Parameter sweep (best config)",
    "walk_forward": "Walk-forward OOS validation",
}


def _trade_bar_time(trade: dict) -> int | None:
    raw = trade.get("bar_time") or trade.get("time") or trade.get("timestamp")
    if raw is None:
        return None
    try:
        value = int(raw)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _format_bar_time_iso(bar_time: int | None) -> str | None:
    if bar_time is None:
        return None
    try:
        return datetime.fromtimestamp(bar_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (OSError, OverflowError, ValueError):
        return None


def _entry_trades(trades: list[dict], max_trades: int) -> list[tuple[int, dict]]:
    entries: list[tuple[int, dict]] = []
    for idx, trade in enumerate(trades):
        if trade.get("is_exit"):
            continue
        entries.append((idx, trade))
    if len(entries) > max_trades:
        entries = entries[-max_trades:]
    return entries


def _run_scope_label(
    run_kind: str,
    *,
    train_pct: float | None = None,
    configs_tested: int | None = None,
) -> str:
    if run_kind == "walk_forward":
        pct = int(train_pct) if train_pct is not None else 70
        return f"Walk-forward out-of-sample window (trained on first {pct}% of bars)"
    if run_kind == "sweep":
        n = configs_tested or 0
        return f"Best parameter set from sweep ({n} configs tested)" if n else "Best parameter set from sweep"
    return "Standard backtest simulation"


async def generate_backtest_reasoning(
    trades: list[dict],
    *,
    symbol: str,
    strategy: str,
    model: str | None = None,
    max_trades: int | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
    run_kind: str = "single",
    train_pct: float | None = None,
    configs_tested: int | None = None,
) -> dict[str, Any]:
    """
    Batch narrate entry trades after backtest completes.
    Rules-only simulation is unchanged; this adds optional metadata.
    """
    scope = _run_scope_label(run_kind, train_pct=train_pct, configs_tested=configs_tested)
    base_meta = {
        "requested": True,
        "run_kind": run_kind,
        "run_kind_label": RUN_KIND_LABELS.get(run_kind, run_kind),
        "scope": scope,
    }

    if not await is_llm_available():
        return {
            **base_meta,
            "available": False,
            "trades": [],
            "error": "No LLM provider available — check Ollama or OpenRouter in Settings",
        }

    cap = max_trades if max_trades is not None else BACKTEST_REASONING_MAX_TRADES
    targets = _entry_trades(trades or [], cap)
    if not targets:
        return {
            **base_meta,
            "available": True,
            "trades": [],
            "message": "No entry trades to explain",
        }

    out_trades: list[dict[str, Any]] = []
    resolved_panel_model = model
    resolved_panel_provider: str | None = None
    total = len(targets)
    seen_narratives: set[str] = set()

    for i, (idx, trade) in enumerate(targets):
        if progress_cb:
            progress_cb(i + 1, total, f"Explaining entry {i + 1}/{total}…")

        bar_time = _trade_bar_time(trade)
        entry_reason = trade.get("reason") or "ENTRY"
        insight = trade.get("insight_snapshot")
        if isinstance(insight, str):
            try:
                insight = json.loads(insight)
            except json.JSONDecodeError:
                insight = None
        bundle = {
            "symbol": symbol,
            "strategy": strategy,
            "run_kind": run_kind,
            "run_scope": scope,
            "entry_ordinal": i + 1,
            "entries_in_batch": total,
            "trade_index": idx,
            "signal": trade.get("side"),
            "trade_context": {
                "side": trade.get("side"),
                "price": trade.get("price"),
                "quantity": trade.get("quantity"),
                "bar_time": bar_time,
                "bar_time_iso": _format_bar_time_iso(bar_time),
                "reason": entry_reason,
                "pnl": trade.get("pnl"),
            },
        }
        if insight:
            bundle["insight"] = insight
            bundle["analyst_signal"] = insight.get("signal")
            bundle["analyst_confidence"] = insight.get("confidence")
            bundle["analyst_reasons"] = (insight.get("reasons") or [])[:3]
            bundle["sub_reports"] = insight.get("sub_reports")
        narrative, resolved_model, provider = await summarize_backtest_entry(
            bundle,
            model=model,
        )

        if not narrative:
            logger.warning(
                "Backtest reasoning empty narrative trade_index=%s model=%s provider=%s side=%s",
                idx,
                resolved_model,
                provider,
                trade.get("side"),
            )

        if narrative:
            norm = narrative.strip().lower()
            if norm in seen_narratives:
                narrative = f"{narrative.rstrip('.')} (entry #{i + 1}, {entry_reason})."
            else:
                seen_narratives.add(norm)

        if resolved_model and not resolved_panel_model:
            resolved_panel_model = resolved_model
        if provider and not resolved_panel_provider:
            resolved_panel_provider = provider

        out_trades.append({
            "trade_index": idx,
            "entry_ordinal": i + 1,
            "side": trade.get("side"),
            "price": trade.get("price"),
            "quantity": trade.get("quantity"),
            "reason": entry_reason,
            "time": bar_time,
            "bar_time": bar_time,
            "narrative": narrative,
            "model": resolved_model,
            "provider": provider,
            "insight_snapshot": insight if isinstance(insight, dict) else None,
        })

    return {
        **base_meta,
        "available": True,
        "model": resolved_panel_model,
        "provider": resolved_panel_provider,
        "trade_count": len(out_trades),
        "trades": out_trades,
    }
