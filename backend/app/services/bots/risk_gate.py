"""Pre-trade risk checks for algo bot orders."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import (
    BOT_MIN_NOTIONAL,
    BOT_DAILY_LOSS_LIMIT_PCT,
    BOT_MAX_ACTIVE_BOTS,
    BOT_MAX_CONSECUTIVE_LOSSES,
    BOT_LOSS_COOLOFF_SEC,
    BOT_MAX_DRAWDOWN_PCT,
    BOT_MAX_PER_SYMBOL,
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
        at_ts: float | int | None = None,
    ) -> RiskDecision:
        if not is_exit:
            blocked = self._kill_switch_block()
            if blocked:
                return blocked

            symbol = str(bot.get("symbol") or "")
            if at_ts is not None:
                try:
                    ref_dt = datetime.fromtimestamp(float(at_ts), tz=timezone.utc)
                except (TypeError, ValueError, OSError, OverflowError):
                    ref_dt = None
            else:
                ref_dt = None
            in_window, window_reason = is_no_trade_window(ref_dt, symbol)
            if in_window:
                return RiskDecision(False, window_reason)

            from app.services.altdata.event_policy import check_entry_gates
            import time as _time

            bot_cfg = self._parse_bot_config(bot)
            gate_ts = at_ts if at_ts is not None else _time.time()
            allowed, gate_reason, _gate = check_entry_gates(
                symbol, gate_ts, bot_cfg, is_exit=False,
            )
            if not allowed and gate_reason:
                return RiskDecision(False, gate_reason)

            # 4.1: Consecutive-loss auto-pause — block entries after N consecutive losses.
            streak_decision = self._check_streak_and_cooloff(bot)
            if streak_decision is not None:
                return streak_decision

            # 4.2: Max drawdown circuit breaker — per-bot cumulative DD limit.
            dd_decision = self._check_max_drawdown(bot)
            if dd_decision is not None:
                return dd_decision

            # 4.3: Per-symbol bot concentration — max N bots on same symbol.
            sym_decision = self._check_symbol_concentration(bot)
            if sym_decision is not None:
                return sym_decision

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

        if is_exit:
            if side == "SELL" and position_size <= 0:
                return RiskDecision(False, "No long position to close.")
            if side == "BUY" and position_size >= 0:
                return RiskDecision(False, "No short position to close.")
            exit_qty = min(quantity, abs(position_size))
            return RiskDecision(True, "OK", exit_qty)

        if side == "BUY" and position_size > 0:
            return RiskDecision(False, "Already long — skip entry.")

        bot_cfg = bot.get("config") or {}
        if isinstance(bot_cfg, str):
            try:
                bot_cfg = json.loads(bot_cfg) if bot_cfg else {}
            except (json.JSONDecodeError, TypeError):
                bot_cfg = {}

        direction_mode = bot_cfg.get("direction_mode", "LONG_ONLY").upper()
        if direction_mode not in ("LONG_ONLY", "SHORT_ONLY", "BOTH"):
            direction_mode = "LONG_ONLY"

        if direction_mode == "LONG_ONLY" and side == "SELL" and position_size <= 0:
            return RiskDecision(False, "Long-only mode — no short entry.")
        if direction_mode == "SHORT_ONLY" and side == "BUY" and position_size >= 0:
            return RiskDecision(False, "Short-only mode — no long entry.")

        if notional < BOT_MIN_NOTIONAL:
            return RiskDecision(
                False,
                f"Notional ${notional:.2f} below minimum ${BOT_MIN_NOTIONAL:.2f}.",
            )

        max_notional = MAX_ORDER_VALUE
        if allocation > 0:
            max_notional = min(allocation, MAX_ORDER_VALUE)
        if notional > max_notional:
            capped = max_notional / price
            if allocation > 0 and allocation < MAX_ORDER_VALUE:
                cap_label = f"allocation cap (${allocation:.0f})"
            else:
                cap_label = f"MAX_ORDER_VALUE (${MAX_ORDER_VALUE:,.0f})"
            return RiskDecision(True, f"Reduced to {cap_label}.", capped)

        return RiskDecision(True, "OK", quantity)

    # ── Streak & cooling-off gate ──────────────────────────────────────────

    def _check_streak_and_cooloff(self, bot: dict) -> RiskDecision | None:
        """Block entry if the bot is on a consecutive-loss streak or in cooling-off.

        Configurable via BOT_MAX_CONSECUTIVE_LOSSES (default 5) and
        BOT_LOSS_COOLOFF_SEC (default 300 = 5 min).
        """
        bot_id = bot.get("id", "")
        if not bot_id:
            return None

        max_streak = int(
            (bot.get("config") or {}).get(
                "max_consecutive_losses", BOT_MAX_CONSECUTIVE_LOSSES
            )
        )
        cooloff_sec = int(
            (bot.get("config") or {}).get(
                "loss_cooloff_sec", BOT_LOSS_COOLOFF_SEC
            )
        )

        # Skip if disabled (0 = no limit)
        if max_streak <= 0 and cooloff_sec <= 0:
            return None

        from app.services.bots import analytics as bot_analytics

        streak = bot_analytics.get_recent_consecutive_losses(bot_id)

        # Consecutive-loss gate
        if max_streak > 0 and streak >= max_streak:
            return RiskDecision(
                False,
                f"Consecutive-loss streak ({streak}) reached limit ({max_streak}). "
                "Auto-paused — resume manually or wait for cooloff.",
            )

        # Cooling-off gate: after a losing exit, wait cooloff_sec before next entry
        if cooloff_sec > 0 and streak > 0:
            last_exit_ts = bot_analytics.last_exit_timestamp(bot_id)
            if last_exit_ts:
                import time
                from datetime import datetime, timezone

                try:
                    if isinstance(last_exit_ts, str):
                        if last_exit_ts.endswith("Z"):
                            last_exit_ts = last_exit_ts[:-1] + "+00:00"
                        dt = datetime.fromisoformat(last_exit_ts)
                    else:
                        dt = datetime.fromtimestamp(float(last_exit_ts), tz=timezone.utc)
                    elapsed = time.time() - dt.timestamp()
                    if elapsed < cooloff_sec:
                        remaining = int(cooloff_sec - elapsed)
                        return RiskDecision(
                            False,
                            f"Cooling-off after loss: {remaining}s remaining "
                            f"(streak {streak}, cooloff {cooloff_sec}s).",
                        )
                except (TypeError, ValueError, OSError):
                    pass

        return None

    # ── Config helper ────────────────────────────────────────────────────

    @staticmethod
    def _parse_bot_config(bot: dict) -> dict:
        """Safely extract bot config as a dict (may be JSON string in DB)."""
        cfg = bot.get("config") or {}
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg) if cfg else {}
            except (json.JSONDecodeError, TypeError):
                cfg = {}
        return cfg if isinstance(cfg, dict) else {}

    # ── Max drawdown circuit breaker ──────────────────────────────────────

    def _check_max_drawdown(self, bot: dict) -> RiskDecision | None:
        """Block entries if the bot's cumulative drawdown exceeds BOT_MAX_DRAWDOWN_PCT.

        Looks at the bot's total PnL relative to its allocation.  If cumulative
        losses exceed the configured percentage, the bot is paused.
        """
        bot_cfg = self._parse_bot_config(bot)
        max_dd_pct = float(
            bot_cfg.get("max_drawdown_pct", BOT_MAX_DRAWDOWN_PCT)
        )
        if max_dd_pct <= 0:
            return None

        allocation = float(bot.get("allocation") or 0)
        if allocation <= 0:
            return None

        total_pnl = float(bot.get("total_pnl") or bot.get("pnl") or 0)
        if total_pnl >= 0:
            return None  # no drawdown

        dd_pct = abs(total_pnl) / allocation * 100.0
        if dd_pct >= max_dd_pct:
            return RiskDecision(
                False,
                f"Max drawdown circuit breaker: bot DD {dd_pct:.1f}% "
                f"exceeds limit {max_dd_pct:.1f}%. Auto-paused.",
            )
        return None

    # ── Per-symbol bot concentration ─────────────────────────────────────

    def _check_symbol_concentration(self, bot: dict) -> RiskDecision | None:
        """Limit concurrent bots on the same symbol."""
        bot_cfg = self._parse_bot_config(bot)
        max_per_sym = int(
            bot_cfg.get("max_bots_per_symbol", BOT_MAX_PER_SYMBOL)
        )
        if max_per_sym <= 0:
            return None

        symbol = str(bot.get("symbol") or "")
        bot_id = bot.get("id", "")
        if not symbol:
            return None

        try:
            from app.services.bots.analytics import get_active_bots_for_symbol

            active = get_active_bots_for_symbol(symbol, exclude_bot_id=bot_id)
            if active >= max_per_sym:
                return RiskDecision(
                    False,
                    f"Per-symbol limit: {active} active bots on {symbol} "
                    f"(max {max_per_sym}). Blocked.",
                )
        except (ImportError, Exception):
            pass  # graceful degrade if analytics function not available

        return None
