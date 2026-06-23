from datetime import datetime, timezone

from app.config import BOT_DAILY_LOSS_LIMIT_PCT
from app.services.bots.backtest_costs import (
    entry_fill_price,
    exit_fill_price,
    parse_cost_config,
    trade_fee,
)
from app.services.bots.indicators import first_eval_index, merge_strategy_config, prepare_strategy_df
from app.services.bots.strategies_chart_agent import (
    build_signal_from_insight,
    classify_filter_reject,
)
from app.services.bots.tick_strategies import is_tick_strategy
from app.services.market.resample import resample_candles_for_timeframe
from app.services.market.timeframes import normalize_timeframe
from app.services.bots.risk_gate import RiskGate
from app.services.bots.strategies import get_strategy, normalize_strategy_name
from app.services.bots.backtest_analytics import drawdown_curve, enrich_summary
from app.services.bots.take_profit import merge_tp_config, resolve_take_profit

_SIM_MODES = frozenset({"live_aligned", "research"})
_MIN_QTY = 0.001
_DEFAULT_ALLOCATION = 10_000.0


def _utc_day_key(bar_time) -> str | None:
    if bar_time is None:
        return None
    try:
        return datetime.fromtimestamp(int(bar_time), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _check_long_sl_tp(position: dict, bar_low: float, bar_high: float) -> tuple[str | None, float | None]:
    """Intra-bar SL/TP for a long position (conservative: SL before TP)."""
    sl = position.get("stop_loss")
    tp = position.get("take_profit")

    if sl is not None and bar_low <= sl:
        return "SL", sl
    if tp is not None and bar_high >= tp:
        return "TP", tp
    return None, None


def _check_short_sl_tp(position: dict, bar_low: float, bar_high: float) -> tuple[str | None, float | None]:
    """Intra-bar SL/TP for a short position (conservative: SL before TP)."""
    sl = position.get("stop_loss")
    tp = position.get("take_profit")

    if sl is not None and bar_high >= sl:
        return "SL", sl
    if tp is not None and bar_low <= tp:
        return "TP", tp
    return None, None


def _update_trailing_stop_short(position: dict, bar_low: float, bar_high: float, trailing_pct: float) -> None:
    if trailing_pct <= 0:
        return
    position["low_watermark"] = min(position.get("low_watermark", bar_high), bar_low)
    new_sl = position["low_watermark"] * (1 + trailing_pct / 100)
    current_sl = position.get("stop_loss")
    position["stop_loss"] = min(current_sl, new_sl) if current_sl is not None else new_sl


def _update_trailing_stop(position: dict, bar_low: float, bar_high: float, trailing_pct: float) -> None:
    if trailing_pct <= 0:
        return
    position["high_watermark"] = max(position["high_watermark"], bar_high)
    new_sl = position["high_watermark"] * (1 - trailing_pct / 100)
    position["stop_loss"] = max(position.get("stop_loss") or 0, new_sl)


def _sharpe_ratio(equity_curve: list[dict]) -> float | None:
    if len(equity_curve) < 3:
        return None
    returns: list[float] = []
    for j in range(1, len(equity_curve)):
        prev_eq = equity_curve[j - 1].get("equity")
        curr_eq = equity_curve[j].get("equity")
        if prev_eq and prev_eq > 0 and curr_eq is not None:
            returns.append((float(curr_eq) - float(prev_eq)) / float(prev_eq))
    if len(returns) < 2:
        return None
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = variance ** 0.5
    if std_r < 1e-12:
        return None
    t0 = equity_curve[0].get("time")
    t1 = equity_curve[-1].get("time")
    if t0 and t1 and t1 > t0:
        years = (int(t1) - int(t0)) / (365.25 * 86400)
        if years > 0:
            return round((mean_r / std_r) * (len(returns) / years) ** 0.5, 2)
    return round((mean_r / std_r) * (len(returns) ** 0.5), 2)


def _max_consecutive_losses(closed: list[dict]) -> int:
    streak = 0
    best = 0
    for trade in closed:
        if (trade.get("pnl") or 0) < 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def _compute_summary(
    closed: list[dict],
    *,
    total_pnl: float,
    win_rate: float,
    max_drawdown: float,
    trade_count: int,
    starting_equity: float,
    equity_curve: list[dict] | None = None,
    bars_in_market: int = 0,
    eval_bars: int = 0,
    blocked_entries: int = 0,
    filter_rejects: dict | None = None,
    total_fees: float = 0.0,
    slippage_bps: float = 0.0,
    fee_bps: float = 0.0,
) -> dict:
    wins = [float(t["pnl"]) for t in closed if (t.get("pnl") or 0) > 0]
    losses = [float(t["pnl"]) for t in closed if (t.get("pnl") or 0) < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 2)
    elif gross_profit > 0:
        profit_factor = None
    else:
        profit_factor = 0.0

    hold_secs = [int(t["hold_seconds"]) for t in closed if t.get("hold_seconds")]
    avg_hold_hours = (
        round(sum(hold_secs) / len(hold_secs) / 3600, 2) if hold_secs else 0.0
    )

    return {
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 2),
        "total_trades": trade_count,
        "max_drawdown": round(max_drawdown, 2),
        "profit_factor": profit_factor,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "expectancy": round(total_pnl / trade_count, 2) if trade_count else 0.0,
        "return_pct": round((total_pnl / starting_equity) * 100, 2) if starting_equity else 0.0,
        "avg_hold_hours": avg_hold_hours,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "sharpe_ratio": _sharpe_ratio(equity_curve or []),
        "max_consecutive_losses": _max_consecutive_losses(closed),
        "time_in_market_pct": round(
            (bars_in_market / eval_bars) * 100, 2,
        ) if eval_bars > 0 else 0.0,
        "blocked_entries": blocked_entries,
        "filter_rejects": filter_rejects or {},
        "total_fees": round(total_fees, 2),
        "slippage_bps": slippage_bps,
        "fee_bps": fee_bps,
    }


class BacktesterService:
    def __init__(self, screener_service):
        self.screener = screener_service
        self._risk_gate = RiskGate()

    def run_backtest(
        self,
        symbol: str,
        strategy_name: str,
        config: dict,
        candles: list,
        *,
        progress_cb=None,
        cancel_cb=None,
    ) -> dict:
        """
        Bar-close backtest aligned with live BotManager semantics:
        long-only entries, allocation-based sizing, risk gates, intra-bar SL/TP.
        Tick strategies replay simulated intra-bar paths from 1m candles.
        """
        if is_tick_strategy(strategy_name):
            from app.services.bots.backtest_tick import run_tick_backtest

            return run_tick_backtest(
                self, symbol, strategy_name, config, candles,
                progress_cb=progress_cb, cancel_cb=cancel_cb,
            )

        if not candles or len(candles) < 50:
            return {"error": "Not enough historical data"}

        cfg = config or {}
        sim_mode = str(cfg.get("sim_mode") or "live_aligned").lower()
        if sim_mode not in _SIM_MODES:
            sim_mode = "live_aligned"
        research = sim_mode == "research"
        allocation = float(cfg.get("allocation") or _DEFAULT_ALLOCATION)
        if allocation <= 0:
            allocation = _DEFAULT_ALLOCATION

        df = self.screener.process_candles(
            symbol, candles, cfg, strategy_name, full_history=True,
        )
        if df.empty:
            return {"error": "Failed to calculate indicators"}

        df = prepare_strategy_df(df, strategy_name, cfg)
        strategy = get_strategy(strategy_name, cfg)
        merged_config = merge_tp_config(strategy_name, cfg)
        strat_key = normalize_strategy_name(strategy_name)
        min_confidence = float(merged_config.get("min_confidence", 0.55))
        trailing_pct = float(
            cfg.get("trailing_stop_percent") or cfg.get("stop_loss_percent") or 0,
        )
        risk_per_entry = allocation * 0.01
        loss_limit = allocation * (BOT_DAILY_LOSS_LIMIT_PCT / 100.0)
        slippage_bps, fee_bps = parse_cost_config(cfg)
        total_fees = 0.0
        chart_cfg = merge_strategy_config("CHART_AGENT", cfg) if strat_key == "CHART_AGENT" else {}

        if strat_key == "CHART_AGENT":
            from app.services.agent.rule_engine import score_at_index

            confirm_tf = (chart_cfg.get("confirm_timeframe") or "").strip()
            htf_df = None
            if confirm_tf:
                try:
                    cf_tf = normalize_timeframe(confirm_tf)
                    htf_candles = resample_candles_for_timeframe(candles, cf_tf)
                    if htf_candles:
                        htf_df = self.screener.process_candles(
                            symbol, htf_candles, cfg, strategy_name, full_history=True,
                        )
                        htf_df = prepare_strategy_df(htf_df, strategy_name, cfg)
                except ValueError:
                    htf_df = None

            def _htf_closed_index(bar_time_val) -> int | None:
                if htf_df is None or htf_df.empty or bar_time_val is None:
                    return None
                mask = htf_df["time"] <= int(bar_time_val)
                if not mask.any():
                    return None
                return int(htf_df.index[mask][-1])

            def _chart_agent_signal(i: int) -> dict:
                insight = score_at_index(df, i, symbol)
                if not insight:
                    return {"signal": "NONE"}
                insight_dict = insight.to_dict()
                insight_dict.setdefault("timeframe", normalize_timeframe(cfg.get("timeframe") or "1m"))
                confirm_insight = None
                if htf_df is not None:
                    bar_time_val = df.iloc[i].get("time")
                    htf_idx = _htf_closed_index(bar_time_val)
                    if htf_idx is not None and htf_idx >= 1:
                        confirm = score_at_index(htf_df, htf_idx, symbol)
                        if confirm:
                            confirm_insight = confirm.to_dict()
                backtest_bot_id = str(cfg.get("_bot_id") or cfg.get("backtest_bot_id") or "backtest")
                return build_signal_from_insight(
                    insight_dict,
                    chart_cfg,
                    confirm_insight=confirm_insight,
                    bot_id=backtest_bot_id,
                    symbol=symbol,
                    timeframe=normalize_timeframe(cfg.get("timeframe") or "1m"),
                )
        else:
            _chart_agent_signal = None

        position = None
        trade_log = []
        equity = allocation
        starting_equity = allocation
        peak_equity = equity
        max_drawdown = 0.0
        equity_curve = []
        sample_stride = max(1, (len(df) - 1) // 200)
        start_i = first_eval_index(df, strategy_name, cfg)
        last_signal_bar_time = None
        daily_pnl = 0.0
        daily_pnl_day: str | None = None
        halted = False
        bot_stub = {"status": "RUNNING", "allocation": allocation}
        blocked_entries = 0
        filter_rejects: dict[str, int] = {
            "min_score": 0,
            "trend": 0,
            "vol": 0,
            "htf": 0,
            "confidence": 0,
            "calibration": 0,
            "other": 0,
        }
        bars_in_market = 0

        def _roll_daily(bar_time) -> None:
            nonlocal daily_pnl, daily_pnl_day
            day = _utc_day_key(bar_time)
            if day is None:
                return
            if daily_pnl_day != day:
                daily_pnl_day = day
                daily_pnl = 0.0

        def _close_position(bar_time, exit_price: float, reason: str) -> None:
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
            hold_seconds = None
            if entry_time is not None and bar_time is not None:
                try:
                    hold_seconds = max(0, int(bar_time) - int(entry_time))
                except (TypeError, ValueError):
                    hold_seconds = None
            exit_row = {
                "time": int(bar_time) if bar_time is not None else 0,
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

        def _try_entry(signal: str, signal_data: dict, row: dict, bar_time) -> None:
            nonlocal position, last_signal_bar_time, blocked_entries, equity, total_fees
            if (not research and halted) or signal != "BUY":
                return
            if bar_time is not None and last_signal_bar_time == bar_time:
                return

            current_price = float(row["close"])
            stop_loss_price = signal_data.get("stop_loss_price")
            if stop_loss_price is not None:
                stop_loss = float(stop_loss_price)
            else:
                sl_dist = signal_data.get("stop_loss_distance", current_price * 0.02)
                stop_loss = current_price - float(sl_dist)

            price_diff = abs(current_price - stop_loss)
            if price_diff <= 0:
                return

            qty = risk_per_entry / price_diff
            size_factor = float((signal_data or {}).get("size_factor") or 1.0)
            if strat_key == "CHART_AGENT" and chart_cfg.get("use_vol_sizing", True) and size_factor > 0:
                qty *= size_factor
            if research:
                qty = min(qty, allocation / max(current_price, 1e-9))
            else:
                decision = self._risk_gate.validate_trade(
                    bot_stub,
                    "BUY",
                    qty,
                    current_price,
                    is_exit=False,
                    daily_pnl=daily_pnl,
                    position_size=0.0,
                )
                if not decision.allowed:
                    blocked_entries += 1
                    return
                qty = decision.quantity if decision.quantity is not None else qty
            if qty < _MIN_QTY:
                blocked_entries += 1
                return

            _, tp_price = resolve_take_profit(
                merged_config, signal_data, signal, current_price,
            )
            entry_fill = entry_fill_price(current_price, "BUY", slippage_bps)
            entry_fee = trade_fee(entry_fill * qty, fee_bps)
            equity -= entry_fee
            total_fees += entry_fee
            position = {
                "side": "BUY",
                "entry_price": current_price,
                "entry_fill": entry_fill,
                "entry_time": bar_time,
                "qty": qty,
                "stop_loss": stop_loss,
                "take_profit": tp_price,
                "high_watermark": current_price,
            }
            entry_row = {
                "time": int(bar_time) if bar_time is not None else 0,
                "side": "BUY",
                "price": round(entry_fill, 4),
                "quantity": round(qty, 6),
                "pnl": None,
                "is_exit": False,
                "reason": "ENTRY",
                "fee": round(entry_fee, 4),
            }
            snap = signal_data.get("insight_snapshot")
            if snap:
                entry_row["insight_snapshot"] = snap
            trade_log.append(entry_row)
            last_signal_bar_time = bar_time

        def _record_filter_reject(signal_data: dict | None) -> None:
            if not signal_data:
                return
            reason = signal_data.get("reject_reason")
            if not reason:
                return
            bucket = classify_filter_reject(reason)
            if bucket:
                filter_rejects[bucket] = filter_rejects.get(bucket, 0) + 1

        def _try_short_entry(signal: str, signal_data: dict, row: dict, bar_time) -> None:
            nonlocal position, last_signal_bar_time, blocked_entries, equity, total_fees
            if not research or signal != "SELL":
                return
            if bar_time is not None and last_signal_bar_time == bar_time:
                return

            current_price = float(row["close"])
            stop_loss_price = signal_data.get("stop_loss_price")
            if stop_loss_price is not None:
                stop_loss = float(stop_loss_price)
            else:
                sl_dist = signal_data.get("stop_loss_distance", current_price * 0.02)
                stop_loss = current_price + float(sl_dist)

            price_diff = abs(stop_loss - current_price)
            if price_diff <= 0:
                return

            qty = risk_per_entry / price_diff
            size_factor = float((signal_data or {}).get("size_factor") or 1.0)
            if strat_key == "CHART_AGENT" and chart_cfg.get("use_vol_sizing", True) and size_factor > 0:
                qty *= size_factor
            qty = min(qty, allocation / max(current_price, 1e-9))
            if qty < _MIN_QTY:
                blocked_entries += 1
                return

            _, tp_price = resolve_take_profit(
                merged_config, signal_data, "SELL", current_price,
            )
            entry_fill = entry_fill_price(current_price, "SELL", slippage_bps)
            entry_fee = trade_fee(entry_fill * qty, fee_bps)
            equity -= entry_fee
            total_fees += entry_fee
            position = {
                "side": "SELL",
                "entry_price": current_price,
                "entry_fill": entry_fill,
                "entry_time": bar_time,
                "qty": qty,
                "stop_loss": stop_loss,
                "take_profit": tp_price,
                "low_watermark": current_price,
            }
            trade_log.append({
                "time": int(bar_time) if bar_time is not None else 0,
                "side": "SELL",
                "price": round(entry_fill, 4),
                "quantity": round(qty, 6),
                "pnl": None,
                "is_exit": False,
                "reason": "ENTRY_SHORT",
                "fee": round(entry_fee, 4),
            })
            last_signal_bar_time = bar_time

        eval_bars = max(len(df) - start_i, 1)
        progress_stride = max(1, eval_bars // 40)

        for i in range(start_i, len(df)):
            if cancel_cb and cancel_cb():
                return {"error": "Backtest cancelled", "cancelled": True}

            row = df.iloc[i].to_dict()
            bar_time = row.get("time")
            bar_low = float(row.get("low") or row.get("close") or 0)
            bar_high = float(row.get("high") or row.get("close") or 0)
            bar_close = float(row.get("close") or 0)
            _roll_daily(bar_time)

            if position:
                bars_in_market += 1
                if position["side"] == "BUY":
                    _update_trailing_stop(position, bar_low, bar_high, trailing_pct)
                    trigger, exit_px = _check_long_sl_tp(position, bar_low, bar_high)
                else:
                    _update_trailing_stop_short(position, bar_low, bar_high, trailing_pct)
                    trigger, exit_px = _check_short_sl_tp(position, bar_low, bar_high)
                if trigger:
                    _close_position(bar_time, exit_px, trigger)

            signal_data = None
            if _chart_agent_signal is not None:
                signal_data = _chart_agent_signal(i)
            else:
                signal_data = strategy.evaluate(row)
            signal = (signal_data or {}).get("signal")

            if strat_key == "CHART_AGENT" and signal not in ("BUY", "SELL", "CLOSE"):
                _record_filter_reject(signal_data)

            if position:
                close_signal = (
                    (position["side"] == "BUY" and signal in ("SELL", "CLOSE"))
                    or (position["side"] == "SELL" and signal in ("BUY", "CLOSE"))
                )
                if close_signal:
                    if bar_time is not None and last_signal_bar_time == bar_time:
                        signal = None
                    else:
                        _close_position(bar_time, bar_close, "SIGNAL")
                        last_signal_bar_time = bar_time
                        signal = None

            if not position and signal == "BUY":
                _try_entry(signal, signal_data, row, bar_time)
            elif not position and signal == "SELL" and research:
                _try_short_entry(signal, signal_data, row, bar_time)

            peak_equity = max(peak_equity, equity)
            drawdown = (peak_equity - equity) / peak_equity * 100 if peak_equity else 0
            max_drawdown = max(max_drawdown, drawdown)

            if bar_time is not None and (i % sample_stride == 0 or i == len(df) - 1):
                equity_curve.append({"time": int(bar_time), "equity": round(equity, 2)})

            if progress_cb and (i - start_i) % progress_stride == 0:
                progress_cb(i - start_i, eval_bars)

        if progress_cb:
            progress_cb(eval_bars, eval_bars)

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
            eval_bars=eval_bars,
            blocked_entries=blocked_entries,
            filter_rejects=filter_rejects,
            total_fees=total_fees,
            slippage_bps=slippage_bps,
            fee_bps=fee_bps,
        )
        if strat_key == "CHART_AGENT":
            summary["filter_rejects_total"] = sum(filter_rejects.values())
        summary = enrich_summary(
            summary,
            equity_curve=equity_curve,
            candles=candles,
            starting_equity=starting_equity,
        )
        if summary.get("max_drawdown") and float(summary["max_drawdown"]) > 0:
            ret = float(summary.get("return_pct") or 0)
            summary["calmar_ratio"] = round(ret / float(summary["max_drawdown"]), 3)
        dd_curve = drawdown_curve(equity_curve)

        from app.services.bots.monte_carlo import monte_carlo_trade_bands

        monte_carlo = monte_carlo_trade_bands(
            trade_log,
            starting_equity=starting_equity,
        )

        return {
            "win_rate": summary["win_rate"],
            "total_pnl": summary["total_pnl"],
            "max_drawdown": summary["max_drawdown"],
            "trade_count": total_trades,
            "equity_curve": equity_curve,
            "drawdown_curve": dd_curve,
            "starting_equity": round(starting_equity, 2),
            "allocation": round(allocation, 2),
            "trades": trade_log,
            "trades_total": len(trade_log),
            "summary": summary,
            "sim_mode": sim_mode,
            "costs": {
                "slippage_bps": slippage_bps,
                "fee_bps": fee_bps,
                "total_fees": round(total_fees, 2),
            },
            "monte_carlo": monte_carlo,
        }
