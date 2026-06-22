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
# Modes: "SIMULATED", "LIVE_ALPACA", "LIVE_BINANCE", "LIVE_ETORO"
TERMINAL_MODE = os.environ.get("TERMINAL_MODE", "SIMULATED")
USE_LIVE_FEEDS = TERMINAL_MODE != "SIMULATED"

# Bot engine on live brokers is opt-in (paper/live safety gate).
ALLOW_LIVE_BOTS = os.environ.get("ALLOW_LIVE_BOTS", "false").lower() in ("1", "true", "yes")
BOT_MIN_CANDLES = int(os.environ.get("BOT_MIN_CANDLES", "200"))
# Default tail size for chart subscribe / candles API (full feed buffer may be larger).
MARKET_CANDLE_SNAPSHOT_LIMIT = int(os.environ.get("MARKET_CANDLE_SNAPSHOT_LIMIT", "600"))
MARKET_CANDLE_SNAPSHOT_MAX = int(os.environ.get("MARKET_CANDLE_SNAPSHOT_MAX", "10080"))
CALIBRATION_CACHE_TTL_SEC = int(os.environ.get("CALIBRATION_CACHE_TTL_SEC", "300"))
# Simulated feed — lightweight startup (defer yfinance SBBS until after listen)
SIM_INITIAL_CANDLE_BARS = int(os.environ.get("SIM_INITIAL_CANDLE_BARS", "600"))
SIM_SBBS_WARM_ON_STARTUP = os.environ.get("SIM_SBBS_WARM_ON_STARTUP", "true").lower() in (
    "1", "true", "yes"
)
SIM_SBBS_WARM_PARALLEL = max(1, min(int(os.environ.get("SIM_SBBS_WARM_PARALLEL", "4")), 12))

# Distributed runtime: all (monolith) | server (WS+feed) | worker (bot engine only)
TERMINAL_ROLE = os.environ.get("TERMINAL_ROLE", "all").lower()
REDIS_URL = os.environ.get("REDIS_URL", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# Optional user strategy plugins in backend/strategies/
ALLOW_CUSTOM_STRATEGIES = os.environ.get("ALLOW_CUSTOM_STRATEGIES", "false").lower() in (
    "1", "true", "yes"
)

# WebSocket Server Settings
WS_HOST = os.environ.get("WS_HOST", "127.0.0.1")
WS_PORT = int(os.environ.get("WS_PORT", "8765"))
# 7-day 1m history payloads exceed the library default (1 MB); allow up to 4 MB frames.
WS_MAX_MESSAGE_SIZE = int(os.environ.get("WS_MAX_MESSAGE_SIZE", str(4 * 1024 * 1024)))
# MessagePack binary frames for large history/tick payloads (Phase 4 transport).
WS_MSGPACK_ENABLED = os.environ.get("WS_MSGPACK_ENABLED", "true").lower() in ("1", "true", "yes")
WS_MSGPACK_MIN_BYTES = int(os.environ.get("WS_MSGPACK_MIN_BYTES", "4096"))

# HTTP REST API (Phase 3) — runs alongside WebSocket in server/all roles
HTTP_ENABLED = os.environ.get("HTTP_ENABLED", "true").lower() in ("1", "true", "yes")
HTTP_HOST = os.environ.get("HTTP_HOST", "127.0.0.1")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8766"))
# Comma-separated origins for CORS, or * for all (dev default)
HTTP_CORS_ORIGINS = os.environ.get("HTTP_CORS_ORIGINS", "*").strip()
# Optional API key for HTTP routes (except /health). Empty = auth disabled.
HTTP_API_KEY = os.environ.get("HTTP_API_KEY", "").strip()

# Pre-Trade Risk Limits
MAX_ORDER_VALUE = 50000.0

# Bot risk limits
BOT_MIN_NOTIONAL = float(os.environ.get("BOT_MIN_NOTIONAL", "10.0"))
BOT_DAILY_LOSS_LIMIT_PCT = float(os.environ.get("BOT_DAILY_LOSS_LIMIT_PCT", "5.0"))
BOT_MAX_ACTIVE_BOTS = int(os.environ.get("BOT_MAX_ACTIVE_BOTS", "20"))
BOT_SNAPSHOT_INTERVAL = float(os.environ.get("BOT_SNAPSHOT_INTERVAL", "300"))
BOT_SNAPSHOT_RETENTION = int(os.environ.get("BOT_SNAPSHOT_RETENTION", "2000"))
BOT_LOG_RETENTION = int(os.environ.get("BOT_LOG_RETENTION", "5000"))
OPTIMIZATION_RETENTION_DAYS = int(os.environ.get("OPTIMIZATION_RETENTION_DAYS", "30"))
BACKTEST_JOB_RETENTION_DAYS = int(os.environ.get("BACKTEST_JOB_RETENTION_DAYS", "14"))

# Portfolio-level risk (all bots combined)
PORTFOLIO_MAX_GROSS_EXPOSURE_PCT = float(os.environ.get("PORTFOLIO_MAX_GROSS_EXPOSURE_PCT", "80"))
PORTFOLIO_MAX_GROUP_EXPOSURE_PCT = float(os.environ.get("PORTFOLIO_MAX_GROUP_EXPOSURE_PCT", "40"))

# Static correlation buckets for group exposure caps
CORRELATION_GROUPS = {
    "TECH": ["AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "AMZN", "META", "NFLX"],
    "INDEX_ETF": ["SPY", "QQQ"],
    "CRYPTO_MAJOR": ["BTCUSDT", "ETHUSDT"],
    "CRYPTO_ALT": ["SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT", "LTCUSDT", "LINKUSDT"],
}

# Long-term market bar archive (1m bars → DB, rollup to 1h after retention window)
ARCHIVE_ENABLED = os.environ.get("ARCHIVE_ENABLED", "true").lower() in ("1", "true", "yes")
ARCHIVE_RETENTION_1M_DAYS = int(os.environ.get("ARCHIVE_RETENTION_1M_DAYS", "90"))
ARCHIVE_RETENTION_1H_DAYS = int(os.environ.get("ARCHIVE_RETENTION_1H_DAYS", "1825"))
ARCHIVE_ROLLUP_INTERVAL = float(os.environ.get("ARCHIVE_ROLLUP_INTERVAL", "3600"))
ARCHIVE_FLUSH_INTERVAL = float(os.environ.get("ARCHIVE_FLUSH_INTERVAL", "60"))
ARCHIVE_BACKEND = os.environ.get("ARCHIVE_BACKEND", "db").lower()
ARCHIVE_BACKFILL_ON_STARTUP = os.environ.get("ARCHIVE_BACKFILL_ON_STARTUP", "false").lower() in (
    "1", "true", "yes"
)
ARCHIVE_PARQUET_ENABLED = os.environ.get("ARCHIVE_PARQUET_ENABLED", "false").lower() in (
    "1", "true", "yes"
)
ARCHIVE_PARQUET_DIR = os.environ.get(
    "ARCHIVE_PARQUET_DIR",
    os.path.join(BASE_DIR, "archive_parquet"),
)

# Sub-minute tick snapshots (trade/quote polls) — optional, short retention
ARCHIVE_TICKS_ENABLED = os.environ.get("ARCHIVE_TICKS_ENABLED", "false").lower() in (
    "1", "true", "yes"
)
ARCHIVE_TICK_RETENTION_HOURS = int(os.environ.get("ARCHIVE_TICK_RETENTION_HOURS", "24"))
ARCHIVE_TICK_FLUSH_INTERVAL = float(os.environ.get("ARCHIVE_TICK_FLUSH_INTERVAL", "30"))

if ARCHIVE_BACKEND not in ("db", "parquet", "both", ""):
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "ARCHIVE_BACKEND=%r unknown; using db.",
        ARCHIVE_BACKEND,
    )
    ARCHIVE_BACKEND = "db"
if ARCHIVE_BACKEND in ("parquet", "both"):
    ARCHIVE_PARQUET_ENABLED = True

# Chart Analyst Agent
AGENT_ENABLED = os.environ.get("AGENT_ENABLED", "true").lower() in ("1", "true", "yes")
AGENT_LLM_ENABLED = os.environ.get("AGENT_LLM_ENABLED", "false").lower() in ("1", "true", "yes")
AGENT_LLM_MIN_CONFIDENCE = float(os.environ.get("AGENT_LLM_MIN_CONFIDENCE", "0.55"))
AGENT_LLM_COOLDOWN_SEC = int(os.environ.get("AGENT_LLM_COOLDOWN_SEC", "300"))
AGENT_LLM_SIM_COOLDOWN_SEC = int(os.environ.get("AGENT_LLM_SIM_COOLDOWN_SEC", "30"))
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
AGENT_LLM_MODEL = os.environ.get("AGENT_LLM_MODEL", "openai/gpt-4o-mini")
AGENT_LLM_MODEL_DEEP = os.environ.get("AGENT_LLM_MODEL_DEEP", "").strip() or AGENT_LLM_MODEL
# LLM provider: auto | ollama | openrouter | off
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "auto").lower()
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip()
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b").strip()
OLLAMA_MODEL_NARRATOR = os.environ.get("OLLAMA_MODEL_NARRATOR", "").strip() or OLLAMA_MODEL
OLLAMA_MODEL_DEEP = os.environ.get("OLLAMA_MODEL_DEEP", "").strip()
OLLAMA_TIMEOUT_SEC = float(os.environ.get("OLLAMA_TIMEOUT_SEC", "60"))
_ollama_reasoning_effort = os.environ.get("OLLAMA_REASONING_EFFORT", "none").strip().lower()
OLLAMA_REASONING_EFFORT = (
    _ollama_reasoning_effort
    if _ollama_reasoning_effort in ("none", "low", "medium", "high")
    else "none"
)
AGENT_LLM_PREFER_LOCAL = os.environ.get("AGENT_LLM_PREFER_LOCAL", "true").lower() in ("1", "true", "yes")
AGENT_LLM_FALLBACK_CLOUD = os.environ.get("AGENT_LLM_FALLBACK_CLOUD", "false").lower() in ("1", "true", "yes")
BACKTEST_REASONING_MAX_TRADES = int(os.environ.get("BACKTEST_REASONING_MAX_TRADES", "20"))

# Market scanner + on-demand vision
SCANNER_ENABLED = os.environ.get("SCANNER_ENABLED", "true").lower() in ("1", "true", "yes")
AGENT_VISION_ENABLED = os.environ.get("AGENT_VISION_ENABLED", "false").lower() in ("1", "true", "yes")
AGENT_VISION_MODEL = os.environ.get("AGENT_VISION_MODEL", "openai/gpt-4o-mini")
AGENT_VISION_CACHE_SEC = int(os.environ.get("AGENT_VISION_CACHE_SEC", str(4 * 3600)))

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

# eToro Public API Credentials & URLs
# Auth is EITHER a Bearer token (from SSO) OR an API-key pair (x-api-key + x-user-key) — NEVER both.
ETORO_API_BASE = os.environ.get("ETORO_API_BASE", "https://public-api.etoro.com/api/v1")
ETORO_ACCESS_TOKEN = os.environ.get("ETORO_ACCESS_TOKEN", "")  # SSO Bearer token
ETORO_API_KEY = os.environ.get("ETORO_API_KEY", "")            # partner x-api-key
ETORO_USER_KEY = os.environ.get("ETORO_USER_KEY", "")          # per-user x-user-key
# eToro has no public market-data WebSocket; poll the rates endpoint on this interval (seconds).
ETORO_POLL_INTERVAL = float(os.environ.get("ETORO_POLL_INTERVAL", "1.0"))
# Account env: "demo", "real", or "auto" (probe /trading/info/real/pnl once at startup).
ETORO_ENV = os.environ.get("ETORO_ENV", "auto")
# Minimum spacing between trade-execution POSTs (eToro: 20 req/min shared limit).
ETORO_EXEC_MIN_INTERVAL = float(os.environ.get("ETORO_EXEC_MIN_INTERVAL", "3.0"))

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
elif TERMINAL_MODE == "LIVE_ETORO":
    # eToro is unique: a single API covers both equities and crypto, so the
    # live eToro feed can serve the full merged pool that until now only
    # the simulator could offer.
    SYMBOLS = {**EQUITY_SYMBOLS, **CRYPTO_SYMBOLS}
else: # "SIMULATED"
    # Merge both for a wider mock trading pool
    SYMBOLS = {**EQUITY_SYMBOLS, **CRYPTO_SYMBOLS}
