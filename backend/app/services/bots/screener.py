import importlib.metadata
import pandas as pd
import pandas_ta as ta
import logging

class MarketScreenerService:
    """
    Calculates all technical indicators for crypto day trading using pandas-ta.
    Takes OHLCV data and appends indicator columns required by the bots.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def process_candles(self, symbol: str, ohlcv_data: list) -> pd.DataFrame:
        """
        Converts a list of candle dicts to a DataFrame and computes all required indicators.
        ohlcv_data format: [{'time': ts, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v}, ...]
        """
        if not ohlcv_data or len(ohlcv_data) < 50:
            return pd.DataFrame() # Not enough data
            
        df = pd.DataFrame(ohlcv_data)
        
        # Ensure correct types
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                
        # Drop rows with NaN in price columns
        df = df.dropna(subset=['open', 'high', 'low', 'close'])
        
        if len(df) < 50:
            return pd.DataFrame()

        try:
            # 1. MACD (12, 26, 9)
            macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
            if macd is not None:
                df = pd.concat([df, macd], axis=1)
                
            # 2. RSI (14)
            df['RSI_14'] = ta.rsi(df['close'], length=14)
            
            # 3. Stochastic (14, 3, 3)
            stoch = ta.stoch(df['high'], df['low'], df['close'], k=14, d=3, smooth_k=3)
            if stoch is not None:
                df = pd.concat([df, stoch], axis=1)
                
            # 4. Bollinger Bands (20, 2)
            bb = ta.bbands(df['close'], length=20, std=2)
            if bb is not None:
                df = pd.concat([df, bb], axis=1)
                
            # 5. ATR (14)
            df['ATR_14'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            
            # 6. SuperTrend (14, 3)
            st = ta.supertrend(df['high'], df['low'], df['close'], length=14, multiplier=3.0)
            if st is not None:
                df = pd.concat([df, st], axis=1)
                
            # 7. ADX (14)
            adx = ta.adx(df['high'], df['low'], df['close'], length=14)
            if adx is not None:
                df = pd.concat([df, adx], axis=1)
                
            # 8. VWAP (Requires timestamp index for proper session grouping, but simple is fine here)
            # VWAP usually requires a datetime index
            if 'time' in df.columns:
                df['datetime'] = pd.to_datetime(df['time'], unit='s')
                df.set_index('datetime', inplace=False)
                vwap = ta.vwap(df['high'], df['low'], df['close'], df['volume'])
                if vwap is not None:
                    df['VWAP'] = vwap.values

        except Exception as e:
            self.logger.error(f"Error calculating indicators for {symbol}: {e}")
            
        return df
