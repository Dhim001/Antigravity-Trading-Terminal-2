import logging

class BaseStrategy:
    def __init__(self, config: dict):
        self.config = config
        
    def evaluate(self, df_row) -> dict:
        """
        Takes the latest row of a pandas DataFrame (with indicators attached).
        Returns a dict: {'signal': 'BUY'|'SELL'|'CLOSE'|'NONE', 'stop_loss_price': float}
        """
        return {'signal': 'NONE'}

class MacdRsiStrategy(BaseStrategy):
    """Strategy 1: MACD + RSI + Mean Reversion"""
    def evaluate(self, df_row) -> dict:
        try:
            # MACD_12_26_9, MACDs_12_26_9, MACDh_12_26_9
            macd_hist = df_row.get('MACDh_12_26_9', 0)
            macd_hist_prev = df_row.get('MACDh_12_26_9_prev', 0) # Requires shift in screener
            rsi = df_row.get('RSI_14', 50)
            atr = df_row.get('ATR_14', 0)
            close = df_row.get('close', 0)

            # Long Entry
            if macd_hist > 0 and macd_hist_prev <= 0:
                if rsi < 50:
                    return {'signal': 'BUY', 'stop_loss_distance': 1.5 * atr}
            
            # Short Entry
            if macd_hist < 0 and macd_hist_prev >= 0:
                if rsi > 50:
                    return {'signal': 'SELL', 'stop_loss_distance': 1.5 * atr}
                    
        except Exception as e:
            pass
        return {'signal': 'NONE'}

class BrsScalpingStrategy(BaseStrategy):
    """Strategy 2: Bollinger, RSI, Stochastic"""
    def evaluate(self, df_row) -> dict:
        try:
            # BBL_20_2.0 (Lower), BBU_20_2.0 (Upper), BBM_20_2.0 (Middle)
            bbl = df_row.get('BBL_20_2.0')
            bbu = df_row.get('BBU_20_2.0')
            bbm = df_row.get('BBM_20_2.0')
            rsi = df_row.get('RSI_14')
            stoch_k = df_row.get('STOCHk_14_3_3')
            close = df_row.get('close')
            atr = df_row.get('ATR_14')
            
            if None in (bbl, bbu, rsi, stoch_k, close):
                return {'signal': 'NONE'}

            if close < bbl and rsi < 30 and stoch_k < 20:
                return {'signal': 'BUY', 'stop_loss_distance': 1.5 * atr, 'take_profit_price': bbm}
                
            if close > bbu and rsi > 70 and stoch_k > 80:
                return {'signal': 'SELL', 'stop_loss_distance': 1.5 * atr, 'take_profit_price': bbm}
                
        except Exception:
            pass
        return {'signal': 'NONE'}

class SupertrendAdxStrategy(BaseStrategy):
    """Strategy 3: SuperTrend + ADX"""
    def evaluate(self, df_row) -> dict:
        try:
            # SUPERTd_14_3.0 is the direction (1 for up, -1 for down)
            # SUPERT_14_3.0 is the line value
            st_dir = df_row.get('SUPERTd_14_3.0')
            st_dir_prev = df_row.get('SUPERTd_14_3.0_prev')
            st_val = df_row.get('SUPERT_14_3.0')
            adx = df_row.get('ADX_14')
            close = df_row.get('close')
            
            if None in (st_dir, st_dir_prev, adx):
                return {'signal': 'NONE'}
                
            # Flipping green
            if st_dir == 1 and st_dir_prev == -1 and adx > 25:
                return {'signal': 'BUY', 'stop_loss_price': st_val} # Stop exactly at SuperTrend line
                
            # Flipping red
            if st_dir == -1 and st_dir_prev == 1 and adx > 25:
                return {'signal': 'SELL', 'stop_loss_price': st_val}
                
        except Exception:
            pass
        return {'signal': 'NONE'}

class VwapPullbackStrategy(BaseStrategy):
    """Strategy 4: VWAP Pullback"""
    def evaluate(self, df_row) -> dict:
        try:
            vwap = df_row.get('VWAP')
            close = df_row.get('close')
            close_prev = df_row.get('close_prev')
            atr = df_row.get('ATR_14')
            
            if None in (vwap, close, close_prev):
                return {'signal': 'NONE'}
                
            # Crossed VWAP from above (Pullback to VWAP)
            if close_prev > vwap and close <= vwap:
                return {'signal': 'BUY', 'stop_loss_distance': 1.5 * atr}
                
            if close_prev < vwap and close >= vwap:
                return {'signal': 'SELL', 'stop_loss_distance': 1.5 * atr}
                
        except Exception:
            pass
        return {'signal': 'NONE'}

def get_strategy(strategy_name: str, config: dict) -> BaseStrategy:
    strategies = {
        'MACD_RSI': MacdRsiStrategy,
        'BRS_SCALPING': BrsScalpingStrategy,
        'SUPERTREND_ADX': SupertrendAdxStrategy,
        'VWAP_PULLBACK': VwapPullbackStrategy
    }
    strat_class = strategies.get(strategy_name.upper(), BaseStrategy)
    return strat_class(config)
