import os

# Base Directory & Database Path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(BASE_DIR)


def _load_env_file(path: str) -> None:
    """Load KEY=VALUE pairs into os.environ (manual dotenv; no external deps)."""
    if not path or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()


# Base secrets/overrides, then optional dual-instance profile (profile wins on conflict).
_load_env_file(os.path.join(_REPO_ROOT, ".env"))
_terminal_profile = os.environ.get("TERMINAL_PROFILE", "").strip().lower()
if _terminal_profile:
    _load_env_file(os.path.join(_REPO_ROOT, "env.profiles", f"{_terminal_profile}.env"))

_sqlite_db = os.environ.get("SQLITE_DB_PATH", "").strip()
DB_PATH = (
    os.path.join(BASE_DIR, _sqlite_db)
    if _sqlite_db
    else os.path.join(BASE_DIR, "trading.db")
)

# Features & Integration Flags
# Modes: "SIMULATED", "LIVE_ALPACA", "LIVE_BINANCE", "LIVE_ETORO", "LIVE_IB", "LIVE_MASSIVE"
TERMINAL_MODE = os.environ.get("TERMINAL_MODE", "SIMULATED")
USE_LIVE_FEEDS = TERMINAL_MODE != "SIMULATED"
# Operator/admin UI — exposed on GET /api/v1/session as operator_mode.
OPERATOR_MODE = os.environ.get("OPERATOR_MODE", "true").lower() in ("1", "true", "yes")

# Bot engine on live brokers is opt-in (paper/live safety gate).
ALLOW_LIVE_BOTS = os.environ.get("ALLOW_LIVE_BOTS", "false").lower() in ("1", "true", "yes")
# Deploy gate — block bot_create when linked backtest fails OOS/WF prerequisites.
DEPLOY_GATE_ENABLED = os.environ.get("DEPLOY_GATE_ENABLED", "true").lower() in ("1", "true", "yes")
DEPLOY_MIN_OOS_PNL = float(os.environ.get("DEPLOY_MIN_OOS_PNL", "0"))
DEPLOY_MIN_OOS_TRADES = int(os.environ.get("DEPLOY_MIN_OOS_TRADES", "1"))
DEPLOY_MIN_STABILITY_SCORE = float(os.environ.get("DEPLOY_MIN_STABILITY_SCORE", "0.5"))
DEPLOY_MAX_DRAWDOWN_WARN_PCT = float(os.environ.get("DEPLOY_MAX_DRAWDOWN_WARN_PCT", "25"))
BOT_MIN_CANDLES = int(os.environ.get("BOT_MIN_CANDLES", "200"))
# Chart analyst / agent scoring (MACD/RSI warm-up); lower than bot backtest minimum.
AGENT_MIN_CANDLES = int(os.environ.get("AGENT_MIN_CANDLES", "50"))
# Default tail size for chart subscribe / candles API (full feed buffer may be larger).
MARKET_CANDLE_SNAPSHOT_LIMIT = int(os.environ.get("MARKET_CANDLE_SNAPSHOT_LIMIT", "600"))
MARKET_CANDLE_SNAPSHOT_MAX = int(os.environ.get("MARKET_CANDLE_SNAPSHOT_MAX", "10080"))
CALIBRATION_CACHE_TTL_SEC = int(os.environ.get("CALIBRATION_CACHE_TTL_SEC", "300"))
# Background recomputation interval for meta-label calibration index (0 = disabled).
CALIBRATION_REFRESH_SEC = int(os.environ.get("CALIBRATION_REFRESH_SEC", "600"))
# Meta-label GBM artifacts (per-bot joblib + metadata.json)
META_LABEL_MODEL_DIR = os.environ.get(
    "META_LABEL_MODEL_DIR",
    os.path.join(BASE_DIR, "data", "meta_label_models"),
)
META_LABEL_MIN_TRAIN_SAMPLES = int(os.environ.get("META_LABEL_MIN_TRAIN_SAMPLES", "30"))
# Emit JSON logs on trade/agent paths when true (default off in dev).
LOG_JSON = os.environ.get("LOG_JSON", "false").lower() in ("1", "true", "yes")
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
# WebSocket protocol keepalive (websockets.serve). Tolerate short event-loop stalls from
# sync DB / bot work — default ping_timeout is 20s and drops clients under load.
WS_PING_INTERVAL = float(os.environ.get("WS_PING_INTERVAL", "30"))
WS_PING_TIMEOUT = float(os.environ.get("WS_PING_TIMEOUT", "90"))
# Application-level keepalive broadcast when clients are connected (NAT / proxy idle).
WS_KEEPALIVE_INTERVAL_SEC = float(os.environ.get("WS_KEEPALIVE_INTERVAL_SEC", "25"))

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
# Sim / Massive paper OMS: allow short entries (margin = 100% notional locked in quote).
PAPER_SHORTS_ENABLED = os.environ.get("PAPER_SHORTS_ENABLED", "true").lower() in ("1", "true", "yes")

# Bot risk limits
BOT_MIN_NOTIONAL = float(os.environ.get("BOT_MIN_NOTIONAL", "10.0"))
BOT_DAILY_LOSS_LIMIT_PCT = float(os.environ.get("BOT_DAILY_LOSS_LIMIT_PCT", "5.0"))
BOT_MAX_ACTIVE_BOTS = int(os.environ.get("BOT_MAX_ACTIVE_BOTS", "20"))
BOT_SNAPSHOT_INTERVAL = float(os.environ.get("BOT_SNAPSHOT_INTERVAL", "300"))
BOT_SNAPSHOT_RETENTION = int(os.environ.get("BOT_SNAPSHOT_RETENTION", "2000"))
BOT_LOG_RETENTION = int(os.environ.get("BOT_LOG_RETENTION", "5000"))
BOT_MAX_CONSECUTIVE_LOSSES = int(os.environ.get("BOT_MAX_CONSECUTIVE_LOSSES", "5"))
BOT_LOSS_COOLOFF_SEC = int(os.environ.get("BOT_LOSS_COOLOFF_SEC", "300"))
# Max cumulative drawdown (%) per bot before auto-pause.  0 = disabled.
BOT_MAX_DRAWDOWN_PCT = float(os.environ.get("BOT_MAX_DRAWDOWN_PCT", "15.0"))
# Max concurrent bots trading the same symbol.  0 = unlimited.
BOT_MAX_PER_SYMBOL = int(os.environ.get("BOT_MAX_PER_SYMBOL", "3"))
OPTIMIZATION_RETENTION_DAYS = int(os.environ.get("OPTIMIZATION_RETENTION_DAYS", "30"))
BACKTEST_JOB_RETENTION_DAYS = int(os.environ.get("BACKTEST_JOB_RETENTION_DAYS", "14"))
# Parallel symbol/sweep workers (memory-bound — default 4).
BACKTEST_PARALLEL_WORKERS = int(os.environ.get("BACKTEST_PARALLEL_WORKERS", "4"))
# Run portfolio / sweep / WF / reasoning in a background asyncio task.
BACKTEST_DEFER_HEAVY = os.environ.get("BACKTEST_DEFER_HEAVY", "true").lower() in ("1", "true", "yes")
# Always queue sweep / walk-forward optimization (never inline on WS handler).
BACKTEST_FORCE_DEFER_OPTIMIZATION = os.environ.get(
    "BACKTEST_FORCE_DEFER_OPTIMIZATION", "true"
).lower() in ("1", "true", "yes")
# Inline WS handler runs under this estimate (seconds); slower jobs go to the queue.
BACKTEST_INLINE_MAX_SEC = float(os.environ.get("BACKTEST_INLINE_MAX_SEC", "30"))
# Tier 5 adaptive trial budget defaults (overridable per sweep request).
BACKTEST_SWEEP_MAX_TRIALS = int(os.environ.get("BACKTEST_SWEEP_MAX_TRIALS", "200"))
BACKTEST_SWEEP_MAX_GRID = int(os.environ.get("BACKTEST_SWEEP_MAX_GRID", "24"))
BACKTEST_SWEEP_TIME_BUDGET_SEC = float(os.environ.get("BACKTEST_SWEEP_TIME_BUDGET_SEC", "300"))

# Portfolio-level risk (all bots combined)
PORTFOLIO_MAX_GROSS_EXPOSURE_PCT = float(os.environ.get("PORTFOLIO_MAX_GROSS_EXPOSURE_PCT", "80"))
PORTFOLIO_MAX_GROUP_EXPOSURE_PCT = float(os.environ.get("PORTFOLIO_MAX_GROUP_EXPOSURE_PCT", "40"))

# Account drawdown kill switch — stops all bots when equity falls this % below peak
RISK_KILL_SWITCH_ENABLED = os.environ.get("RISK_KILL_SWITCH_ENABLED", "true").lower() in (
    "1", "true", "yes"
)
RISK_MAX_DRAWDOWN_PCT = float(os.environ.get("RISK_MAX_DRAWDOWN_PCT", "15.0"))
RISK_MONITOR_INTERVAL_SEC = float(os.environ.get("RISK_MONITOR_INTERVAL_SEC", "30"))

# Time-based risk controls (equities only — crypto exempt from no-trade + weekend flatten)
RISK_TIME_CONTROLS_ENABLED = os.environ.get("RISK_TIME_CONTROLS_ENABLED", "true").lower() in (
    "1", "true", "yes"
)
RISK_NO_TRADE_WINDOWS = os.environ.get("RISK_NO_TRADE_WINDOWS", "09:30-09:35,15:55-16:00")
RISK_EQUITY_MARKET_TZ = os.environ.get("RISK_EQUITY_MARKET_TZ", "America/New_York")
RISK_WEEKEND_FLATTEN_ENABLED = os.environ.get("RISK_WEEKEND_FLATTEN_ENABLED", "true").lower() in (
    "1", "true", "yes"
)
RISK_WEEKEND_FLATTEN_FRIDAY_AFTER = os.environ.get("RISK_WEEKEND_FLATTEN_FRIDAY_AFTER", "15:50")

# Per-bot max position duration — auto-close when hold time exceeds limit
RISK_POSITION_DURATION_ENABLED = os.environ.get("RISK_POSITION_DURATION_ENABLED", "true").lower() in (
    "1", "true", "yes"
)
RISK_MAX_POSITION_HOURS = float(os.environ.get("RISK_MAX_POSITION_HOURS", "0"))

# Dynamic correlation groups — rolling price correlation replaces static buckets when enabled
RISK_DYNAMIC_CORRELATION_ENABLED = os.environ.get("RISK_DYNAMIC_CORRELATION_ENABLED", "true").lower() in (
    "1", "true", "yes"
)
RISK_CORRELATION_LOOKBACK_DAYS = int(os.environ.get("RISK_CORRELATION_LOOKBACK_DAYS", "60"))
RISK_CORRELATION_THRESHOLD = float(os.environ.get("RISK_CORRELATION_THRESHOLD", "0.7"))
RISK_CORRELATION_REFRESH_SEC = float(os.environ.get("RISK_CORRELATION_REFRESH_SEC", "300"))
RISK_CORRELATION_MIN_DAYS = int(os.environ.get("RISK_CORRELATION_MIN_DAYS", "30"))
RISK_CORRELATION_WINSORIZE_PCT = float(os.environ.get("RISK_CORRELATION_WINSORIZE_PCT", "0.005"))

# Margin / leverage awareness — block or cap entries when utilization exceeds limit
RISK_MARGIN_ENABLED = os.environ.get("RISK_MARGIN_ENABLED", "true").lower() in (
    "1", "true", "yes"
)
RISK_MAX_MARGIN_UTILIZATION_PCT = float(os.environ.get("RISK_MAX_MARGIN_UTILIZATION_PCT", "85"))
RISK_MAX_LEVERAGE = float(os.environ.get("RISK_MAX_LEVERAGE", "1"))

# Pre-trade preview — estimated execution costs (not broker-confirmed)
ORDER_PREVIEW_FEE_BPS = float(os.environ.get("ORDER_PREVIEW_FEE_BPS", "10"))
ORDER_PREVIEW_SLIPPAGE_BPS = float(os.environ.get("ORDER_PREVIEW_SLIPPAGE_BPS", "5"))

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
ARCHIVE_BACKFILL_ON_STARTUP = os.environ.get("ARCHIVE_BACKFILL_ON_STARTUP", "true").lower() in (
    "1", "true", "yes"
)
ARCHIVE_INGESTION_ENABLED = os.environ.get("ARCHIVE_INGESTION_ENABLED", "true").lower() in (
    "1", "true", "yes"
)
ARCHIVE_INGESTION_ON_STARTUP = os.environ.get("ARCHIVE_INGESTION_ON_STARTUP", "true").lower() in (
    "1", "true", "yes"
)
ARCHIVE_INGESTION_INTERVAL = float(os.environ.get("ARCHIVE_INGESTION_INTERVAL", "3600"))
ARCHIVE_INGESTION_DAYS = int(os.environ.get("ARCHIVE_INGESTION_DAYS", "90"))
ARCHIVE_INGESTION_GAP_SCAN_DAYS = int(os.environ.get("ARCHIVE_INGESTION_GAP_SCAN_DAYS", "7"))
ARCHIVE_INGESTION_MAX_GAPS_PER_RUN = int(os.environ.get("ARCHIVE_INGESTION_MAX_GAPS_PER_RUN", "8"))
ARCHIVE_INGESTION_CONCURRENCY = int(os.environ.get("ARCHIVE_INGESTION_CONCURRENCY", "2"))
ARCHIVE_INGESTION_STARTUP_BATCH_SIZE = int(os.environ.get("ARCHIVE_INGESTION_STARTUP_BATCH_SIZE", "6"))
ARCHIVE_INGESTION_SYMBOL_DELAY_SEC = float(os.environ.get("ARCHIVE_INGESTION_SYMBOL_DELAY_SEC", "1.0"))
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
ARCHIVE_TICK_BATCH_MAX = int(os.environ.get("ARCHIVE_TICK_BATCH_MAX", "5000"))
# Archive range reads: fetchmany batch for one-pass iterators (bars stay capped by LIMIT).
ARCHIVE_QUERY_BATCH_SIZE = int(os.environ.get("ARCHIVE_QUERY_BATCH_SIZE", "2000"))
# Default / backtest resolve cap (≈35d of 1m bars).
ARCHIVE_QUERY_LIMIT = int(os.environ.get("ARCHIVE_QUERY_LIMIT", "50000"))
# Chart / WS history pan — keep smaller than backtest resolve.
ARCHIVE_QUERY_LIMIT_UI = int(os.environ.get("ARCHIVE_QUERY_LIMIT_UI", "10000"))
ARCHIVE_TICK_QUERY_LIMIT = int(os.environ.get("ARCHIVE_TICK_QUERY_LIMIT", "10000"))
# Footprint heatmap: time-chunked SQLite aggregates + hard caps (ms / cells).
FOOTPRINT_MAX_RANGE_MS = int(os.environ.get("FOOTPRINT_MAX_RANGE_MS", str(24 * 3600 * 1000)))
FOOTPRINT_CHUNK_MS = int(os.environ.get("FOOTPRINT_CHUNK_MS", str(60 * 60 * 1000)))
FOOTPRINT_MAX_CELLS = int(os.environ.get("FOOTPRINT_MAX_CELLS", "50000"))

# Data quality monitoring (stale feeds, candle gaps, abnormal spreads)
DATA_QUALITY_ENABLED = os.environ.get("DATA_QUALITY_ENABLED", "true").lower() in (
    "1", "true", "yes"
)
DATA_QUALITY_INTERVAL_SEC = float(os.environ.get("DATA_QUALITY_INTERVAL_SEC", "15"))
DIAGNOSTICS_INTERVAL_SEC = float(os.environ.get("DIAGNOSTICS_INTERVAL_SEC", "15"))
DIAGNOSTICS_STATS_CACHE_SEC = float(os.environ.get("DIAGNOSTICS_STATS_CACHE_SEC", "30"))
ALPACA_BROADCAST_INTERVAL_SEC = float(os.environ.get("ALPACA_BROADCAST_INTERVAL_SEC", "1.5"))
DATA_QUALITY_STALE_WARN_SEC = float(os.environ.get("DATA_QUALITY_STALE_WARN_SEC", "30"))
DATA_QUALITY_STALE_PAUSE_SEC = float(os.environ.get("DATA_QUALITY_STALE_PAUSE_SEC", "60"))
DATA_QUALITY_MAX_SPREAD_PCT = float(os.environ.get("DATA_QUALITY_MAX_SPREAD_PCT", "2.0"))
DATA_QUALITY_GAP_BAR_SEC = int(os.environ.get("DATA_QUALITY_GAP_BAR_SEC", "120"))
DATA_QUALITY_ACTIVE_PAUSE = os.environ.get("DATA_QUALITY_ACTIVE_PAUSE", "true").lower() in (
    "1", "true", "yes"
)

# Alternative data refresh (Massive/Polygon REST)
ALTDATA_ENABLED = os.environ.get("ALTDATA_ENABLED", "true").lower() in ("1", "true", "yes")
ALTDATA_REFRESH_INTERVAL_SEC = float(os.environ.get("ALTDATA_REFRESH_INTERVAL_SEC", "3600"))
# Calendar + corporate event entry gates (equity bots; crypto exempt)
CALENDAR_GATES_ENABLED = os.environ.get("CALENDAR_GATES_ENABLED", "true").lower() in ("1", "true", "yes")
CORP_EVENT_GATES_ENABLED = os.environ.get("CORP_EVENT_GATES_ENABLED", "true").lower() in ("1", "true", "yes")
CORP_BLACKOUT_SPLIT_DAYS = int(os.environ.get("CORP_BLACKOUT_SPLIT_DAYS", "1"))
CORP_BLACKOUT_EX_DIV_DAYS = int(os.environ.get("CORP_BLACKOUT_EX_DIV_DAYS", "0"))
# Backtest price series: raw | split_only | total_return
BACKTEST_PRICE_ADJUST = os.environ.get("BACKTEST_PRICE_ADJUST", "split_only").strip().lower()
if BACKTEST_PRICE_ADJUST not in ("raw", "split_only", "total_return"):
    BACKTEST_PRICE_ADJUST = "split_only"

# Macro release entry gates (FOMC, CPI, NFP — applies to equities + crypto)
MACRO_GATES_ENABLED = os.environ.get("MACRO_GATES_ENABLED", "true").lower() in ("1", "true", "yes")
MACRO_BLACKOUT_MINUTES = int(os.environ.get("MACRO_BLACKOUT_MINUTES", "30"))
MACRO_CALENDAR_ENABLED = os.environ.get("MACRO_CALENDAR_ENABLED", "true").lower() in ("1", "true", "yes")
# Crypto perp positioning (Binance public API — funding + OI)
CRYPTO_DERIVATIVES_ENABLED = os.environ.get("CRYPTO_DERIVATIVES_ENABLED", "true").lower() in ("1", "true", "yes")

# News/social sentiment feed (lexicon-scored headlines → sentiment_events)
SENTIMENT_ENABLED = os.environ.get("SENTIMENT_ENABLED", "true").lower() in ("1", "true", "yes")
SENTIMENT_LOOKBACK_HOURS = float(os.environ.get("SENTIMENT_LOOKBACK_HOURS", "24"))
SENTIMENT_SCORE_THRESHOLD = float(os.environ.get("SENTIMENT_SCORE_THRESHOLD", "0.2"))

# Finnhub.io — company news + news-sentiment (https://finnhub.io/docs/api)
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()
FINNHUB_API_URL = os.environ.get("FINNHUB_API_URL", "https://finnhub.io/api/v1").strip().rstrip("/")

# Google News RSS (gnews package — keyword search, no API key)
GNEWS_ENABLED = os.environ.get("GNEWS_ENABLED", "true").lower() in ("1", "true", "yes")
try:
    GNEWS_MAX_RESULTS = int(os.environ.get("GNEWS_MAX_RESULTS", "15"))
except ValueError:
    GNEWS_MAX_RESULTS = 15
GNEWS_PERIOD = os.environ.get("GNEWS_PERIOD", "7d").strip() or "7d"

# Strategy advisor — LLM-suggested bot params with optional shadow backtest
STRATEGY_ADVISOR_ENABLED = os.environ.get("STRATEGY_ADVISOR_ENABLED", "true").lower() in ("1", "true", "yes")
STRATEGY_ADVISOR_DEFAULT_DAYS = int(os.environ.get("STRATEGY_ADVISOR_DEFAULT_DAYS", "30"))

# External notifications (webhooks, Telegram, email digest)
NOTIFICATIONS_ENABLED = os.environ.get("NOTIFICATIONS_ENABLED", "true").lower() in (
    "1", "true", "yes"
)
# Master key for encrypting per-channel secrets in DB (generate a long random string)
NOTIFICATION_ENCRYPTION_KEY = os.environ.get("NOTIFICATION_ENCRYPTION_KEY", "").strip()
NOTIFICATION_DEDUPE_WINDOW_SEC = float(os.environ.get("NOTIFICATION_DEDUPE_WINDOW_SEC", "60"))
NOTIFICATION_DELIVERY_MAX_RETRIES = int(os.environ.get("NOTIFICATION_DELIVERY_MAX_RETRIES", "3"))
NOTIFICATION_DIGEST_ENABLED = os.environ.get("NOTIFICATION_DIGEST_ENABLED", "true").lower() in (
    "1", "true", "yes"
)
NOTIFICATION_DIGEST_HOUR = int(os.environ.get("NOTIFICATION_DIGEST_HOUR", "18"))
NOTIFICATION_DIGEST_TZ = os.environ.get(
    "NOTIFICATION_DIGEST_TZ",
    os.environ.get("RISK_EQUITY_MARKET_TZ", "America/New_York"),
)
ALERT_RULES_ENABLED = os.environ.get("ALERT_RULES_ENABLED", "true").lower() in (
    "1", "true", "yes"
)
WEB_PUSH_ENABLED = os.environ.get("WEB_PUSH_ENABLED", "true").lower() in (
    "1", "true", "yes"
)
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@localhost").strip()

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
# WebSocket equity stream — auto-resolved to sip or iex when ALPACA_DATA_FEED=auto (default).
ALPACA_DATA_URL = os.environ.get("ALPACA_DATA_URL", "wss://stream.data.alpaca.markets/v2/sip")
ALPACA_DATA_FEED = os.environ.get("ALPACA_DATA_FEED", "auto").strip().lower()  # auto | sip | iex

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

# Interactive Brokers (LIVE_IB) — feed-only via TWS / IB Gateway + ib_async
IB_HOST = os.environ.get("IB_HOST", "127.0.0.1")
IB_PORT = int(os.environ.get("IB_PORT", "4002"))  # Gateway paper; 4001 live; TWS 7497/7496
IB_CLIENT_ID = int(os.environ.get("IB_CLIENT_ID", "7"))
IB_USE_RTH = os.environ.get("IB_USE_RTH", "true").lower() in ("1", "true", "yes")
IB_MARKET_DATA_TYPE = int(os.environ.get("IB_MARKET_DATA_TYPE", "1"))  # 1=live, 3=delayed
IB_HIST_DURATION = os.environ.get("IB_HIST_DURATION", "5 D")
IB_STREAM_STAGGER_SEC = float(os.environ.get("IB_STREAM_STAGGER_SEC", "2.0"))
# Pause new historical subscriptions after IB pacing violation (error 162).
IB_PACING_PAUSE_SEC = float(os.environ.get("IB_PACING_PAUSE_SEC", "600"))
# When live quotes are denied, fall back to delayed frozen (type 3).
IB_AUTO_DELAYED_FALLBACK = os.environ.get("IB_AUTO_DELAYED_FALLBACK", "true").lower() in (
    "1", "true", "yes"
)
# Stream L1 ticks via reqMktData for snappier UI between 1m bar closes.
IB_L1_TICKS_ENABLED = os.environ.get("IB_L1_TICKS_ENABLED", "true").lower() in (
    "1", "true", "yes"
)
# Real IB order routing (paper Gateway default). Off = simulated OMS (feed-only).
IB_OMS_ENABLED = os.environ.get("IB_OMS_ENABLED", "false").lower() in ("1", "true", "yes")
IB_OMS_CLIENT_ID = int(os.environ.get("IB_OMS_CLIENT_ID", str(IB_CLIENT_ID + 50)))
IB_READ_ONLY_API = os.environ.get("IB_READ_ONLY_API", "false").lower() in ("1", "true", "yes")
# Smoke/integration tests use a dedicated client id so they don't collide with a running feed.
IB_SMOKE_CLIENT_ID = int(os.environ.get("IB_SMOKE_CLIENT_ID", str(IB_CLIENT_ID + 900)))
# How often the LIVE_IB server pushes in-memory quotes to WebSocket clients.
IB_BROADCAST_INTERVAL_SEC = float(os.environ.get("IB_BROADCAST_INTERVAL_SEC", "1.5"))

# Massive.com (formerly Polygon.io) — stocks + crypto WebSocket + REST seed (LIVE_MASSIVE)
MASSIVE_API_KEY = os.environ.get("MASSIVE_API_KEY", "")
_feed = os.environ.get("MASSIVE_FEED", "realtime").strip().lower()
_default_ws = (
    "wss://delayed.massive.com/stocks"
    if _feed == "delayed"
    else "wss://socket.massive.com/stocks"
)
_default_crypto_ws = (
    "wss://delayed.massive.com/crypto"
    if _feed == "delayed"
    else "wss://socket.massive.com/crypto"
)
MASSIVE_WS_URL = os.environ.get("MASSIVE_WS_URL", _default_ws)
MASSIVE_CRYPTO_WS_URL = os.environ.get("MASSIVE_CRYPTO_WS_URL", _default_crypto_ws)
MASSIVE_REST_URL = os.environ.get("MASSIVE_REST_URL", "https://api.polygon.io")
MASSIVE_HIST_DAYS = int(os.environ.get("MASSIVE_HIST_DAYS", "5"))
MASSIVE_BROADCAST_INTERVAL_SEC = float(os.environ.get("MASSIVE_BROADCAST_INTERVAL_SEC", "1.5"))
MASSIVE_WS_RECONNECT_SEC = float(os.environ.get("MASSIVE_WS_RECONNECT_SEC", "5"))
MASSIVE_WS_ENABLED = os.environ.get("MASSIVE_WS_ENABLED", "true").lower() in ("1", "true", "yes")
# When WS auth fails or MASSIVE_WS_ENABLED=false, poll REST for bars/quotes.
MASSIVE_POLL_FALLBACK = os.environ.get("MASSIVE_POLL_FALLBACK", "true").lower() in ("1", "true", "yes")
MASSIVE_POLL_INTERVAL_SEC = float(os.environ.get("MASSIVE_POLL_INTERVAL_SEC", "15"))
# Parallel REST history seed (concurrent symbol fetches at startup).
MASSIVE_SEED_CONCURRENCY = int(os.environ.get("MASSIVE_SEED_CONCURRENCY", "4"))
# NBBO: stocks Q.*, crypto XQ.* (plan permitting; falls back to synthetic book on trade/agg).
MASSIVE_QUOTES_ENABLED = os.environ.get("MASSIVE_QUOTES_ENABLED", "true").lower() in ("1", "true", "yes")
MASSIVE_FEED = _feed if _feed in ("realtime", "delayed") else "realtime"
# Server-side HT REST cache (Phase 3 memory tuning)
MASSIVE_HT_CACHE_TTL_SEC = float(os.environ.get("MASSIVE_HT_CACHE_TTL_SEC", "300"))
MASSIVE_HT_CACHE_MAX_ENTRIES = int(os.environ.get("MASSIVE_HT_CACHE_MAX_ENTRIES", "48"))

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
elif TERMINAL_MODE == "LIVE_IB":
    SYMBOLS = EQUITY_SYMBOLS
elif TERMINAL_MODE == "LIVE_MASSIVE":
    SYMBOLS = {**EQUITY_SYMBOLS, **CRYPTO_SYMBOLS}
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
