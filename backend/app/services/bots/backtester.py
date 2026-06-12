from app.services.bots.indicators import first_eval_index, prepare_strategy_df
from app.services.bots.strategies import get_strategy


class BacktesterService:
    def __init__(self, screener_service):
        self.screener = screener_service

    def run_backtest(self, symbol: str, strategy_name: str, config: dict, candles: list) -> dict:
        """
        Runs a bar-close backtest over historical candles (same eval row as live manager).
        Returns metrics: win_rate, total_pnl, max_drawdown, trade_count, equity_curve.
        """
        if not candles or len(candles) < 50:
            return {"error": "Not enough historical data"}

        df = self.screener.process_candles(symbol, candles, config, strategy_name)
        if df.empty:
            return {"error": "Failed to calculate indicators"}

        df = prepare_strategy_df(df, strategy_name, config)
        strategy = get_strategy(strategy_name, config)

        position = None
        trades = []
        equity = 10000.0
        peak_equity = equity
        max_drawdown = 0.0
        equity_curve = []
        sample_stride = max(1, (len(df) - 1) // 200)
        start_i = first_eval_index(df, strategy_name, config)

        for i in range(start_i, len(df)):
            row = df.iloc[i].to_dict()

            if position:
                current_price = row["close"]

                if position["side"] == "BUY" and current_price <= position["stop_loss"]:
                    profit = (position["stop_loss"] - position["entry_price"]) * position["qty"]
                    equity += profit
                    trades.append({"profit": profit, "type": "SL"})
                    position = None
                elif position["side"] == "SELL" and current_price >= position["stop_loss"]:
                    profit = (position["entry_price"] - position["stop_loss"]) * position["qty"]
                    equity += profit
                    trades.append({"profit": profit, "type": "SL"})
                    position = None

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
                        position = {
                            "side": signal,
                            "entry_price": current_price,
                            "qty": qty,
                            "stop_loss": stop_loss,
                            "high_watermark": current_price,
                            "low_watermark": current_price,
                        }

            peak_equity = max(peak_equity, equity)
            drawdown = (peak_equity - equity) / peak_equity * 100
            max_drawdown = max(max_drawdown, drawdown)

            bar_time = row.get("time")
            if bar_time is not None and (i % sample_stride == 0 or i == len(df) - 1):
                equity_curve.append({"time": int(bar_time), "equity": round(equity, 2)})

        winning_trades = sum(1 for t in trades if t["profit"] > 0)
        total_trades = len(trades)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        return {
            "win_rate": round(win_rate, 2),
            "total_pnl": round(equity - 10000.0, 2),
            "max_drawdown": round(max_drawdown, 2),
            "trade_count": total_trades,
            "equity_curve": equity_curve,
            "starting_equity": 10000.0,
        }
