import os

# Base Directory & Database Path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "trading.db")

# Helper to load .env manually if python-dotenv is not installed
env_path = os.path.join(os.path.dirname(BASE_DIR), ".env")
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

# Features & Integration Flags
# Modes: "SIMULATED", "LIVE_ALPACA", "LIVE_BINANCE"
TERMINAL_MODE = os.environ.get("TERMINAL_MODE", "SIMULATED")
USE_LIVE_FEEDS = TERMINAL_MODE != "SIMULATED"

# WebSocket Server Settings
WS_HOST = "localhost"
WS_PORT = 8765

# Pre-Trade Risk Limits
MAX_ORDER_VALUE = 50000.0

# Simulation Settings Defaults
DEFAULT_TICK_INTERVAL = 0.25
DEFAULT_VOLATILITY_MULTIPLIER = 1.0

# Alpaca Credentials & URLs
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_DATA_URL = os.environ.get("ALPACA_DATA_URL", "wss://stream.data.alpaca.markets/v2/sip")

# Binance Credentials & URLs
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY", "")
BINANCE_BASE_URL = os.environ.get("BINANCE_BASE_URL", "https://api.binance.com")
BINANCE_WS_URL = os.environ.get("BINANCE_WS_URL", "wss://stream.binance.com:9443")

# Detailed symbol catalog lists
EQUITY_SYMBOLS = {
    "AAPL": {"price": 182.50, "volatility": 0.0001, "decimals": 2, "asset": "AAPL", "quote": "USD"},
    "TSLA": {"price": 178.20, "volatility": 0.0003, "decimals": 2, "asset": "TSLA", "quote": "USD"},
    "MSFT": {"price": 420.10, "volatility": 0.00008, "decimals": 2, "asset": "MSFT", "quote": "USD"},
    "NVDA": {"price": 875.12, "volatility": 0.0004, "decimals": 2, "asset": "NVDA", "quote": "USD"},
    "AMD": {"price": 160.20, "volatility": 0.0003, "decimals": 2, "asset": "AMD", "quote": "USD"},
    "GOOGL": {"price": 175.40, "volatility": 0.00012, "decimals": 2, "asset": "GOOGL", "quote": "USD"},
    "AMZN": {"price": 180.50, "volatility": 0.00015, "decimals": 2, "asset": "AMZN", "quote": "USD"},
    "NFLX": {"price": 610.80, "volatility": 0.00022, "decimals": 2, "asset": "NFLX", "quote": "USD"},
    "META": {"price": 485.60, "volatility": 0.0002, "decimals": 2, "asset": "META", "quote": "USD"},
    "COIN": {"price": 240.50, "volatility": 0.0005, "decimals": 2, "asset": "COIN", "quote": "USD"},
    "SPY": {"price": 510.20, "volatility": 0.00006, "decimals": 2, "asset": "SPY", "quote": "USD"},
    "QQQ": {"price": 435.50, "volatility": 0.00008, "decimals": 2, "asset": "QQQ", "quote": "USD"},
    "JPM": {"price": 195.40, "volatility": 0.0001, "decimals": 2, "asset": "JPM", "quote": "USD"},
    "V": {"price": 275.60, "volatility": 0.00007, "decimals": 2, "asset": "V", "quote": "USD"},
    "DIS": {"price": 115.30, "volatility": 0.00014, "decimals": 2, "asset": "DIS", "quote": "USD"}
}

CRYPTO_SYMBOLS = {
    "BTCUSDT": {"price": 68500.0, "volatility": 0.00015, "decimals": 2, "asset": "BTC", "quote": "USDT"},
    "ETHUSDT": {"price": 3520.0, "volatility": 0.0002, "decimals": 2, "asset": "ETH", "quote": "USDT"},
    "SOLUSDT": {"price": 145.50, "volatility": 0.0004, "decimals": 2, "asset": "SOL", "quote": "USDT"},
    "BNBUSDT": {"price": 580.20, "volatility": 0.00018, "decimals": 2, "asset": "BNB", "quote": "USDT"},
    "XRPUSDT": {"price": 0.5200, "volatility": 0.00025, "decimals": 4, "asset": "XRP", "quote": "USDT"},
    "ADAUSDT": {"price": 0.4500, "volatility": 0.00028, "decimals": 4, "asset": "ADA", "quote": "USDT"},
    "DOGEUSDT": {"price": 0.1450, "volatility": 0.00045, "decimals": 4, "asset": "DOGE", "quote": "USDT"},
    "DOTUSDT": {"price": 6.80, "volatility": 0.0003, "decimals": 2, "asset": "DOT", "quote": "USDT"},
    "LTCUSDT": {"price": 82.40, "volatility": 0.00022, "decimals": 2, "asset": "LTC", "quote": "USDT"},
    "LINKUSDT": {"price": 15.20, "volatility": 0.00032, "decimals": 2, "asset": "LINK", "quote": "USDT"}
}

# Supported Trading Symbols & Properties based on mode
if TERMINAL_MODE == "LIVE_ALPACA":
    SYMBOLS = EQUITY_SYMBOLS
elif TERMINAL_MODE == "LIVE_BINANCE":
    SYMBOLS = CRYPTO_SYMBOLS
else: # "SIMULATED"
    # Merge both for a wider mock trading pool
    SYMBOLS = {**EQUITY_SYMBOLS, **CRYPTO_SYMBOLS}
