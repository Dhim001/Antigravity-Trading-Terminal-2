"""Pre-trade risk checks for algo bot orders."""

from dataclasses import dataclass
from app.config import (
    BOT_MIN_NOTIONAL,
    BOT_DAILY_LOSS_LIMIT_PCT,
    BOT_MAX_ACTIVE_BOTS,
    MAX_ORDER_VALUE,
)


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    quantity: float | None = None


class RiskGate:
    def validate_create(self, active_bot_count: int) -> RiskDecision:
        if active_bot_count >= BOT_MAX_ACTIVE_BOTS:
            return RiskDecision(
                False,
                f"Maximum active bots ({BOT_MAX_ACTIVE_BOTS}) reached.",
            )
        return RiskDecision(True, "OK")

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
        status = bot.get("status", "STOPPED")
        if status != "RUNNING":
            return RiskDecision(False, f"Bot is {status}, not RUNNING.")

        allocation = float(bot.get("allocation") or 0)
        loss_limit = allocation * (BOT_DAILY_LOSS_LIMIT_PCT / 100.0)
        if loss_limit > 0 and daily_pnl <= -loss_limit:
            return RiskDecision(
                False,
                f"Daily loss limit reached ({BOT_DAILY_LOSS_LIMIT_PCT}% of ${allocation:.0f} allocation).",
            )

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
