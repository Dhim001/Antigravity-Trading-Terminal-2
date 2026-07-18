"""Detect strategies that cannot produce trades (stubs, missing data, zero signals).

Attached to backtest results as ``strategy_readiness`` so Backtest Lab can warn early.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


# Static notes shown even when the run produces trades (data-mode caveats).
STRATEGY_TRADE_NOTES: dict[str, list[str]] = {
    "CVD_DIVERGENCE": [
        "CVD is approximated from OHLCV (Close Location Value), not exchange trade CVD.",
    ],
    "ORDERFLOW_IMBALANCE": [
        "Backtest uses a candle pressure proxy; live prefers L2 orderbook when available.",
    ],
    "ABSORPTION_AGENT": [
        "Only the volume-absorption domain is implemented (not full multi-domain scoring).",
    ],
    "CHART_AGENT": [
        "Live path needs a chart-analyst insight cache; backtest uses bar-by-bar scoring.",
    ],
    "MARKET_MAKING": [
        "Bar-close spread proxy — not continuous quoting / inventory MM.",
    ],
}


def static_trade_notes(strategy: str | None) -> list[str]:
    key = str(strategy or "").upper()
    return list(STRATEGY_TRADE_NOTES.get(key) or [])


def _tally_blocked_kinds(blocked_events: list[dict] | None) -> list[tuple[str, int]]:
    if not blocked_events:
        return []
    c: Counter[str] = Counter()
    for ev in blocked_events:
        if not isinstance(ev, dict):
            continue
        kind = str(ev.get("kind") or "other")
        c[kind] += 1
    return c.most_common(5)


def _tally_blocked_reasons(blocked_events: list[dict] | None) -> list[tuple[str, int]]:
    if not blocked_events:
        return []
    c: Counter[str] = Counter()
    for ev in blocked_events:
        if not isinstance(ev, dict):
            continue
        reason = str(ev.get("reason") or "").strip()
        if not reason:
            continue
        c[reason] += 1
    return c.most_common(5)


def build_strategy_readiness(
    strategy: str | None,
    *,
    trade_count: int,
    bars_evaluated: int,
    signal_counts: dict[str, int] | None = None,
    reject_reasons: Counter | dict[str, int] | None = None,
    evaluate_errors: int = 0,
    blocked_entries: int = 0,
    blocked_events: list[dict] | None = None,
    direction_mode: str | None = None,
    sim_mode: str | None = None,
) -> dict[str, Any]:
    """Summarize whether the strategy produced actionable signals this run."""
    key = str(strategy or "").upper()
    counts = {str(k).upper(): int(v) for k, v in (signal_counts or {}).items()}
    buy = counts.get("BUY", 0)
    sell = counts.get("SELL", 0)
    close = counts.get("CLOSE", 0)
    none = counts.get("NONE", 0)
    directional = buy + sell
    notes = static_trade_notes(key)
    mode = str(direction_mode or "LONG_ONLY").upper()
    sim = str(sim_mode or "live_aligned").lower()

    if isinstance(reject_reasons, Counter):
        top_rejects = reject_reasons.most_common(5)
    elif isinstance(reject_reasons, dict):
        top_rejects = sorted(reject_reasons.items(), key=lambda kv: -int(kv[1]))[:5]
    else:
        top_rejects = []

    top_blocks = _tally_blocked_kinds(blocked_events)
    top_block_reasons = _tally_blocked_reasons(blocked_events)
    warnings: list[str] = []
    status = "ok"

    if evaluate_errors > 0:
        status = "broken"
        warnings.append(
            f"Strategy evaluate() raised {evaluate_errors} error(s) — "
            "check reject reasons for 'evaluate error'."
        )

    if bars_evaluated >= 50 and directional == 0 and int(trade_count or 0) == 0:
        status = "no_signals" if status == "ok" else status
        warnings.append(
            f"No BUY/SELL signals across {bars_evaluated} evaluated bars "
            f"({none} NONE). This strategy will not open trades in this run."
        )
        if top_rejects:
            top = ", ".join(f"{r} ({n})" for r, n in top_rejects[:3])
            warnings.append(f"Top reject reasons: {top}")
    elif int(trade_count or 0) == 0 and directional > 0:
        status = "signals_blocked" if status == "ok" else status
        warnings.append(
            f"{directional} directional signal(s) but 0 fills "
            f"(BUY {buy} · SELL {sell})."
        )
        # Direction mode is the most common cause when short-heavy.
        if mode == "LONG_ONLY" and sell > 0 and buy == 0:
            warnings.append(
                "direction_mode=LONG_ONLY — all signals were SELL/short; "
                "set Trade direction to BOTH (or sim_mode=research) to allow shorts."
            )
        elif mode == "LONG_ONLY" and sell > buy * 2 and buy > 0:
            warnings.append(
                f"direction_mode=LONG_ONLY ignored {sell} SELL signal(s); "
                f"only {buy} BUY could open. Set BOTH to trade shorts, or keep LONG_ONLY "
                "and investigate why BUY entries did not fill."
            )
        elif mode == "SHORT_ONLY" and buy > 0 and sell == 0:
            warnings.append(
                "direction_mode=SHORT_ONLY — all signals were BUY/long; "
                "set Trade direction to BOTH to allow longs."
            )

        if int(blocked_entries or 0) > 0:
            warnings.append(f"{int(blocked_entries)} entry attempt(s) were blocked.")
        if top_block_reasons:
            top = "; ".join(f"{r} ({n})" for r, n in top_block_reasons[:2])
            warnings.append(f"Top block reason: {top}")
        elif top_blocks:
            top = ", ".join(f"{k} ({n})" for k, n in top_blocks[:3])
            warnings.append(f"Top block kinds: {top}")
        elif not top_blocks and buy > 0 and mode in ("LONG_ONLY", "BOTH"):
            warnings.append(
                "No block events recorded — check sizing (stop distance), "
                "allocation, or risk gates in live-aligned mode."
            )
        if sim == "live_aligned" and mode == "LONG_ONLY" and sell > buy:
            warnings.append(
                "Tip: Research sim_mode allows shorts without changing direction_mode."
            )

    # Surface static caveats as informational notes (not failures).
    info = list(notes)

    return {
        "strategy": key or None,
        "status": status,
        "ok": status == "ok",
        "trade_count": int(trade_count or 0),
        "bars_evaluated": int(bars_evaluated or 0),
        "signals": {
            "BUY": buy,
            "SELL": sell,
            "CLOSE": close,
            "NONE": none,
            "directional": directional,
        },
        "direction_mode": mode,
        "sim_mode": sim,
        "blocked_entries": int(blocked_entries or 0),
        "top_block_kinds": [{"kind": str(k), "count": int(n)} for k, n in top_blocks],
        "top_block_reasons": [
            {"reason": str(r), "count": int(n)} for r, n in top_block_reasons
        ],
        "top_reject_reasons": [
            {"reason": str(r), "count": int(n)} for r, n in top_rejects
        ],
        "evaluate_errors": int(evaluate_errors or 0),
        "warnings": warnings,
        "notes": info,
        "message": warnings[0] if warnings else (
            info[0] if info and int(trade_count or 0) == 0 else None
        ),
    }
