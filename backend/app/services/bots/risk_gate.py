"""Pre-trade risk checks for algo bot orders."""

import json
import time
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


def _parse_event_ts(ts) -> datetime | None:
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        if isinstance(ts, str):
            raw = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
            dt = datetime.fromisoformat(raw)
            # SQLite CURRENT_TIMESTAMP is UTC without tzinfo — treat naive as UTC.
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except (TypeError, ValueError, OSError):
        return None
    return None


def _resolve_bot_total_pnl(bot: dict, total_pnl: float | None = None) -> float:
    if total_pnl is not None:
        return float(total_pnl)
    return float(bot.get("total_pnl") or bot.get("pnl") or 0)


def _compute_drawdown_hold(bot: dict, total_pnl: float | None = None) -> dict | None:
    """Return drawdown circuit-breaker hold when cumulative loss exceeds the limit."""
    cfg = RiskGate._parse_bot_config(bot)
    max_dd_pct = float(cfg.get("max_drawdown_pct", BOT_MAX_DRAWDOWN_PCT))
    if max_dd_pct <= 0:
        return None

    allocation = float(bot.get("allocation") or 0)
    if allocation <= 0:
        return None

    pnl = _resolve_bot_total_pnl(bot, total_pnl)
    if pnl >= 0:
        return None

    dd_pct = abs(pnl) / allocation * 100.0
    if dd_pct < max_dd_pct:
        return None

    block_reason = (
        f"Max drawdown circuit breaker: bot DD {dd_pct:.1f}% "
        f"exceeds limit {max_dd_pct:.1f}%. Auto-paused."
    )
    return {
        "kind": "drawdown",
        "drawdown_pct": round(dd_pct, 2),
        "max_drawdown_pct": max_dd_pct,
        "total_pnl": round(pnl, 2),
        "reason": f"Drawdown {dd_pct:.1f}% / {max_dd_pct:.1f}%",
        "block_reason": block_reason,
    }


def _streak_hold_cleared(cfg: dict, last_exit_dt: datetime | None) -> bool:
    """True when resume (or explicit clear) acknowledged losses through last_exit."""
    raw = cfg.get("streak_hold_cleared_at")
    if raw is None or last_exit_dt is None:
        return False
    cleared_dt = _parse_event_ts(raw)
    if cleared_dt is None:
        return False
    # Small epsilon so float/ISO round-trips still count as "at or after" last exit.
    return cleared_dt.timestamp() + 1e-3 >= last_exit_dt.timestamp()


def get_bot_entry_hold(bot: dict, *, total_pnl: float | None = None) -> dict | None:
    """Active entry hold for UI — streak limit, post-loss cooloff, or max drawdown.

    Cooloff is shown for RUNNING and PAUSED so a manual pause does not hide the timer.

    Streak-limit is not a permanent ban: after ``loss_cooloff_sec`` from the last
    exit (or a manual resume that sets ``streak_hold_cleared_at``), entries are
    allowed again. Otherwise bots that hit the limit could never trade a winner
    to clear the streak.
    """
    bot_id = bot.get("id", "")
    status = str(bot.get("status") or "").upper()
    if not bot_id or status not in ("RUNNING", "PAUSED"):
        return None

    cfg = RiskGate._parse_bot_config(bot)
    max_streak = int(cfg.get("max_consecutive_losses", BOT_MAX_CONSECUTIVE_LOSSES))
    cooloff_sec = int(cfg.get("loss_cooloff_sec", BOT_LOSS_COOLOFF_SEC))

    from app.services.bots import analytics as bot_analytics

    streak = bot_analytics.get_recent_consecutive_losses(bot_id)
    last_exit_ts = bot_analytics.last_exit_timestamp(bot_id)
    exit_dt = _parse_event_ts(last_exit_ts)

    if _streak_hold_cleared(cfg, exit_dt):
        return _compute_drawdown_hold(bot, total_pnl)

    if max_streak > 0 and streak >= max_streak:
        if cooloff_sec > 0 and exit_dt is not None:
            elapsed = time.time() - exit_dt.timestamp()
            if elapsed >= cooloff_sec:
                # Cooloff elapsed — allow entries again (streak may still be high
                # until a win; the next loss re-arms the hold).
                return _compute_drawdown_hold(bot, total_pnl)
            remaining = max(0, int(cooloff_sec - elapsed))
            until = datetime.fromtimestamp(
                exit_dt.timestamp() + cooloff_sec,
                tz=timezone.utc,
            )
            block_reason = (
                f"Consecutive-loss streak ({streak}) reached limit ({max_streak}). "
                f"Entries blocked for {remaining}s — resume to clear early, or wait."
            )
            return {
                "kind": "streak_limit",
                "consecutive_losses": streak,
                "max_consecutive_losses": max_streak,
                "cooloff_sec": cooloff_sec,
                "remaining_sec": remaining,
                "cooloff_until": until.isoformat().replace("+00:00", "Z"),
                "reason": f"Loss streak {streak}/{max_streak}",
                "block_reason": block_reason,
            }
        block_reason = (
            f"Consecutive-loss streak ({streak}) reached limit ({max_streak}). "
            "Auto-paused — resume manually to clear the hold."
        )
        return {
            "kind": "streak_limit",
            "consecutive_losses": streak,
            "max_consecutive_losses": max_streak,
            "reason": f"Loss streak {streak}/{max_streak}",
            "block_reason": block_reason,
        }

    if cooloff_sec > 0 and streak > 0 and exit_dt is not None:
        elapsed = time.time() - exit_dt.timestamp()
        if elapsed < cooloff_sec:
            remaining = max(0, int(cooloff_sec - elapsed))
            until = datetime.fromtimestamp(
                exit_dt.timestamp() + cooloff_sec,
                tz=timezone.utc,
            )
            block_reason = (
                f"Cooling-off after loss: {remaining}s remaining "
                f"(streak {streak}, cooloff {cooloff_sec}s)."
            )
            return {
                "kind": "cooloff",
                "consecutive_losses": streak,
                "cooloff_sec": cooloff_sec,
                "remaining_sec": remaining,
                "cooloff_until": until.isoformat().replace("+00:00", "Z"),
                "reason": (
                    f"Cooling off after {streak} consecutive "
                    f"loss{'es' if streak != 1 else ''}"
                ),
                "block_reason": block_reason,
            }

    return _compute_drawdown_hold(bot, total_pnl)


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
        backtest: bool = False,
    ) -> RiskDecision:
        if not is_exit:
            # Live-only latches must not zero out historical sims when the
            # desk already has bots running (or a kill switch tripped).
            if not backtest:
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

            if not backtest:
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
        """Block entry if the bot is on a consecutive-loss streak or in cooling-off."""
        hold = get_bot_entry_hold(bot)
        if hold and hold.get("block_reason"):
            return RiskDecision(False, hold["block_reason"])
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
        """Block entries if the bot's cumulative drawdown exceeds BOT_MAX_DRAWDOWN_PCT."""
        hold = _compute_drawdown_hold(bot)
        if hold and hold.get("block_reason"):
            return RiskDecision(False, hold["block_reason"])
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
