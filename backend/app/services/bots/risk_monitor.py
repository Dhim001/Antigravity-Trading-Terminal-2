"""Account-level drawdown monitor — stops all bots on breach."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.api.outbound import publish_bots_update
from app.config import (
    RISK_KILL_SWITCH_ENABLED,
    RISK_MAX_DRAWDOWN_PCT,
    RISK_WEEKEND_FLATTEN_ENABLED,
    RISK_POSITION_DURATION_ENABLED,
    RISK_DYNAMIC_CORRELATION_ENABLED,
)
from app.services.bots.portfolio_risk import build_portfolio_snapshot
from app.services.bots import risk_state_store as store
from app.services.events import channels
from app.services.events import publish as event_publish

logger = logging.getLogger(__name__)


@dataclass
class DrawdownSnapshot:
    account_equity: float
    cash_equity: float
    equity_peak: float
    current_drawdown_pct: float
    max_drawdown_pct: float
    kill_switch_enabled: bool
    kill_switch_tripped: bool
    kill_switch_tripped_at: float | None


# Number of consecutive risk-monitor ticks a drawdown must persist before the
# kill switch actually fires.  This prevents transient mark-to-market dips
# (e.g. one bad tick, a single open trade fluctuating) from tripping the switch.
_BREACH_CONFIRM_TICKS = 3
_breach_counter: int = 0


def compute_drawdown(oms) -> DrawdownSnapshot:
    snapshot = build_portfolio_snapshot(oms)
    total_equity = max(float(snapshot.account_equity), 0.0)
    gross = float(snapshot.gross_exposure)

    # --- Peak tracking ---
    # Only ratchet the peak upward when the portfolio is flat (no open
    # positions).  While positions are open, mark-to-market swings can
    # inflate total_equity above the "real" high-water mark.  When the
    # position eventually closes, equity may drop back below that
    # inflated peak — causing a false drawdown signal.
    #
    # By only updating the peak when gross == 0 (all positions closed),
    # we ensure the peak reflects *realised* equity — money that is
    # actually locked in.
    is_flat = gross < 1.0  # effectively zero exposure
    if is_flat:
        peak = store.update_peak_if_higher(total_equity)
    else:
        peak = store.get_equity_peak()
        # If no peak has ever been recorded (first run), initialise it
        # to current equity so we don't start with peak=None / 0.
        if peak is None or peak <= 0:
            store.set_equity_peak(total_equity)
            peak = total_equity

    dd_pct = ((peak - total_equity) / peak * 100.0) if peak > 0 else 0.0
    dd_pct = max(dd_pct, 0.0)

    return DrawdownSnapshot(
        account_equity=round(total_equity, 2),
        cash_equity=round(total_equity - gross, 2),
        equity_peak=round(peak, 2),
        current_drawdown_pct=round(dd_pct, 2),
        max_drawdown_pct=RISK_MAX_DRAWDOWN_PCT,
        kill_switch_enabled=RISK_KILL_SWITCH_ENABLED,
        kill_switch_tripped=store.is_kill_switch_tripped(),
        kill_switch_tripped_at=store.get_kill_switch_tripped_at(),
    )


def drawdown_to_dict(snapshot: DrawdownSnapshot) -> dict:
    return {
        "account_equity": snapshot.account_equity,
        "cash_equity": snapshot.cash_equity,
        "equity_peak": snapshot.equity_peak,
        "current_drawdown_pct": snapshot.current_drawdown_pct,
        "max_drawdown_pct": snapshot.max_drawdown_pct,
        "kill_switch_enabled": snapshot.kill_switch_enabled,
        "kill_switch_tripped": snapshot.kill_switch_tripped,
        "kill_switch_tripped_at": snapshot.kill_switch_tripped_at,
    }


class RiskMonitor:
    async def evaluate(self, oms, bot_manager) -> DrawdownSnapshot:
        snapshot = compute_drawdown(oms)

        if RISK_WEEKEND_FLATTEN_ENABLED:
            try:
                flattened = await bot_manager.flatten_weekend_non_crypto_positions()
                if flattened:
                    logger.info("Weekend flatten closed %d non-crypto bot position(s).", flattened)
            except Exception as exc:
                logger.error("Weekend flatten failed: %s", exc)

        if RISK_POSITION_DURATION_ENABLED:
            try:
                closed = await bot_manager.close_stale_positions()
                if closed:
                    logger.info("Max position duration closed %d bot position(s).", closed)
            except Exception as exc:
                logger.error("Position duration auto-close failed: %s", exc)

        if RISK_DYNAMIC_CORRELATION_ENABLED:
            try:
                import asyncio
                from app.services.bots.correlation import refresh_correlation_cache

                feed = getattr(oms, "feed", None)
                await asyncio.to_thread(refresh_correlation_cache, feed=feed)
                bot_manager._risk_gate.invalidate_portfolio_cache()
            except Exception as exc:
                logger.error("Dynamic correlation refresh failed: %s", exc)

        if not RISK_KILL_SWITCH_ENABLED:
            return snapshot

        if store.is_kill_switch_tripped():
            return snapshot

        global _breach_counter

        if snapshot.current_drawdown_pct >= RISK_MAX_DRAWDOWN_PCT:
            _breach_counter += 1
            logger.warning(
                "Drawdown %.1f%% >= limit %.1f%% — breach tick %d/%d "
                "(equity $%s, peak $%s).",
                snapshot.current_drawdown_pct,
                RISK_MAX_DRAWDOWN_PCT,
                _breach_counter,
                _BREACH_CONFIRM_TICKS,
                f"{snapshot.account_equity:,.2f}",
                f"{snapshot.equity_peak:,.2f}",
            )
            if _breach_counter >= _BREACH_CONFIRM_TICKS:
                _breach_counter = 0
                store.trip_kill_switch()
                stopped = await bot_manager.stop_all_bots()
                reason = (
                    f"Drawdown kill switch: equity ${snapshot.account_equity:,.2f} is "
                    f"{snapshot.current_drawdown_pct:.1f}% below peak "
                    f"${snapshot.equity_peak:,.2f} (limit {RISK_MAX_DRAWDOWN_PCT:.1f}%). "
                    f"Confirmed over {_BREACH_CONFIRM_TICKS} consecutive checks. "
                    f"Stopped {stopped} bot(s)."
                )
                logger.error(reason)
                await event_publish.publish(
                    channels.EMERGENCY_STOP,
                    {
                        "source": "drawdown_kill_switch",
                        "drawdown_pct": snapshot.current_drawdown_pct,
                        "equity": snapshot.account_equity,
                        "peak": snapshot.equity_peak,
                    },
                )
                await event_publish.publish(channels.BOT_RELOAD, {})
                if bot_manager.broadcast_cb:
                    await publish_bots_update(
                        bot_manager.broadcast_cb,
                        bot_manager.list_bots_public(),
                    )
                snapshot.kill_switch_tripped = True
                snapshot.kill_switch_tripped_at = store.get_kill_switch_tripped_at()
        else:
            # Drawdown recovered below limit — reset confirmation counter.
            if _breach_counter > 0:
                logger.info(
                    "Drawdown recovered to %.1f%% (< %.1f%%) — "
                    "resetting breach counter (was %d).",
                    snapshot.current_drawdown_pct,
                    RISK_MAX_DRAWDOWN_PCT,
                    _breach_counter,
                )
            _breach_counter = 0

        return snapshot
