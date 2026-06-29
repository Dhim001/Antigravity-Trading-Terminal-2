"""Startup reconciliation — journal scan, broker reconcile, safe mode entry."""

from __future__ import annotations

import logging
from typing import Any

from app.config import TERMINAL_MODE
from app.services.bots import signal_ledger
from app.services.reconciliation import auto_reconcile_with_portfolio, list_ambiguous_orders
from app.services.runtime import system_state

logger = logging.getLogger(__name__)


async def run_startup_recovery(
    oms,
    bot_manager,
    *,
    restore_checkpoint: bool = True,
) -> dict[str, Any]:
    """
    Scan fill journal and ambiguous orders before bots trade.
    Enters safe mode after unclean shutdown or unresolved discrepancies.
    """
    unclean = system_state.was_unclean_shutdown()
    orphaned = signal_ledger.reconcile_orphaned_claims()
    incomplete = signal_ledger.list_incomplete_signals()
    ambiguous_count = len(list_ambiguous_orders(include_resolved=False))

    broker_reconciled = 0

    if incomplete and oms is not None and hasattr(oms, "get_trade_history"):
        try:
            from app.services.bots.execution_mode import uses_paper_oms

            if not uses_paper_oms():
                broker_reconciled = await bot_manager.reconcile_pending_fills()
                auto_result = auto_reconcile_with_portfolio(oms)
                broker_reconciled += int(auto_result.get("matched") or 0)
        except Exception as exc:
            logger.error("Startup broker reconciliation failed: %s", exc)

    incomplete_after = signal_ledger.list_incomplete_signals()
    ambiguous_after = len(list_ambiguous_orders(include_resolved=False))

    for entry in incomplete_after:
        if entry.get("status") == "submitted":
            signal_id = entry.get("signal_id")
            if signal_id:
                signal_ledger.mark_signal_ambiguous(
                    signal_id,
                    "submitted but unconfirmed after startup reconciliation",
                    order_id=entry.get("order_id"),
                )

    incomplete_final = signal_ledger.list_incomplete_signals()
    journal_reconciled = max(0, len(incomplete) - len(incomplete_final))

    should_safe_mode = unclean or bool(incomplete_final) or ambiguous_after > 0

    if should_safe_mode:
        reasons = []
        if unclean:
            reasons.append("unclean_shutdown")
        if incomplete_final:
            reasons.append("incomplete_journal")
        if ambiguous_after:
            reasons.append("ambiguous_orders")
        reason = "startup_recovery:" + ",".join(reasons)
        system_state.enter_safe_mode(
            reason,
            details={
                "unclean_shutdown": unclean,
                "orphaned_claims": orphaned,
                "incomplete_journal": len(incomplete_final),
                "ambiguous_orders": ambiguous_after,
                "terminal_mode": TERMINAL_MODE,
            },
        )
        try:
            from app.services.notifications.dispatcher import emit_notification
            from app.services.notifications.events import NotificationEvent
            from app.services.notifications import types as ntypes

            await emit_notification(
                NotificationEvent(
                    event_type=ntypes.SAFE_MODE,
                    title="Safe mode active",
                    body=f"System started in safe mode ({reason}). All bots paused until operator confirms.",
                    severity="warn",
                    payload={"reason": reason},
                )
            )
        except Exception:
            pass
        paused = bot_manager.apply_safe_mode_pause()
        logger.warning(
            "Safe mode active (%s) — paused %d bot(s). Operator confirmation required.",
            reason,
            paused,
        )
    elif restore_checkpoint:
        checkpoint = system_state.load_bot_runtime_checkpoint()
        if checkpoint:
            restored = bot_manager.restore_runtime_checkpoint(checkpoint)
            system_state.clear_bot_runtime_checkpoint()
            logger.info(
                "Restored bot runtime checkpoint for %d bot(s) (%d resumed).",
                len(checkpoint),
                restored,
            )

    result = {
        "unclean_shutdown": unclean,
        "orphaned_claims": orphaned,
        "journal_reconciled": journal_reconciled,
        "broker_reconciled": broker_reconciled,
        "incomplete_remaining": len(incomplete_final),
        "ambiguous_orders": ambiguous_after,
        "safe_mode": system_state.is_safe_mode_active(),
    }
    logger.info("Startup recovery complete: %s", result)
    return result


def confirm_safe_mode() -> dict[str, Any]:
    """Operator acknowledges system state — clears safe mode latch."""
    info = system_state.get_safe_mode_info()
    system_state.clear_safe_mode()
    system_state.mark_shutdown_clean()
    return {"cleared": True, "previous": info}
