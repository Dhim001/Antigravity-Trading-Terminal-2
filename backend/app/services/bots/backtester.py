import pandas as pd
from app.services.bots.strategies import get_strategy

class BacktesterService:
    def __init__(self, screener_service):
        self.screener = screener_service

    def run_backtest(self, symbol: str, strategy_name: str, config: dict, candles: list) -> dict:
        """
        Runs a vectorized backtest over the provided candles.
        Returns metrics: win_rate, total_pnl, max_drawdown, trade_count.
        """
        if not candles or len(candles) < 50:
            return {"error": "Not enough historical data"}

        # Calculate indicators
        df = self.screener.process_candles(symbol, candles)
        if df.empty:
            return {"error": "Failed to calculate indicators"}

        df['MACDh_12_26_9_prev'] = df['MACDh_12_26_9'].shift(1)
        df['SUPERTd_14_3.0_prev'] = df['SUPERTd_14_3.0'].shift(1)
        df['close_prev'] = df['close'].shift(1)

        strategy = get_strategy(strategy_name, config)
        if not strategy:
            return {"error": "Invalid strategy"}

        position = None
        trades = []
        equity = 10000.0  # Starting capital
        peak_equity = equity
        max_drawdown = 0.0

        for i in range(1, len(df)):
            row = df.iloc[i].to_dict()
            
            # If in position, check SL/TP or trailing stop (simplified for backtest)
            if position:
                current_price = row['close']
                
                # Check Stop Loss
                if position['side'] == 'BUY' and current_price <= position['stop_loss']:
                    profit = (position['stop_loss'] - position['entry_price']) * position['qty']
                    equity += profit
                    trades.append({"profit": profit, "type": "SL"})
                    position = None
                elif position['side'] == 'SELL' and current_price >= position['stop_loss']:
                    profit = (position['entry_price'] - position['stop_loss']) * position['qty']
                    equity += profit
                    trades.append({"profit": profit, "type": "SL"})
                    position = None
                
                # Update Trailing Stop logic if config has trailing stop
                if position and config.get('trailing_stop_percent'):
                    if position['side'] == 'BUY':
                        position['high_watermark'] = max(position['high_watermark'], current_price)
                        new_sl = position['high_watermark'] * (1 - config['trailing_stop_percent']/100)
                        position['stop_loss'] = max(position['stop_loss'], new_sl)
                    else:
                        position['low_watermark'] = min(position['low_watermark'], current_price)
                        new_sl = position['low_watermark'] * (1 + config['trailing_stop_percent']/100)
                        position['stop_loss'] = min(position['stop_loss'], new_sl)
            
            # If not in position, evaluate signal
            if not position:
                signal_data = strategy.evaluate(row)
                signal = signal_data.get('signal')
                
                if signal in ('BUY', 'SELL'):
                    current_price = row['close']
                    sl_dist = signal_data.get('stop_loss_distance', current_price * 0.02)
                    stop_loss = current_price - sl_dist if signal == 'BUY' else current_price + sl_dist
                    
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
                            "low_watermark": current_price
                        }

            # Track drawdown
            peak_equity = max(peak_equity, equity)
            drawdown = (peak_equity - equity) / peak_equity * 100
            max_drawdown = max(max_drawdown, drawdown)

        winning_trades = sum(1 for t in trades if t['profit'] > 0)
        total_trades = len(trades)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        return {
            "win_rate": round(win_rate, 2),
            "total_pnl": round(equity - 10000.0, 2),
            "max_drawdown": round(max_drawdown, 2),
            "trade_count": total_trades
        }
