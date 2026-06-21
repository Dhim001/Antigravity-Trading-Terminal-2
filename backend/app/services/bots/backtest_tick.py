"""Tick-strategy backtest — replay simulated tick paths from 1m OHLC archive."""

from __future__ import annotations

from app.config import BOT_DAILY_LOSS_LIMIT_PCT
from app.services.bots.backtest_costs import (
    entry_fill_price,
    exit_fill_price,
    parse_cost_config,
    trade_fee,
)
from app.services.bots.backtest_analytics import drawdown_curve, enrich_summary
from app.services.bots.backtester import (
    BacktesterService,
    _check_long_sl_tp,
    _compute_summary,
    _update_trailing_stop,
    _utc_day_key,
)
from app.services.bots.risk_gate import RiskGate
from app.services.bots.take_profit import merge_tp_config, resolve_take_profit
from app.services.bots.tick_screener import TickScreener
from app.services.bots.tick_strategies import get_tick_strategy, merge_tick_config

_MIN_QTY = 0.001
_DEFAULT_ALLOCATION = 10_000.0
_TICKS_PER_BAR = 4


def simulate_ticks_from_candle(bar: dict) -> list[tuple[int, float]]:
    """Build a conservative OHLC tick path within one 1m bar."""
    t0 = int(bar.get("time") or 0)
    o = float(bar.get("open") or bar.get("close") or 0)
    h = float(bar.get("high") or o)
    l = float(bar.get("low") or o)
    c = float(bar.get("close") or o)
    if o <= 0:
        return []

    if c >= o:
        prices = [o, l, h, c]
    else:
        prices = [o, h, l, c]

    out: list[tuple[int, float]] = []
    span_ms = 59_000
    step = span_ms // max(len(prices) - 1, 1)
    for i, px in enumerate(prices):
        out.append((t0 * 1000 + i * step, px))
    return out


class TickBacktester:
    """Replay tick strategies over simulated intra-bar price paths."""

    def __init__(self, risk_gate: RiskGate | None = None):
        self._risk_gate = risk_gate or RiskGate()

    def run(
        self,
        symbol: str,
        strategy_name: str,
        config: dict,
        candles_1m: list,
        *,
        progress_cb=None,
        cancel_cb=None,
    ) -> dict:
        if not candles_1m or len(candles_1m) < 50:
            return {"error": "Not enough historical data"}

        cfg = config or {}
        sim_mode = str(cfg.get("sim_mode") or "live_aligned").lower()
        research = sim_mode == "research"
        allocation = float(cfg.get("allocation") or _DEFAULT_ALLOCATION)
        if allocation <= 0:
            allocation = _DEFAULT_ALLOCATION

        tick_cfg = merge_tick_config(strategy_name, cfg)
        strategy = get_tick_strategy(strategy_name, tick_cfg)
        merged_config = merge_tp_config(strategy_name, cfg)
        trailing_pct = float(
            cfg.get("trailing_stop_percent") or cfg.get("stop_loss_percent") or 0,
        )
        risk_per_entry = allocation * 0.01
        loss_limit = allocation * (BOT_DAILY_LOSS_LIMIT_PCT / 100.0)
        slippage_bps, fee_bps = parse_cost_config(cfg)
        cooldown_ms = int(float(tick_cfg.get("tick_cooldown_sec", 10)) * 1000)
        lookback = int(tick_cfg.get("lookback_ticks", 20))

        screener = TickScreener()
        position = None
        trade_log: list[dict] = []
        equity = allocation
        starting_equity = allocation
        peak_equity = equity
        max_drawdown = 0.0
        equity_curve: list[dict] = []
        daily_pnl = 0.0
        daily_pnl_day: str | None = None
        halted = False
        blocked_entries = 0
        total_fees = 0.0
        last_signal_bucket: int | None = None
        last_tick_signal_at = 0
        bot_stub = {"status": "RUNNING", "allocation": allocation}
        bars_in_market = 0
        tick_count = 0
        eval_ticks = 0

        def _roll_daily(ts_sec: int) -> None:
            nonlocal daily_pnl, daily_pnl_day
            day = _utc_day_key(ts_sec)
            if day is None:
                return
            if daily_pnl_day != day:
                daily_pnl_day = day
                daily_pnl = 0.0

        def _close_position(ts_sec: int, exit_price: float, reason: str) -> None:
            nonlocal position, equity, daily_pnl, halted, total_fees
            if not position:
                return
            side = position["side"]
            qty = position["qty"]
            entry_fill = float(position.get("entry_fill") or position["entry_price"])
            exit_side = "SELL" if side == "BUY" else "BUY"
            exit_fill = exit_fill_price(exit_price, exit_side, slippage_bps)
            exit_fee = trade_fee(exit_fill * qty, fee_bps)
            if side == "BUY":
                profit = (exit_fill - entry_fill) * qty - exit_fee
            else:
                profit = (entry_fill - exit_fill) * qty - exit_fee
            equity += profit
            total_fees += exit_fee
            daily_pnl += profit
            entry_time = position.get("entry_time")
            hold_seconds = max(0, int(ts_sec) - int(entry_time)) if entry_time else None
            exit_row = {
                "time": int(ts_sec),
                "side": "SELL" if side == "BUY" else "BUY",
                "price": round(exit_fill, 4),
                "quantity": round(qty, 6),
                "pnl": round(profit, 2),
                "is_exit": True,
                "reason": reason,
                "fee": round(exit_fee, 4),
            }
            if hold_seconds is not None:
                exit_row["hold_seconds"] = hold_seconds
            trade_log.append(exit_row)
            position = None
            if not research and loss_limit > 0 and daily_pnl <= -loss_limit:
                halted = True
                bot_stub["status"] = "ERROR"

        def _try_entry(signal: str, signal_data: dict, price: float, ts_sec: int, bucket: int) -> None:
            nonlocal position, last_signal_bucket, blocked_entries, equity, total_fees
            if (not research and halted) or signal != "BUY":
                return
            if last_signal_bucket == bucket:
                return

            stop_loss_price = signal_data.get("stop_loss_price")
            if stop_loss_price is not None:
                stop_loss = float(stop_loss_price)
            else:
                sl_dist = signal_data.get("stop_loss_distance", price * 0.02)
                stop_loss = price - float(sl_dist)

            price_diff = abs(price - stop_loss)
            if price_diff <= 0:
                return

            qty = risk_per_entry / price_diff
            if research:
                qty = min(qty, allocation / max(price, 1e-9))
            else:
                decision = self._risk_gate.validate_trade(
                    bot_stub, "BUY", qty, price,
                    is_exit=False, daily_pnl=daily_pnl, position_size=0.0,
                )
                if not decision.allowed:
                    blocked_entries += 1
                    return
                qty = decision.quantity if decision.quantity is not None else qty
            if qty < _MIN_QTY:
                blocked_entries += 1
                return

            _, tp_price = resolve_take_profit(merged_config, signal_data, "BUY", price)
            entry_fill = entry_fill_price(price, "BUY", slippage_bps)
            entry_fee = trade_fee(entry_fill * qty, fee_bps)
            equity -= entry_fee
            total_fees += entry_fee
            position = {
                "side": "BUY",
                "entry_price": price,
                "entry_fill": entry_fill,
                "entry_time": ts_sec,
                "qty": qty,
                "stop_loss": stop_loss,
                "take_profit": tp_price,
                "high_watermark": price,
            }
            trade_log.append({
                "time": int(ts_sec),
                "side": "BUY",
                "price": round(entry_fill, 4),
                "quantity": round(qty, 6),
                "pnl": None,
                "is_exit": False,
                "reason": "ENTRY",
                "fee": round(entry_fee, 4),
            })
            last_signal_bucket = bucket

        warmup_bars = min(30, len(candles_1m) // 4)
        start_bar = max(warmup_bars, 1)
        total_ticks_est = max((len(candles_1m) - start_bar) * _TICKS_PER_BAR, 1)
        progress_stride = max(1, total_ticks_est // 40)
        sample_stride = max(1, (len(candles_1m) - start_bar) // 200)

        for bar_idx, bar in enumerate(candles_1m[start_bar:], start=start_bar):
            if cancel_cb and cancel_cb():
                return {"error": "Backtest cancelled", "cancelled": True}

            ticks = simulate_ticks_from_candle(bar)
            bar_low = float(bar.get("low") or bar.get("close") or 0)
            bar_high = float(bar.get("high") or bar.get("close") or 0)

            for time_ms, price in ticks:
                if price <= 0:
                    continue
                tick_count += 1
                eval_ticks += 1
                ts_sec = time_ms // 1000
                _roll_daily(ts_sec)

                screener.record(symbol, price, time_ms)

                if position:
                    bars_in_market += 1
                    _update_trailing_stop(position, bar_low, bar_high, trailing_pct)
                    trigger, exit_px = _check_long_sl_tp(position, bar_low, bar_high)
                    if trigger:
                        _close_position(ts_sec, exit_px, trigger)

                if last_tick_signal_at and (time_ms - last_tick_signal_at) < cooldown_ms:
                    if progress_cb and eval_ticks % progress_stride == 0:
                        progress_cb(eval_ticks, total_ticks_est)
                    continue

                ctx = screener.context(symbol, price, time_ms, lookback)
                if ctx is None:
                    if progress_cb and eval_ticks % progress_stride == 0:
                        progress_cb(eval_ticks, total_ticks_est)
                    continue

                signal_data = strategy.evaluate(ctx, price)
                signal = (signal_data or {}).get("signal")

                if position and signal == "SELL":
                    bucket = ts_sec
                    if last_signal_bucket != bucket:
                        _close_position(ts_sec, price, "SIGNAL")
                        last_signal_bucket = bucket
                        last_tick_signal_at = time_ms
                elif not position and signal == "BUY":
                    bucket = ts_sec
                    _try_entry(signal, signal_data, price, ts_sec, bucket)
                    last_tick_signal_at = time_ms

                peak_equity = max(peak_equity, equity)
                drawdown = (peak_equity - equity) / peak_equity * 100 if peak_equity else 0
                max_drawdown = max(max_drawdown, drawdown)

                if progress_cb and eval_ticks % progress_stride == 0:
                    progress_cb(eval_ticks, total_ticks_est)

            if bar_idx % sample_stride == 0 or bar_idx == len(candles_1m) - 1:
                bar_time = int(bar.get("time") or 0)
                if bar_time:
                    equity_curve.append({"time": bar_time, "equity": round(equity, 2)})

        if progress_cb:
            progress_cb(total_ticks_est, total_ticks_est)

        closed = [t for t in trade_log if t.get("is_exit")]
        winning_trades = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
        total_trades = len(closed)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        total_pnl = round(equity - starting_equity, 2)
        summary = _compute_summary(
            closed,
            total_pnl=total_pnl,
            win_rate=win_rate,
            max_drawdown=max_drawdown,
            trade_count=total_trades,
            starting_equity=starting_equity,
            equity_curve=equity_curve,
            bars_in_market=bars_in_market,
            eval_bars=max(eval_ticks, 1),
            blocked_entries=blocked_entries,
            total_fees=total_fees,
            slippage_bps=slippage_bps,
            fee_bps=fee_bps,
        )
        summary["execution_mode"] = "TICK"
        summary["ticks_replayed"] = tick_count
        summary = enrich_summary(
            summary,
            equity_curve=equity_curve,
            candles=candles_1m,
            starting_equity=starting_equity,
        )

        return {
            "win_rate": summary["win_rate"],
            "total_pnl": summary["total_pnl"],
            "max_drawdown": summary["max_drawdown"],
            "trade_count": total_trades,
            "equity_curve": equity_curve,
            "drawdown_curve": drawdown_curve(equity_curve),
            "starting_equity": round(starting_equity, 2),
            "allocation": round(allocation, 2),
            "trades": trade_log,
            "trades_total": len(trade_log),
            "summary": summary,
            "sim_mode": sim_mode,
            "execution_mode": "TICK",
            "costs": {
                "slippage_bps": slippage_bps,
                "fee_bps": fee_bps,
                "total_fees": round(total_fees, 2),
            },
        }


def run_tick_backtest(
    backtester: BacktesterService,
    symbol: str,
    strategy_name: str,
    config: dict,
    candles: list,
    *,
    progress_cb=None,
    cancel_cb=None,
) -> dict:
    """Entry point used by BacktesterService — always expects 1m candles."""
    runner = TickBacktester(risk_gate=backtester._risk_gate)
    return runner.run(
        symbol, strategy_name, config, candles,
        progress_cb=progress_cb, cancel_cb=cancel_cb,
    )
