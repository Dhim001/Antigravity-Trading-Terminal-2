import os

# Base Directory & Database Path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(BASE_DIR)
# Shared on-disk data root (models, calibrations, job results, archives).
# Several bot services import this — previously reinvented per-module.
DATA_DIR = os.path.join(BASE_DIR, "data")


def _load_env_file(path: str, *, override: bool = False) -> None:
    """Load KEY=VALUE pairs into os.environ (manual dotenv; no external deps)."""
    if not path or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
                if override or key not in os.environ:
                    os.environ[key] = val


def _load_profile_env(path: str, *, protect_keys: frozenset[str]) -> None:
    """Load profile env on top of ``.env``, but never clobber process-env pins.

    Priority (highest → lowest): process env before config import → profile → ``.env``.
    This prevents ``TERMINAL_PROFILE=alpaca`` from rewriting a pytest-pinned
    ``SQLITE_DB_PATH`` back onto the live ``trading-alpaca.db``.
    """
    if not path or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
                if key in protect_keys:
                    continue
                os.environ[key] = val


# Snapshot process env *before* file loads so launchers/tests can pin DB paths.
_PROCESS_ENV_KEYS = frozenset(os.environ.keys())

# Base secrets/overrides, then optional dual-instance profile (profile beats .env;
# process env pins beat both).
_load_env_file(os.path.join(_REPO_ROOT, ".env"), override=False)
_terminal_profile = os.environ.get("TERMINAL_PROFILE", "").strip().lower()
if _terminal_profile:
    _load_profile_env(
        os.path.join(_REPO_ROOT, "env.profiles", f"{_terminal_profile}.env"),
        protect_keys=_PROCESS_ENV_KEYS,
    )

_sqlite_db = os.environ.get("SQLITE_DB_PATH", "").strip()
DB_PATH = (
    os.path.join(BASE_DIR, _sqlite_db)
    if _sqlite_db
    else os.path.join(BASE_DIR, "trading.db")
)
# Empty → SQLite (DB_PATH). Set postgresql://… only for Postgres deployments.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


# Features & Integration Flags
# Modes: "SIMULATED", "LIVE_ALPACA", "LIVE_BINANCE", "LIVE_ETORO", "LIVE_IB", "LIVE_MASSIVE"
TERMINAL_MODE = os.environ.get("TERMINAL_MODE", "SIMULATED")
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
# In-memory ML/ONNX model caches (per strategy store) — LRU + idle TTL.
ML_MODEL_CACHE_MAX = int(os.environ.get("ML_MODEL_CACHE_MAX", "12"))
ML_MODEL_CACHE_TTL_SEC = float(os.environ.get("ML_MODEL_CACHE_TTL_SEC", "3600"))
# Isolate torch/ONNX train+validate in a max-1 process pool (MEMORY #9).
ML_TRAIN_PROCESS_ISOLATION = os.environ.get("ML_TRAIN_PROCESS_ISOLATION", "true").lower() in (
    "1", "true", "yes"
)
# Concurrent train/validate process-pool workers. Default 1 (conservative).
# Set to ``auto`` for conservative scale-up (≤2 when CUDA present + RSS≥6144).
# Integer env values are used as-is (see resolve_ml_train_max_workers).
ML_TRAIN_MAX_WORKERS_RAW = os.environ.get("ML_TRAIN_MAX_WORKERS", "1").strip()
try:
    ML_TRAIN_MAX_WORKERS = max(1, int(ML_TRAIN_MAX_WORKERS_RAW))
except ValueError:
    # ``auto`` or other non-int — resolved at pool creation time.
    ML_TRAIN_MAX_WORKERS = 1
# Soft RSS ceiling for train/validate worker processes (MEMORY #27). 0 = disabled.
# Unix: resource.RLIMIT_AS (address space). Windows: best-effort log-only check via psutil.
# Optimizer perf doc recommends 6144 on ≥16GB hosts with CUDA; keep 4096 shipped default.
ML_TRAIN_RSS_LIMIT_MB = int(os.environ.get("ML_TRAIN_RSS_LIMIT_MB", "4096"))
# Cap concurrent async train/validate tasks so candle lists are not pinned unboundedly.
ML_ASYNC_MAX_INFLIGHT = int(os.environ.get("ML_ASYNC_MAX_INFLIGHT", "1"))
# Walk-forward fold ThreadPool size for CPU/GBM strategies (``auto``|int; 1=sequential).
# Shipped default 1 (conservative). Set ``auto`` or 2–4 for Opt #2 speedup.
# GPU deep models stay sequential regardless. Never nests ProcessPool.
ML_WF_FOLD_WORKERS = os.environ.get("ML_WF_FOLD_WORKERS", "1").strip()
# Optuna multi-fidelity screen: parallel startup (random) trials only.
# Shipped default 1 (conservative). Set 2–4 to opt into Opt #3.
ML_OPTUNA_STARTUP_WORKERS = int(os.environ.get("ML_OPTUNA_STARTUP_WORKERS", "1"))
# Torch DataLoader workers for deep trainers (empty = platform default: 0 on Windows).
ML_DATALOADER_NUM_WORKERS = os.environ.get("ML_DATALOADER_NUM_WORKERS", "").strip()
# Optional sim_mode for lean/exploratory ML validate (wf_capacity_parity=false only).
# Deploy-grade capacity-parity WF stays live_aligned. Example: research_fast
ML_EXPLORATORY_SIM_MODE = os.environ.get("ML_EXPLORATORY_SIM_MODE", "").strip().lower()
# Torch/RL trains: default to the process pool (MEMORY_CENTRIC_REVIEW #41) so the
# largest RSS spikes stay out of the live feed/OMS process (Hummingbot lesson).
# The previous in-process default avoided two Windows issues: (1) pickling tens of
# thousands of enriched candles into a spawn worker looked like a hang from pct=0 —
# mitigated now by parent-side candle trimming for pool-bound validate jobs
# (submit_validate_job) and progress-file writes from the worker; (2) CUDA + spawn
# fragility — workers import torch lazily and touch CUDA only post-spawn.
# Set ML_TRAIN_TORCH_IN_PROCESS=1 to opt back into in-process threads for debugging.
ML_TRAIN_TORCH_IN_PROCESS = os.environ.get("ML_TRAIN_TORCH_IN_PROCESS", "false").lower() in (
    "1", "true", "yes"
)
# Per-strategy in-process overrides. RL_PPO_AGENT hangs on Windows spawn+CUDA
# (worker alive, CPU busy, but no progress past "bars loaded") — observed twice
# on 2026-08-08 — so it trains in a thread by default. Set
# ML_TRAIN_IN_PROCESS_STRATEGIES="" to force everything back into the pool.
ML_TRAIN_IN_PROCESS_STRATEGIES = frozenset(
    s.strip().upper()
    for s in os.environ.get(
        "ML_TRAIN_IN_PROCESS_STRATEGIES", "RL_PPO_AGENT"
    ).split(",")
    if s.strip()
)
# Training device override: empty = auto (CUDA if available). Example: ML_TRAIN_DEVICE=cpu
# Live inference stays CPU ONNX regardless.
# Drain MlRetrainScheduler pending queue into real train jobs (APP_SCAN #6).
ML_RETRAIN_AUTO_DRAIN = os.environ.get("ML_RETRAIN_AUTO_DRAIN", "true").lower() in (
    "1", "true", "yes"
)
ML_RETRAIN_DRAIN_INTERVAL_SEC = float(os.environ.get("ML_RETRAIN_DRAIN_INTERVAL_SEC", "45"))
# Nested FIT → EMBARGO → HOLDOUT for ML Lab train / validate / Algo BT (anti-overfit).
# Off by default; set ML_CALENDAR_HOLDOUT=1 (e.g. LIVE_MASSIVE) to enable.
ML_CALENDAR_HOLDOUT = os.environ.get("ML_CALENDAR_HOLDOUT", "").lower() in (
    "1", "true", "yes"
)
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
# Reject-telemetry + ML run history retention (MEMORY_CENTRIC_REVIEW #29/#30).
REJECT_LOG_RETENTION_DAYS = int(os.environ.get("REJECT_LOG_RETENTION_DAYS", "7"))
REJECT_LOG_MAX_ROWS = int(os.environ.get("REJECT_LOG_MAX_ROWS", "500000"))
ML_TRAIN_RUNS_RETENTION_DAYS = int(os.environ.get("ML_TRAIN_RUNS_RETENTION_DAYS", "30"))
# Terminal ML job results larger than this are offloaded to disk and slimmed in
# RAM (MEMORY_CENTRIC_REVIEW #31); smaller results stay hot in the job store.
ML_JOB_RESULT_OFFLOAD_BYTES = int(os.environ.get("ML_JOB_RESULT_OFFLOAD_BYTES", "65536"))

# --- Execution TCA (EXECUTION_RISK_INTELLIGENCE_PLAN Phase 1) ----------------
# Arrival-price benchmark capture + implementation-shortfall decomposition for
# every bot order (immediate fills at submit, live fills at reconciliation).
# Read-only telemetry: failures are logged and swallowed, never raised into the
# order path. Set EXEC_QUALITY_LOG_ENABLED=0 to disable capture entirely.
EXEC_QUALITY_LOG_ENABLED = os.environ.get("EXEC_QUALITY_LOG_ENABLED", "1").strip().lower() not in {
    "0", "false", "off",
}
EXEC_QUALITY_RETENTION_DAYS = int(os.environ.get("EXEC_QUALITY_RETENTION_DAYS", "30"))
EXEC_QUALITY_MAX_ROWS = int(os.environ.get("EXEC_QUALITY_MAX_ROWS", "200000"))

# Phase 2 — backtest cost calibration from measured live execution. Suggested
# slippage = measured avg exec cost (spread+impact) × safety factor, clamped to
# [EXEC_CAL_MIN_BPS, EXEC_CAL_MAX_BPS]; latency suggestion = max(0, avg delay).
EXEC_CAL_MIN_SAMPLES = int(os.environ.get("EXEC_CAL_MIN_SAMPLES", "10"))
EXEC_CAL_SAFETY_FACTOR = float(os.environ.get("EXEC_CAL_SAFETY_FACTOR", "1.25"))
EXEC_CAL_MIN_BPS = float(os.environ.get("EXEC_CAL_MIN_BPS", "0.5"))
EXEC_CAL_MAX_BPS = float(os.environ.get("EXEC_CAL_MAX_BPS", "200"))
# Parallel symbol/sweep workers. Default tracks CPU count (capped) — each worker
# holds a DF / model copy so raise carefully on low-RAM hosts.
_bt_cpu = os.cpu_count() or 4
_bt_workers_default = str(min(max(2, _bt_cpu), 8))
BACKTEST_PARALLEL_WORKERS = int(os.environ.get("BACKTEST_PARALLEL_WORKERS", _bt_workers_default))
# Hard safety cap for parallel_worker_count (ProcessPool may use up to this).
BACKTEST_PARALLEL_MAX = int(os.environ.get("BACKTEST_PARALLEL_MAX", "16"))
# auto (default) | thread | process — ``auto`` uses ProcessPool for GIL-bound
# ML sweeps when CUDA EP is not loaded in-process; otherwise threads (spawn +
# CUDA is fragile). Falls back to threads on spawn errors.
BACKTEST_PARALLEL_BACKEND = os.environ.get("BACKTEST_PARALLEL_BACKEND", "auto").strip().lower()
# Backtest-only batched ML inference (sklearn/ONNX chunks). Live bots unchanged.
BACKTEST_BATCH_INFERENCE = os.environ.get("BACKTEST_BATCH_INFERENCE", "true").lower() in (
    "1", "true", "yes",
)
BACKTEST_INFERENCE_BATCH_SIZE = int(os.environ.get("BACKTEST_INFERENCE_BATCH_SIZE", "512"))
# Columnar NumPy ML feature matrix for research/backtest (live evaluate stays per-bar).
BACKTEST_VECTORIZED_FEATURES = os.environ.get("BACKTEST_VECTORIZED_FEATURES", "true").lower() in (
    "1", "true", "yes",
)
# Research backtests only: auto (prefer CUDA EP) | cpu | cuda — live deploy stays CPU ONNX.
BACKTEST_INFERENCE_DEVICE = os.environ.get("BACKTEST_INFERENCE_DEVICE", "auto").strip().lower()
# ORT SessionOptions thread caps (empty = library default). Helps single-run matmul.
# When using ProcessPool, prefer workers × ORT_INTRA_OP_THREADS ≈ cpu_count.
ORT_INTRA_OP_THREADS = os.environ.get("ORT_INTRA_OP_THREADS", "").strip()
ORT_INTER_OP_THREADS = os.environ.get("ORT_INTER_OP_THREADS", "").strip()
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

# Risk Sentinel Agent (proactive portfolio protection)
RISK_SENTINEL_ENABLED = os.environ.get("RISK_SENTINEL_ENABLED", "true").lower() in ("1", "true", "yes")
RISK_SENTINEL_MAX_VELOCITY = float(os.environ.get("RISK_SENTINEL_MAX_VELOCITY", "3.0"))
RISK_SENTINEL_AUTO_PAUSE_ON_STREAK = os.environ.get("RISK_SENTINEL_AUTO_PAUSE_ON_STREAK", "true").lower() in ("1", "true", "yes")
RISK_SENTINEL_MAX_CORRELATION_EXPOSURE_PCT = float(os.environ.get("RISK_SENTINEL_MAX_CORRELATION_EXPOSURE_PCT", "40.0"))

# Regime Rotation Agent (automatic strategy rotation based on market conditions)
REGIME_ROTATION_ENABLED = os.environ.get("REGIME_ROTATION_ENABLED", "true").lower() in ("1", "true", "yes")
REGIME_ROTATION_INTERVAL_SEC = float(os.environ.get("REGIME_ROTATION_INTERVAL_SEC", "300"))
REGIME_ROTATION_FLATTEN_ON_ROTATE = os.environ.get("REGIME_ROTATION_FLATTEN_ON_ROTATE", "true").lower() in ("1", "true", "yes")

# Alpha Decay Monitor Agent — off for live deploy until the monitor is trustworthy.
# (It was pausing correct bots on short-window / inflated backtest Sharpe.)
ALPHA_DECAY_ENABLED = os.environ.get("ALPHA_DECAY_ENABLED", "false").lower() in ("1", "true", "yes")
ALPHA_DECAY_INTERVAL_SEC = float(os.environ.get("ALPHA_DECAY_INTERVAL_SEC", "3600"))
ALPHA_DECAY_MIN_TRADES = int(os.environ.get("ALPHA_DECAY_MIN_TRADES", "10"))
# Auto-pause only on absolute live-edge collapse (not relative-to-backtest).
ALPHA_DECAY_AUTO_PAUSE = os.environ.get("ALPHA_DECAY_AUTO_PAUSE", "true").lower() in ("1", "true", "yes")
ALPHA_DECAY_AUTO_RETRAIN = os.environ.get("ALPHA_DECAY_AUTO_RETRAIN", "true").lower() in ("1", "true", "yes")
# Live Sharpe is not a trustworthy pause input below this calendar span.
ALPHA_DECAY_MIN_SHARPE_DAYS = int(os.environ.get("ALPHA_DECAY_MIN_SHARPE_DAYS", "21"))
# Absolute collapse must have at least this many closed exits.
ALPHA_DECAY_PAUSE_MIN_TRADES = int(os.environ.get("ALPHA_DECAY_PAUSE_MIN_TRADES", "30"))
# Backtest used as a live bar must have this many trades and live-aligned parity.
ALPHA_DECAY_MIN_BT_TRADES = int(os.environ.get("ALPHA_DECAY_MIN_BT_TRADES", "30"))
# IS/short-window Sharpe above this is not a live expectation (warn/ignore, never a bar).
ALPHA_DECAY_MAX_TRUSTED_SHARPE = float(os.environ.get("ALPHA_DECAY_MAX_TRUSTED_SHARPE", "2.5"))

# Pre-Trade Intelligence Agent (last-mile entry checklist)
PRETRADE_INTEL_ENABLED = os.environ.get("PRETRADE_INTEL_ENABLED", "true").lower() in ("1", "true", "yes")
PRETRADE_SETUP_FAIL_LIMIT = int(os.environ.get("PRETRADE_SETUP_FAIL_LIMIT", "3"))
PRETRADE_SETUP_LOOKBACK_HOURS = float(os.environ.get("PRETRADE_SETUP_LOOKBACK_HOURS", "24"))
PRETRADE_SENTIMENT_THRESHOLD = float(os.environ.get("PRETRADE_SENTIMENT_THRESHOLD", "0.45"))
PRETRADE_SENTIMENT_MIN_MENTIONS = int(os.environ.get("PRETRADE_SENTIMENT_MIN_MENTIONS", "3"))
PRETRADE_REDUCE_SIZE_FACTOR = float(os.environ.get("PRETRADE_REDUCE_SIZE_FACTOR", "0.5"))
PRETRADE_GAP_VETO_PCT = float(os.environ.get("PRETRADE_GAP_VETO_PCT", "3.0"))
# failures_streak: reduce (default) | veto | off — streaks are often survivable in backtests
PRETRADE_STREAK_MODE = os.environ.get("PRETRADE_STREAK_MODE", "reduce").strip().lower()
PRETRADE_STREAK_REDUCE_FACTOR = float(os.environ.get("PRETRADE_STREAK_REDUCE_FACTOR", "0.5"))
PRETRADE_STREAK_SEVERE_FACTOR = float(os.environ.get("PRETRADE_STREAK_SEVERE_FACTOR", "0.25"))
PRETRADE_STREAK_SEVERE_LIMIT = int(os.environ.get("PRETRADE_STREAK_SEVERE_LIMIT", "5"))
PRETRADE_STREAK_COOLDOWN_SEC = int(os.environ.get("PRETRADE_STREAK_COOLDOWN_SEC", "900"))  # 15 min
PRETRADE_AWARE_SIGNALS = os.environ.get("PRETRADE_AWARE_SIGNALS", "true").lower() in ("1", "true", "yes")
PRETRADE_WARN_DEBOUNCE_SEC = int(os.environ.get("PRETRADE_WARN_DEBOUNCE_SEC", "900"))

# Post-Trade Learning Agent (close → classify → lesson → optional config apply)
POSTTRADE_LEARNER_ENABLED = os.environ.get("POSTTRADE_LEARNER_ENABLED", "true").lower() in ("1", "true", "yes")
POSTTRADE_LEARNER_USE_LLM = os.environ.get("POSTTRADE_LEARNER_USE_LLM", "true").lower() in ("1", "true", "yes")
POSTTRADE_LEARNER_AUTO_APPLY = os.environ.get("POSTTRADE_LEARNER_AUTO_APPLY", "false").lower() in ("1", "true", "yes")
POSTTRADE_LEARNER_AUTO_RETRAIN = os.environ.get("POSTTRADE_LEARNER_AUTO_RETRAIN", "true").lower() in ("1", "true", "yes")
POSTTRADE_LEARNER_RETRAIN_EVERY_N = int(os.environ.get("POSTTRADE_LEARNER_RETRAIN_EVERY_N", "10"))
POSTTRADE_LEARNER_STOP_WIDEN_PCT = float(os.environ.get("POSTTRADE_LEARNER_STOP_WIDEN_PCT", "0.25"))
POSTTRADE_LEARNER_CONFIDENCE_BUMP = float(os.environ.get("POSTTRADE_LEARNER_CONFIDENCE_BUMP", "0.03"))

# Agent Decision Eval (Sprint 4) — closed-loop scoring of vetoes/rotations/patches/pauses
AGENT_EVAL_ENABLED = os.environ.get("AGENT_EVAL_ENABLED", "true").lower() in ("1", "true", "yes")
AGENT_EVAL_INTERVAL_SEC = float(os.environ.get("AGENT_EVAL_INTERVAL_SEC", "3600"))
# Decisions are graded once they are at least this old (price path has formed)…
AGENT_EVAL_MIN_AGE_SEC = float(os.environ.get("AGENT_EVAL_MIN_AGE_SEC", "3600"))
# …and expire ungraded (outcome=insufficient_data) once older than this.
AGENT_EVAL_MAX_AGE_SEC = float(os.environ.get("AGENT_EVAL_MAX_AGE_SEC", "86400"))
# Veto counterfactual: 1m bars after the blocked entry used for the move check.
AGENT_EVAL_VETO_BARS = int(os.environ.get("AGENT_EVAL_VETO_BARS", "60"))
# Pause counterfactual: hours after the pause used for the price comparison.
AGENT_EVAL_PAUSE_HOURS = float(os.environ.get("AGENT_EVAL_PAUSE_HOURS", "4"))
# Patch/rotation scoring: closed exits compared before vs after the decision.
AGENT_EVAL_TRADE_WINDOW = int(os.environ.get("AGENT_EVAL_TRADE_WINDOW", "10"))
AGENT_EVAL_RETENTION_DAYS = int(os.environ.get("AGENT_EVAL_RETENTION_DAYS", "30"))

# Scanner Auto-Deploy Agent (continuous scan → gate → create bots)
SCANNER_DEPLOY_ENABLED = os.environ.get("SCANNER_DEPLOY_ENABLED", "false").lower() in ("1", "true", "yes")
SCANNER_DEPLOY_INTERVAL_SEC = float(os.environ.get("SCANNER_DEPLOY_INTERVAL_SEC", "300"))
SCANNER_DEPLOY_MIN_CONFIDENCE = float(os.environ.get("SCANNER_DEPLOY_MIN_CONFIDENCE", "0.65"))
SCANNER_DEPLOY_MIN_SCORE = int(os.environ.get("SCANNER_DEPLOY_MIN_SCORE", "3"))
SCANNER_DEPLOY_MAX_CORRELATION = float(os.environ.get("SCANNER_DEPLOY_MAX_CORRELATION", "0.6"))
SCANNER_DEPLOY_MAX_PORTFOLIO_PCT = float(os.environ.get("SCANNER_DEPLOY_MAX_PORTFOLIO_PCT", "40.0"))
SCANNER_DEPLOY_MAX_PORTFOLIO_ALLOCATION = float(os.environ.get("SCANNER_DEPLOY_MAX_PORTFOLIO_ALLOCATION", "10000.0"))
SCANNER_DEPLOY_MAX_CONCURRENT_BOTS = int(os.environ.get("SCANNER_DEPLOY_MAX_CONCURRENT_BOTS", "5"))
SCANNER_DEPLOY_BASE_ALLOCATION = float(os.environ.get("SCANNER_DEPLOY_BASE_ALLOCATION", "1000"))
SCANNER_DEPLOY_MAX_ALLOCATION = float(os.environ.get("SCANNER_DEPLOY_MAX_ALLOCATION", "2500"))
SCANNER_DEPLOY_MAX_PER_CYCLE = int(os.environ.get("SCANNER_DEPLOY_MAX_PER_CYCLE", "2"))
SCANNER_DEPLOY_BACKTEST_DAYS = int(os.environ.get("SCANNER_DEPLOY_BACKTEST_DAYS", "7"))
SCANNER_DEPLOY_MIN_WIN_RATE = float(os.environ.get("SCANNER_DEPLOY_MIN_WIN_RATE", "50"))
SCANNER_DEPLOY_MIN_TRADES = int(os.environ.get("SCANNER_DEPLOY_MIN_TRADES", "3"))
SCANNER_DEPLOY_STRATEGY = os.environ.get("SCANNER_DEPLOY_STRATEGY", "CHART_AGENT")
SCANNER_DEPLOY_TIMEFRAME = os.environ.get("SCANNER_DEPLOY_TIMEFRAME", "1m")
SCANNER_DEPLOY_MAX_DRAWDOWN_PCT = float(os.environ.get("SCANNER_DEPLOY_MAX_DRAWDOWN_PCT", "5.0"))
SCANNER_DEPLOY_AUTO_STOP_ON_DD = os.environ.get("SCANNER_DEPLOY_AUTO_STOP_ON_DD", "true").lower() in ("1", "true", "yes")
# Raw env (slash or USDT); normalized to *USDT after CRYPTO_SYMBOLS is defined.
_SCANNER_DEPLOY_WATCHLIST_RAW = os.environ.get("SCANNER_DEPLOY_WATCHLIST")
SCANNER_DEPLOY_WATCHLIST: list[str] = []

# Autonomous agent actions (Sprint 3) — when false, silent actors (RiskSentinel
# single-bot pause, RegimeRotation, AlphaDecay, ScannerDeploy, PostTrade auto-patch)
# PROPOSE into the HITL action queue instead of mutating directly. Emergencies
# (kill switch, max-drawdown halt, daily-loss halt) always execute immediately.
AUTO_AGENT_ACTIONS = os.environ.get("AUTO_AGENT_ACTIONS", "false").lower() in ("1", "true", "yes")
AGENT_HITL_TTL_SEC = float(os.environ.get("AGENT_HITL_TTL_SEC", "900"))

# Trading Chatbot / Copilot
TRADE_COPILOT_ENABLED = os.environ.get("TRADE_COPILOT_ENABLED", "true").lower() in ("1", "true", "yes")
TRADE_COPILOT_USE_LLM = os.environ.get("TRADE_COPILOT_USE_LLM", "true").lower() in ("1", "true", "yes")
TRADE_COPILOT_HISTORY_LIMIT = int(os.environ.get("TRADE_COPILOT_HISTORY_LIMIT", "40"))
TRADE_COPILOT_PENDING_TTL_SEC = float(os.environ.get("TRADE_COPILOT_PENDING_TTL_SEC", "600"))
# In-memory analyze/session insight map TTL + max sessions (mirrors pending TTL pattern).
TRADE_COPILOT_SESSION_TTL_SEC = float(os.environ.get("TRADE_COPILOT_SESSION_TTL_SEC", "7200"))
TRADE_COPILOT_SESSION_MAX = int(os.environ.get("TRADE_COPILOT_SESSION_MAX", "32"))

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
    "CRYPTO_ALT": [
        "BNBUSDT", "SOLUSDT", "XRPUSDT", "TRXUSDT", "DOGEUSDT", "ADAUSDT",
        "AVAXUSDT", "LINKUSDT", "TONUSDT", "SHIBUSDT", "SUIUSDT", "DOTUSDT",
        "BCHUSDT", "XLMUSDT", "LTCUSDT", "UNIUSDT", "APTUSDT", "NEARUSDT",
    ],
}

# Long-term market bar archive (1m bars → DB, rollup to 1h after retention window)
ARCHIVE_ENABLED = os.environ.get("ARCHIVE_ENABLED", "true").lower() in ("1", "true", "yes")
ARCHIVE_RETENTION_1M_DAYS = int(os.environ.get("ARCHIVE_RETENTION_1M_DAYS", "90"))
ARCHIVE_RETENTION_1H_DAYS = int(os.environ.get("ARCHIVE_RETENTION_1H_DAYS", "1825"))
ARCHIVE_ROLLUP_INTERVAL = float(os.environ.get("ARCHIVE_ROLLUP_INTERVAL", "3600"))
ARCHIVE_FLUSH_INTERVAL = float(os.environ.get("ARCHIVE_FLUSH_INTERVAL", "60"))
# Hard cap on in-memory archive write buffer (symbol×bar keys). Prevents unbounded
# growth if SQLite flush fails repeatedly; excess oldest rows are dropped after WAL.
ARCHIVE_BUFFER_MAX_ROWS = int(os.environ.get("ARCHIVE_BUFFER_MAX_ROWS", "20000"))
# Screener indicator DF cache (MEMORY #13) — entry + approx MB caps.
SCREENER_CACHE_MAX_ENTRIES = int(os.environ.get("SCREENER_CACHE_MAX_ENTRIES", "200"))
SCREENER_CACHE_MAX_MB = int(os.environ.get("SCREENER_CACHE_MAX_MB", "128"))
# SQLite page cache (kibibytes; PRAGMA uses negative = KiB). Default 64 MB.
SQLITE_CACHE_KB = int(os.environ.get("SQLITE_CACHE_KB", "64000"))
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
ALPACA_BROADCAST_INTERVAL_SEC = float(os.environ.get("ALPACA_BROADCAST_INTERVAL_SEC", "0.75"))
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
# Hard caps so sentiment_events cannot grow without bound (disk → RAM on query).
SENTIMENT_MAX_AGE_HOURS = float(os.environ.get("SENTIMENT_MAX_AGE_HOURS", "168"))  # 7d
SENTIMENT_MAX_EVENTS = int(os.environ.get("SENTIMENT_MAX_EVENTS", "5000"))

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
AGENT_LLM_FALLBACK_CLOUD = os.environ.get("AGENT_LLM_FALLBACK_CLOUD", "true").lower() in ("1", "true", "yes")
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
# When false (Alpaca profile default for testing): live Alpaca feed + SimulatedOMS fills.
# Set true to route orders through Alpaca REST (paper or live URL above).
ALPACA_OMS_ENABLED = os.environ.get("ALPACA_OMS_ENABLED", "true").lower() in (
    "1", "true", "yes",
)
# WebSocket equity stream — auto-resolved to sip or iex when ALPACA_DATA_FEED=auto (default).
ALPACA_DATA_URL = os.environ.get("ALPACA_DATA_URL", "wss://stream.data.alpaca.markets/v2/sip")
ALPACA_DATA_FEED = os.environ.get("ALPACA_DATA_FEED", "auto").strip().lower()  # auto | sip | iex
ALPACA_CRYPTO_ENABLED = os.environ.get("ALPACA_CRYPTO_ENABLED", "true").lower() in (
    "1", "true", "yes",
)
ALPACA_CRYPTO_WS_URL = os.environ.get(
    "ALPACA_CRYPTO_WS_URL",
    "wss://stream.data.alpaca.markets/v1beta3/crypto/us",
)
ALPACA_OPTIONS_ENABLED = os.environ.get("ALPACA_OPTIONS_ENABLED", "true").lower() in (
    "1", "true", "yes",
)
ALPACA_OPTION_FEED = os.environ.get("ALPACA_OPTION_FEED", "indicative").strip().lower()
ALPACA_OPTION_UNDERLYINGS = os.environ.get("ALPACA_OPTION_UNDERLYINGS", "SPY,QQQ,AAPL")
ALPACA_OPTION_SYMBOLS = os.environ.get("ALPACA_OPTION_SYMBOLS", "")

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
    "AAPL": {"price": 333.50, "volatility": 0.0001, "decimals": 2, "asset": "AAPL", "quote": "USD"},
    "TSLA": {"price": 248.00, "volatility": 0.0003, "decimals": 2, "asset": "TSLA", "quote": "USD"},
    "MSFT": {"price": 420.10, "volatility": 0.00008, "decimals": 2, "asset": "MSFT", "quote": "USD"},
    "NVDA": {"price": 175.00, "volatility": 0.0004, "decimals": 2, "asset": "NVDA", "quote": "USD"},
    "AMD": {"price": 160.20, "volatility": 0.0003, "decimals": 2, "asset": "AMD", "quote": "USD"},
    "GOOGL": {"price": 175.40, "volatility": 0.00012, "decimals": 2, "asset": "GOOGL", "quote": "USD"},
    "AMZN": {"price": 180.50, "volatility": 0.00015, "decimals": 2, "asset": "AMZN", "quote": "USD"},
    "NFLX": {"price": 610.80, "volatility": 0.00022, "decimals": 2, "asset": "NFLX", "quote": "USD"},
    "META": {"price": 485.60, "volatility": 0.0002, "decimals": 2, "asset": "META", "quote": "USD"},
    "COIN": {"price": 240.50, "volatility": 0.0005, "decimals": 2, "asset": "COIN", "quote": "USD"},
    "SPY": {"price": 580.00, "volatility": 0.00006, "decimals": 2, "asset": "SPY", "quote": "USD"},
    "QQQ": {"price": 510.00, "volatility": 0.00008, "decimals": 2, "asset": "QQQ", "quote": "USD"},
    "JPM": {"price": 195.40, "volatility": 0.0001, "decimals": 2, "asset": "JPM", "quote": "USD"},
    "V": {"price": 275.60, "volatility": 0.00007, "decimals": 2, "asset": "V", "quote": "USD"},
    "DIS": {"price": 115.30, "volatility": 0.00014, "decimals": 2, "asset": "DIS", "quote": "USD"}
}

# Top-20 liquid spot cryptos by market cap + 24h volume (excl. stables),
# limited to majors with reliable Massive/Polygon USD pairs and yfinance history.
# Seed prices are approximate; live feeds overwrite on connect.
CRYPTO_SYMBOLS = {
    "BTCUSDT": {"price": 63000.0, "volatility": 0.00015, "decimals": 2, "asset": "BTC", "quote": "USDT"},
    "ETHUSDT": {"price": 1780.0, "volatility": 0.0002, "decimals": 2, "asset": "ETH", "quote": "USDT"},
    "BNBUSDT": {"price": 570.0, "volatility": 0.00018, "decimals": 2, "asset": "BNB", "quote": "USDT"},
    "XRPUSDT": {"price": 1.08, "volatility": 0.00025, "decimals": 4, "asset": "XRP", "quote": "USDT"},
    "SOLUSDT": {"price": 77.0, "volatility": 0.0004, "decimals": 2, "asset": "SOL", "quote": "USDT"},
    "TRXUSDT": {"price": 0.3300, "volatility": 0.00028, "decimals": 4, "asset": "TRX", "quote": "USDT"},
    "DOGEUSDT": {"price": 0.0720, "volatility": 0.00045, "decimals": 4, "asset": "DOGE", "quote": "USDT"},
    "ADAUSDT": {"price": 0.1600, "volatility": 0.00028, "decimals": 4, "asset": "ADA", "quote": "USDT"},
    "AVAXUSDT": {"price": 22.50, "volatility": 0.0004, "decimals": 2, "asset": "AVAX", "quote": "USDT"},
    "LINKUSDT": {"price": 8.00, "volatility": 0.00032, "decimals": 2, "asset": "LINK", "quote": "USDT"},
    "TONUSDT": {"price": 1.60, "volatility": 0.00035, "decimals": 3, "asset": "TON", "quote": "USDT"},
    "SHIBUSDT": {"price": 0.00001200, "volatility": 0.00055, "decimals": 8, "asset": "SHIB", "quote": "USDT"},
    "SUIUSDT": {"price": 2.50, "volatility": 0.00042, "decimals": 3, "asset": "SUI", "quote": "USDT"},
    "DOTUSDT": {"price": 5.20, "volatility": 0.0003, "decimals": 3, "asset": "DOT", "quote": "USDT"},
    "BCHUSDT": {"price": 240.0, "volatility": 0.00028, "decimals": 2, "asset": "BCH", "quote": "USDT"},
    "XLMUSDT": {"price": 0.1840, "volatility": 0.0003, "decimals": 4, "asset": "XLM", "quote": "USDT"},
    "LTCUSDT": {"price": 44.00, "volatility": 0.00022, "decimals": 2, "asset": "LTC", "quote": "USDT"},
    "UNIUSDT": {"price": 7.20, "volatility": 0.00035, "decimals": 3, "asset": "UNI", "quote": "USDT"},
    "APTUSDT": {"price": 5.40, "volatility": 0.0004, "decimals": 3, "asset": "APT", "quote": "USDT"},
    "NEARUSDT": {"price": 3.10, "volatility": 0.00038, "decimals": 3, "asset": "NEAR", "quote": "USDT"},
}


def _normalize_crypto_watch_symbol(sym: str) -> str:
    """Map BTC/USD, BTC-USD, BTCUSD, BTC → BTCUSDT for scanner / watchlists."""
    s = (sym or "").strip().upper().replace(" ", "")
    if not s:
        return ""
    s = s.replace("-", "/")
    if "/" in s:
        return f"{s.split('/', 1)[0]}USDT"
    if s.endswith("USD") and not s.endswith("USDT"):
        return f"{s[:-3]}USDT"
    if not s.endswith("USDT"):
        return f"{s}USDT"
    return s


if _SCANNER_DEPLOY_WATCHLIST_RAW and str(_SCANNER_DEPLOY_WATCHLIST_RAW).strip():
    SCANNER_DEPLOY_WATCHLIST = [
        n
        for n in (
            _normalize_crypto_watch_symbol(x)
            for x in str(_SCANNER_DEPLOY_WATCHLIST_RAW).split(",")
        )
        if n
    ]
else:
    SCANNER_DEPLOY_WATCHLIST = list(CRYPTO_SYMBOLS.keys())

# Alpaca US crypto stream (`v1beta3/crypto/us`) — keep watchlist aligned with
# pairs the feed can actually seed / stream (Binance-only alts show as "…" otherwise).
ALPACA_US_CRYPTO_BASES = frozenset({
    "BTC", "ETH", "SOL", "DOGE", "LINK", "AVAX", "LTC", "BCH", "UNI",
    "DOT", "AAVE", "CRV", "SUSHI", "BAT", "YFI", "MKR", "GRT", "SHIB",
    "PEPE", "XRP", "ADA", "XTZ",
})


def _alpaca_crypto_symbols(crypto: dict) -> dict:
    out = {}
    for sym, info in crypto.items():
        asset = str((info or {}).get("asset") or "").upper()
        if not asset:
            s = str(sym or "").upper()
            asset = s[:-4] if s.endswith("USDT") else s
        if asset in ALPACA_US_CRYPTO_BASES:
            out[sym] = info
    return out


# Supported Trading Symbols & Properties based on mode
if TERMINAL_MODE == "LIVE_ALPACA":
    # Equities + Alpaca-tradable crypto (OCC options merge dynamically in the feed).
    _alpaca_crypto = _alpaca_crypto_symbols(CRYPTO_SYMBOLS) if ALPACA_CRYPTO_ENABLED else {}
    SYMBOLS = {**EQUITY_SYMBOLS, **_alpaca_crypto} if _alpaca_crypto else dict(EQUITY_SYMBOLS)
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
