"""Shared execution kernel — parity gates and execution-chain logging.

Used by the bar-close backtester today; live BotManager can adopt the same
parity helpers incrementally so backtest and live share one gate vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.bots.backtest_parity import htf_bias_at_time
from app.services.bots.strategies_chart_agent import classify_filter_reject


@dataclass
class ParityBlock:
    kind: str
    reason: str
    side: str | None = None
    signal: str | None = None
    bucket: str | None = None


@dataclass
class ParityGateOutcome:
    signal: str | None
    block: ParityBlock | None = None


def apply_indicator_parity_gates(
    signal: str | None,
    *,
    row: dict,
    bar_time,
    live_parity: bool,
    strat_key: str,
    confirm_tf: str,
    htf_bias_lookup: list[tuple[int, str]],
    strat_filter,
) -> ParityGateOutcome:
    """Mirror live HTF + filter gates for non-CHART_AGENT strategies."""
    if not live_parity or strat_key == "CHART_AGENT" or signal not in ("BUY", "SELL"):
        return ParityGateOutcome(signal=signal)

    if confirm_tf and htf_bias_lookup:
        bias = htf_bias_at_time(htf_bias_lookup, bar_time)
        if signal == "BUY" and bias == "BEAR":
            return ParityGateOutcome(
                signal=None,
                block=ParityBlock(
                    kind="parity_htf",
                    reason=f"HTF bias {bias} blocks BUY",
                    side="BUY",
                    signal="BUY",
                ),
            )
        if signal == "SELL" and bias == "BULL":
            return ParityGateOutcome(
                signal=None,
                block=ParityBlock(
                    kind="parity_htf",
                    reason=f"HTF bias {bias} blocks SELL",
                    side="SELL",
                    signal="SELL",
                ),
            )

    if signal and strat_filter:
        allowed, reject_reason = strat_filter.evaluate_gate(row, signal)
        if not allowed:
            return ParityGateOutcome(
                signal=None,
                block=ParityBlock(
                    kind="parity_filter",
                    reason=reject_reason or "Strategy filter blocked entry",
                    signal=signal,
                ),
            )

    return ParityGateOutcome(signal=signal)


def chart_filter_reject_block(signal_data: dict | None, bar_time=None) -> ParityBlock | None:
    if not signal_data:
        return None
    reason = signal_data.get("reject_reason")
    if not reason:
        return None
    bucket = classify_filter_reject(reason)
    return ParityBlock(
        kind="filter",
        reason=str(reason)[:240],
        signal=(signal_data or {}).get("signal"),
        bucket=bucket,
    )


class ExecutionChain:
    """Event-sourced signal → intent → fill chain for parity diffing."""

    def __init__(self, bar_time) -> None:
        self._bar_time = int(bar_time) if bar_time is not None else 0
        self._events: list[dict[str, Any]] = []

    def record(self, stage: str, *, ok: bool, **detail: Any) -> None:
        payload = {k: v for k, v in detail.items() if v is not None}
        self._events.append({
            "stage": stage,
            "ok": bool(ok),
            "time": self._bar_time,
            **payload,
        })

    def to_list(self) -> list[dict[str, Any]]:
        return list(self._events)
