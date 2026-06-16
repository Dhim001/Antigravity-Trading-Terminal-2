from app.services.bots.indicators import first_eval_index, prepare_strategy_df
from app.services.bots.strategies import get_strategy, normalize_strategy_name
from app.services.bots.take_profit import merge_tp_config, resolve_take_profit


class BacktesterService:
    def __init__(self, screener_service):
        self.screener = screener_service

    def run_backtest(self, symbol: str, strategy_name: str, config: dict, candles: list) -> dict:
        """
        Runs a bar-close backtest over historical candles (same eval row as live manager).
        Returns metrics: win_rate, total_pnl, max_drawdown, trade_count, equity_curve, trades.
        """
        if not candles or len(candles) < 50:
            return {"error": "Not enough historical data"}

        df = self.screener.process_candles(symbol, candles, config, strategy_name)
        if df.empty:
            return {"error": "Failed to calculate indicators"}

        df = prepare_strategy_df(df, strategy_name, config)
        strategy = get_strategy(strategy_name, config)
        merged_config = merge_tp_config(strategy_name, config)
        strat_key = normalize_strategy_name(strategy_name)
        min_confidence = float(merged_config.get("min_confidence", 0.55))

        if strat_key == "CHART_AGENT":
            from app.services.agent.rule_engine import score_at_index

            def _chart_agent_signal(i: int) -> dict:
                insight = score_at_index(df, i, symbol)
                if not insight or insight.confidence < min_confidence:
                    return {"signal": "NONE"}
                if insight.signal not in ("BUY", "SELL"):
                    return {"signal": "NONE"}
                out = {"signal": insight.signal}
                if insight.levels.get("stop_loss_distance") is not None:
                    out["stop_loss_distance"] = insight.levels["stop_loss_distance"]
                if insight.levels.get("take_profit_price") is not None:
                    out["take_profit_price"] = insight.levels["take_profit_price"]
                return out
        else:
            _chart_agent_signal = None

        position = None
        trade_log = []
        equity = 10000.0
        peak_equity = equity
        max_drawdown = 0.0
        equity_curve = []
        sample_stride = max(1, (len(df) - 1) // 200)
        start_i = first_eval_index(df, strategy_name, config)

        def _close_position(bar_time, exit_price, reason: str):
            nonlocal position, equity
            if not position:
                return
            side = position["side"]
            qty = position["qty"]
            entry = position["entry_price"]
            profit = (exit_price - entry) * qty if side == "BUY" else (entry - exit_price) * qty
            equity += profit
            trade_log.append({
                "time": int(bar_time) if bar_time is not None else 0,
                "side": side,
                "price": round(exit_price, 4),
                "quantity": round(qty, 6),
                "pnl": round(profit, 2),
                "is_exit": True,
                "reason": reason,
            })
            position = None

        for i in range(start_i, len(df)):
            row = df.iloc[i].to_dict()
            bar_time = row.get("time")

            if position:
                current_price = row["close"]

                if position["side"] == "BUY" and current_price <= position["stop_loss"]:
                    _close_position(bar_time, position["stop_loss"], "SL")
                elif position["side"] == "SELL" and current_price >= position["stop_loss"]:
                    _close_position(bar_time, position["stop_loss"], "SL")

                if position and position.get("take_profit") is not None:
                    tp = position["take_profit"]
                    if position["side"] == "BUY" and current_price >= tp:
                        _close_position(bar_time, tp, "TP")
                    elif position["side"] == "SELL" and current_price <= tp:
                        _close_position(bar_time, tp, "TP")

                if position and config.get("trailing_stop_percent"):
                    if position["side"] == "BUY":
                        position["high_watermark"] = max(position["high_watermark"], current_price)
                        new_sl = position["high_watermark"] * (
                            1 - config["trailing_stop_percent"] / 100
                        )
                        position["stop_loss"] = max(position["stop_loss"], new_sl)
                    else:
                        position["low_watermark"] = min(position["low_watermark"], current_price)
                        new_sl = position["low_watermark"] * (
                            1 + config["trailing_stop_percent"] / 100
                        )
                        position["stop_loss"] = min(position["stop_loss"], new_sl)

            if not position:
                if _chart_agent_signal is not None:
                    signal_data = _chart_agent_signal(i)
                else:
                    signal_data = strategy.evaluate(row)
                signal = signal_data.get("signal")

                if signal in ("BUY", "SELL"):
                    current_price = row["close"]
                    stop_loss_price = signal_data.get("stop_loss_price")
                    if stop_loss_price is not None:
                        stop_loss = stop_loss_price
                    else:
                        sl_dist = signal_data.get("stop_loss_distance", current_price * 0.02)
                        stop_loss = (
                            current_price - sl_dist if signal == "BUY" else current_price + sl_dist
                        )

                    risk_amount = equity * 0.01
                    price_diff = abs(current_price - stop_loss)
                    if price_diff > 0:
                        qty = risk_amount / price_diff
                        _, tp_price = resolve_take_profit(
                            merged_config, signal_data, signal, current_price
                        )
                        position = {
                            "side": signal,
                            "entry_price": current_price,
                            "qty": qty,
                            "stop_loss": stop_loss,
                            "take_profit": tp_price,
                            "high_watermark": current_price,
                            "low_watermark": current_price,
                        }
                        trade_log.append({
                            "time": int(bar_time) if bar_time is not None else 0,
                            "side": signal,
                            "price": round(current_price, 4),
                            "quantity": round(qty, 6),
                            "pnl": None,
                            "is_exit": False,
                            "reason": "ENTRY",
                        })

            peak_equity = max(peak_equity, equity)
            drawdown = (peak_equity - equity) / peak_equity * 100 if peak_equity else 0
            max_drawdown = max(max_drawdown, drawdown)

            if bar_time is not None and (i % sample_stride == 0 or i == len(df) - 1):
                equity_curve.append({"time": int(bar_time), "equity": round(equity, 2)})

        closed = [t for t in trade_log if t.get("is_exit")]
        winning_trades = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
        total_trades = len(closed)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        return {
            "win_rate": round(win_rate, 2),
            "total_pnl": round(equity - 10000.0, 2),
            "max_drawdown": round(max_drawdown, 2),
            "trade_count": total_trades,
            "equity_curve": equity_curve,
            "starting_equity": 10000.0,
            "trades": trade_log[-100:],
        }
