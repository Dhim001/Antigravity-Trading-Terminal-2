"""Pre-trade risk checks for algo bot orders."""

from dataclasses import dataclass

from app.config import (
    BOT_MIN_NOTIONAL,
    BOT_DAILY_LOSS_LIMIT_PCT,
    BOT_MAX_ACTIVE_BOTS,
    MAX_ORDER_VALUE,
)
from app.services.bots.portfolio_risk import (
    PortfolioSnapshot,
    build_portfolio_snapshot,
    validate_portfolio_entry,
)
from app.services.bots import risk_state_store
from app.services.bots.time_windows import is_no_trade_window


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    quantity: float | None = None


class RiskGate:
    def __init__(self):
        self._portfolio_cache: PortfolioSnapshot | None = None
        self._portfolio_cache_at: float = 0.0

    def _kill_switch_block(self) -> RiskDecision | None:
        if risk_state_store.is_kill_switch_tripped():
            return RiskDecision(
                False,
                "Drawdown kill switch tripped — reset risk controls before trading.",
            )
        return None

    def invalidate_portfolio_cache(self) -> None:
        self._portfolio_cache = None

    def get_portfolio_snapshot(self, oms, *, max_age_sec: float = 2.0) -> PortfolioSnapshot:
        import time

        now = time.time()
        if self._portfolio_cache and (now - self._portfolio_cache_at) < max_age_sec:
            return self._portfolio_cache
        snap = build_portfolio_snapshot(oms)
        self._portfolio_cache = snap
        self._portfolio_cache_at = now
        return snap

    def validate_create(self, active_bot_count: int) -> RiskDecision:
        blocked = self._kill_switch_block()
        if blocked:
            return blocked
        if active_bot_count >= BOT_MAX_ACTIVE_BOTS:
            return RiskDecision(
                False,
                f"Maximum active bots ({BOT_MAX_ACTIVE_BOTS}) reached.",
            )
        return RiskDecision(True, "OK")

    def validate_portfolio(
        self,
        oms,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        *,
        is_exit: bool,
        entry_leverage: float = 1.0,
    ) -> RiskDecision:
        if is_exit:
            return RiskDecision(True, "OK", quantity)
        snapshot = self.get_portfolio_snapshot(oms)
        from app.config import RISK_MARGIN_ENABLED
        from app.services.bots.margin_risk import build_margin_snapshot

        margin = build_margin_snapshot(oms, snapshot) if RISK_MARGIN_ENABLED else None
        allowed, reason, capped = validate_portfolio_entry(
            snapshot,
            symbol,
            side,
            quantity,
            price,
            margin=margin,
            entry_leverage=entry_leverage,
        )
        if not allowed:
            return RiskDecision(False, reason)
        return RiskDecision(True, reason, capped if capped is not None else quantity)

    def validate_trade(
        self,
        bot: dict,
        side: str,
        quantity: float,
        price: float,
        *,
        is_exit: bool,
        daily_pnl: float,
        position_size: float,
    ) -> RiskDecision:
        if not is_exit:
            blocked = self._kill_switch_block()
            if blocked:
                return blocked

            symbol = str(bot.get("symbol") or "")
            in_window, window_reason = is_no_trade_window(None, symbol)
            if in_window:
                return RiskDecision(False, window_reason)

        status = bot.get("status", "STOPPED")
        if status != "RUNNING" and not is_exit:
            return RiskDecision(False, f"Bot is {status}, not RUNNING.")

        allocation = float(bot.get("allocation") or 0)
        loss_limit = allocation * (BOT_DAILY_LOSS_LIMIT_PCT / 100.0)

        # 3.3-B: Graduated daily loss step-down.
        # At full limit  → block entirely (existing behaviour).
        # At half limit  → allow but reduce size by 50% to preserve capital.
        if loss_limit > 0 and not is_exit:
            half_limit = loss_limit * 0.5
            if daily_pnl <= -loss_limit:
                return RiskDecision(
                    False,
                    f"Daily loss limit reached ({BOT_DAILY_LOSS_LIMIT_PCT}% of ${allocation:.0f} allocation).",
                )
            if daily_pnl <= -half_limit:
                reduced_qty = (quantity or 0) * 0.5
                return RiskDecision(
                    True,
                    f"Half daily-loss reached — size reduced 50% (daily PnL ${daily_pnl:.2f}).",
                    reduced_qty,
                )
        elif loss_limit > 0 and is_exit and daily_pnl <= -loss_limit:
            # Still let exits through even at full limit.
            pass


        if quantity <= 0:
            return RiskDecision(False, "Quantity must be greater than 0.")

        notional = quantity * price
        if notional > MAX_ORDER_VALUE:
            capped = MAX_ORDER_VALUE / price
            return RiskDecision(True, "Capped to MAX_ORDER_VALUE.", capped)

        if is_exit:
            if side == "SELL" and position_size <= 0:
                return RiskDecision(False, "No long position to close.")
            if side == "BUY" and position_size >= 0:
                return RiskDecision(False, "No short position to close.")
            exit_qty = min(quantity, abs(position_size))
            return RiskDecision(True, "OK", exit_qty)

        if side == "BUY" and position_size > 0:
            return RiskDecision(False, "Already long — skip entry.")
        if side == "SELL" and position_size <= 0:
            return RiskDecision(False, "Long-only mode — no short entry.")

        if notional < BOT_MIN_NOTIONAL:
            return RiskDecision(
                False,
                f"Notional ${notional:.2f} below minimum ${BOT_MIN_NOTIONAL:.2f}.",
            )

        if notional > allocation:
            capped_qty = allocation / price
            return RiskDecision(
                True,
                f"Reduced to allocation cap (${allocation:.0f}).",
                capped_qty,
            )

        return RiskDecision(True, "OK", quantity)
