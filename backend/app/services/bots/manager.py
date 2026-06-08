import logging
import json
import uuid
import asyncio
from app.database import get_connection
from app.services.bots.strategies import get_strategy

class BotManagerService:
    def __init__(self, oms_service, screener_service, broadcast_cb):
        self.logger = logging.getLogger(__name__)
        self.oms = oms_service
        self.screener = screener_service
        self.broadcast_cb = broadcast_cb
        self.active_bots = {} # Dict of bot_id -> bot_data
        
    def load_bots_from_db(self):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bots WHERE status = 'RUNNING'")
        rows = cursor.fetchall()
        for row in rows:
            bot_id = row['id']
            self.active_bots[bot_id] = dict(row)
            self.active_bots[bot_id]['config'] = json.loads(row['config'])
            # initialize strategy instance
            self.active_bots[bot_id]['strategy_instance'] = get_strategy(row['strategy'], self.active_bots[bot_id]['config'])
        conn.close()
        self.logger.info(f"Loaded {len(self.active_bots)} active bots from DB.")

    async def log_bot_event(self, bot_id: str, level: str, message: str):
        self.logger.info(f"[BOT {bot_id}] {level} - {message}")
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO bot_logs (bot_id, level, message) VALUES (?, ?, ?)", (bot_id, level, message))
        conn.commit()
        conn.close()
        
        # Broadcast to frontend
        await self.broadcast_cb({
            "type": "bot_log",
            "data": {"bot_id": bot_id, "level": level, "message": message}
        })

    def get_account_balance(self):
        return self.oms.get_account_data().get('USD', {}).get('balance', 0)

    def get_recent_logs(self, limit: int = 100):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT bot_id, level, message, timestamp FROM bot_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    async def process_market_tick(self, symbol: str, ohlcv_data: list):
        """Called by the main loop whenever new candles arrive"""
        # 1. Screen indicators
        df = self.screener.process_candles(symbol, ohlcv_data)
        if df.empty:
            return

        # Prepare 'previous' values for logic evaluation
        df['MACDh_12_26_9_prev'] = df['MACDh_12_26_9'].shift(1)
        df['SUPERTd_14_3.0_prev'] = df['SUPERTd_14_3.0'].shift(1)
        df['close_prev'] = df['close'].shift(1)

        latest_row = df.iloc[-1].to_dict()

        # 2. Iterate through bots watching this symbol
        for bot_id, bot in self.active_bots.items():
            if bot['symbol'] != symbol:
                continue

            strat = bot.get('strategy_instance')
            if not strat:
                continue
                
            signal_data = strat.evaluate(latest_row)
            signal = signal_data.get('signal')
            
            if signal in ('BUY', 'SELL'):
                await self._execute_signal(bot, signal, signal_data, latest_row['close'])

    async def _execute_signal(self, bot, side, signal_data, current_price):
        bot_id = bot['id']
        symbol = bot['symbol']
        
        # Risk Management: 1% Rule with ATR Sizing
        account_balance = self.get_account_balance()
        risk_amount = account_balance * 0.01 # 1% Risk
        
        stop_loss_price = signal_data.get('stop_loss_price')
        if not stop_loss_price:
            sl_dist = signal_data.get('stop_loss_distance', current_price * 0.02) # fallback 2%
            if side == 'BUY':
                stop_loss_price = current_price - sl_dist
            else:
                stop_loss_price = current_price + sl_dist
                
        # Handle zero division
        price_diff = abs(current_price - stop_loss_price)
        if price_diff <= 0:
            await self.log_bot_event(bot_id, "ERROR", "Calculated stop loss distance is 0. Aborting trade.")
            return

        # Position Size = (Account Balance × 1%) ÷ (Entry Price − Stop Loss Price)
        position_size = risk_amount / price_diff
        
        # Max allocation cap (e.g. don't use more than allocated capital)
        notional_value = position_size * current_price
        if notional_value > bot['allocation']:
            position_size = bot['allocation'] / current_price
            await self.log_bot_event(bot_id, "WARN", f"ATR position size exceeded allocation cap. Reduced to {position_size:.4f} shares.")
            
        # Ensure we meet minimums
        if position_size < 0.001:
            await self.log_bot_event(bot_id, "INFO", "Signal ignored: Position size too small based on risk.")
            return
            
        await self.log_bot_event(bot_id, "INFO", f"Triggered {side} signal. Price: {current_price:.2f}, SL: {stop_loss_price:.2f}, Qty: {position_size:.4f}")
        
        # Submit to OMS
        try:
            result = await self.oms.place_order({
                "symbol": symbol,
                "type": "MARKET",
                "side": side,
                "quantity": position_size,
                "stop_loss_percent": bot.get('config', {}).get('trailing_stop_percent') or bot.get('config', {}).get('stop_loss_percent'),
                "take_profit_percent": bot.get('config', {}).get('take_profit_percent')
            })
            if result.get("status") == "success":
                order_id = result.get("order_id")
                await self.log_bot_event(bot_id, "SUCCESS", f"Placed {side} order {order_id}.")
            else:
                await self.log_bot_event(bot_id, "ERROR", f"Order failed: {result.get('message')}")
        except Exception as e:
            await self.log_bot_event(bot_id, "ERROR", f"Order exception: {str(e)}")

    async def create_bot(self, strategy: str, symbol: str, timeframe: str, allocation: float, config: dict):
        bot_id = str(uuid.uuid4())
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (bot_id, strategy, symbol, timeframe, 'RUNNING', allocation, json.dumps(config))
        )
        conn.commit()
        conn.close()
        
        self.active_bots[bot_id] = {
            'id': bot_id,
            'strategy': strategy,
            'symbol': symbol,
            'timeframe': timeframe,
            'status': 'RUNNING',
            'allocation': allocation,
            'config': config,
            'strategy_instance': get_strategy(strategy, config)
        }
        
        await self.log_bot_event(bot_id, "INFO", f"Bot created and started for {symbol} using {strategy}.")
        return bot_id

    async def stop_bot(self, bot_id: str):
        if bot_id in self.active_bots:
            del self.active_bots[bot_id]
            
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE bots SET status = 'STOPPED' WHERE id = ?", (bot_id,))
        conn.commit()
        conn.close()
        
        await self.log_bot_event(bot_id, "INFO", "Bot stopped.")
