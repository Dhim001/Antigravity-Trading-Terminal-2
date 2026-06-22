import os
import time
import logging
import pandas as pd
import yfinance as yf
from arch.bootstrap import StationaryBootstrap

logger = logging.getLogger(__name__)

# Map our app symbols to yfinance symbols
YF_SYMBOL_MAP = {
    "BTCUSDT": "BTC-USD",
    "ETHUSDT": "ETH-USD",
    "SOLUSDT": "SOL-USD",
    "BNBUSDT": "BNB-USD",
    "XRPUSDT": "XRP-USD",
    "ADAUSDT": "ADA-USD",
    "DOGEUSDT": "DOGE-USD",
    "DOTUSDT": "DOT-USD",
    "LTCUSDT": "LTC-USD",
    "LINKUSDT": "LINK-USD",
    "AAPL": "AAPL",
    "TSLA": "TSLA",
    "MSFT": "MSFT"
}

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)

class SBBSGenerator:
    def __init__(self, symbol: str, block_size: int = 60, *, defer_fetch: bool = False):
        self.symbol = symbol
        self.yf_symbol = YF_SYMBOL_MAP.get(symbol, symbol)
        self.block_size = block_size
        self.empirical_data = None
        self.bs = None
        self.buffer = []
        self.components = None

        self._load_or_fetch_data(defer_fetch=defer_fetch)
        if self.empirical_data is not None and not self.empirical_data.empty:
            self._prepare_bootstrap()

    def ensure_loaded(self) -> bool:
        """Fetch yfinance data if deferred or missing; return True when ready."""
        if self.empirical_data is not None and not self.empirical_data.empty:
            return True
        self._load_or_fetch_data(defer_fetch=False)
        if self.empirical_data is not None and not self.empirical_data.empty:
            self._prepare_bootstrap()
            return True
        return False
        
    def _load_or_fetch_data(self, *, defer_fetch: bool = False):
        file_path = os.path.join(DATA_DIR, f"{self.symbol}_7d_1m.parquet")
        
        # Check if we have a recent file (less than 24h old)
        if os.path.exists(file_path):
            mtime = os.path.getmtime(file_path)
            if time.time() - mtime < 86400:
                logger.info(f"Loading cached empirical data for {self.symbol} from {file_path}")
                self.empirical_data = pd.read_parquet(file_path)
                return

        if defer_fetch:
            logger.debug("Deferring yfinance fetch for %s (no fresh cache)", self.symbol)
            return
                
        logger.info(f"Fetching 7 days of 1m empirical data for {self.symbol} from yfinance...")
        ticker = yf.Ticker(self.yf_symbol)
        df = ticker.history(period="7d", interval="1m")
        
        if df.empty:
            logger.warning(f"Failed to fetch data for {self.symbol}. Falling back to cached if exists.")
            if os.path.exists(file_path):
                self.empirical_data = pd.read_parquet(file_path)
                return
            else:
                raise ValueError(f"No data available for {self.symbol} and fetch failed.")
                
        # Save to parquet
        df.to_parquet(file_path)
        self.empirical_data = df
        
    def _prepare_bootstrap(self):
        if self.empirical_data is None or self.empirical_data.empty:
            return
        df = self.empirical_data.copy()
        
        # Calculate intra-candle ratios relative to Open, and Open's return relative to prev Close
        # O_ret = (Open - prev_Close) / prev_Close
        df['prev_close'] = df['Close'].shift(1)
        df['O_ret'] = (df['Open'] - df['prev_close']) / df['prev_close']
        
        # H_ret, L_ret, C_ret are relative to Open
        df['H_ret'] = (df['High'] - df['Open']) / df['Open']
        df['L_ret'] = (df['Low'] - df['Open']) / df['Open']
        df['C_ret'] = (df['Close'] - df['Open']) / df['Open']
        
        df = df.dropna(subset=['O_ret', 'H_ret', 'L_ret', 'C_ret', 'Volume'])
        
        # We only need these columns for bootstrapping
        self.components = df[['O_ret', 'H_ret', 'L_ret', 'C_ret', 'Volume']].values
        
        # Initialize the Stationary Bootstrap
        # The block size defines the average block length (geometric distribution)
        # 60 means average block is 60 minutes, preserving 1-hour temporal structures
        self.bs = StationaryBootstrap(self.block_size, self.components)
        
    def _fill_buffer(self):
        # Generate exactly 1 resampled path of the same length as the original data
        for data in self.bs.bootstrap(1):
            resampled_components = data[0][0] # arch bootstrap yields ((data,), kwargs)
            # data[0][0] is the resampled self.components
            # convert back to a list of dicts or tuples for quick popping
            self.buffer.extend(resampled_components.tolist())
            break # only 1 iteration
            
    def get_next(self):
        if not self.buffer:
            self._fill_buffer()
        return self.buffer.pop(0)
