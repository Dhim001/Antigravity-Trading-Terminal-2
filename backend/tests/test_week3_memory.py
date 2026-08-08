"""MEMORY_CENTRIC_REVIEW #41 — deep trainers default to the process pool;
Windows Job Object ceiling with advisory fallback; idle-key evictions for
manager POV volumes, Alpaca symbol maps, and the feature-drift monitor
(#32/#33/#34); portfolio preload guard (#8)."""

import asyncio
import logging
import time

import pytest

from app.services.bots import ml_train_executor
from app.services.bots.ml_train_executor import (
    TORCH_TRAIN_STRATEGIES,
    _parent_trim_validate_candles,
    use_process_pool_for_strategy,
)


# ── #41 default flip ──────────────────────────────────────────────────────


def test_torch_strategies_default_to_process_pool(monkeypatch):
    monkeypatch.setattr("app.config.ML_TRAIN_PROCESS_ISOLATION", True)
    monkeypatch.setattr("app.config.ML_TRAIN_TORCH_IN_PROCESS", False)
    monkeypatch.setattr("app.config.ML_TRAIN_IN_PROCESS_STRATEGIES", frozenset())
    for strat in TORCH_TRAIN_STRATEGIES:
        assert use_process_pool_for_strategy(strat) is True, strat
    assert use_process_pool_for_strategy("ML_SIGNAL_BOOST") is True


def test_spawn_unstable_strategies_default_to_thread(monkeypatch):
    # RL_PPO_AGENT hangs on Windows spawn+CUDA — default in-process even though
    # it is a Torch strategy. Operators can clear the set to re-enable the pool.
    monkeypatch.setattr("app.config.ML_TRAIN_PROCESS_ISOLATION", True)
    monkeypatch.setattr("app.config.ML_TRAIN_TORCH_IN_PROCESS", False)
    monkeypatch.setattr(
        "app.config.ML_TRAIN_IN_PROCESS_STRATEGIES", frozenset({"RL_PPO_AGENT"})
    )
    assert use_process_pool_for_strategy("RL_PPO_AGENT") is False
    assert use_process_pool_for_strategy("LSTM_DIRECTION") is True
    assert use_process_pool_for_strategy("ML_SIGNAL_BOOST") is True


def test_torch_in_process_opt_in_forces_thread(monkeypatch):
    monkeypatch.setattr("app.config.ML_TRAIN_PROCESS_ISOLATION", True)
    monkeypatch.setattr("app.config.ML_TRAIN_TORCH_IN_PROCESS", True)
    monkeypatch.setattr("app.config.ML_TRAIN_IN_PROCESS_STRATEGIES", frozenset())
    for strat in TORCH_TRAIN_STRATEGIES:
        assert use_process_pool_for_strategy(strat) is False, strat
    # GBM still uses the pool even in debugging mode.
    assert use_process_pool_for_strategy("ML_SIGNAL_BOOST") is True


def test_isolation_off_never_uses_pool(monkeypatch):
    monkeypatch.setattr("app.config.ML_TRAIN_PROCESS_ISOLATION", False)
    monkeypatch.setattr("app.config.ML_TRAIN_TORCH_IN_PROCESS", False)
    monkeypatch.setattr(
        "app.config.ML_TRAIN_IN_PROCESS_STRATEGIES", frozenset({"RL_PPO_AGENT"})
    )
    assert use_process_pool_for_strategy("RL_PPO_AGENT") is False


# ── #41 parent-side candle trim ───────────────────────────────────────────


def test_parent_trim_caps_pool_bound_torch_validate(monkeypatch):
    monkeypatch.setattr("app.config.ML_TRAIN_PROCESS_ISOLATION", True)
    monkeypatch.setattr("app.config.ML_TRAIN_TORCH_IN_PROCESS", False)
    candles = [{"time": i} for i in range(20_000)]
    # Default capacity parity → 12k soft default (not the old lean 2500).
    out = _parent_trim_validate_candles("LSTM_DIRECTION", candles, {})
    assert len(out) == 12_000
    assert out[0]["time"] == 20_000 - 12_000


def test_parent_trim_lean_mode_uses_2500_default(monkeypatch):
    monkeypatch.setattr("app.config.ML_TRAIN_PROCESS_ISOLATION", True)
    monkeypatch.setattr("app.config.ML_TRAIN_TORCH_IN_PROCESS", False)
    candles = [{"time": i} for i in range(20_000)]
    out = _parent_trim_validate_candles(
        "LSTM_DIRECTION", candles, {"wf_capacity_parity": False},
    )
    assert len(out) == 2500


def test_parent_trim_respects_validate_max_bars_over_12k(monkeypatch):
    monkeypatch.setattr("app.config.ML_TRAIN_PROCESS_ISOLATION", True)
    monkeypatch.setattr("app.config.ML_TRAIN_TORCH_IN_PROCESS", False)
    # RL_PPO_AGENT is thread-bound by default (spawn+CUDA hang); clear the set
    # so this trim-behavior test exercises the pool-bound path.
    monkeypatch.setattr("app.config.ML_TRAIN_IN_PROCESS_STRATEGIES", frozenset())
    candles = [{"time": i} for i in range(50_000)]
    out = _parent_trim_validate_candles(
        "RL_PPO_AGENT", candles, {"validate_max_bars": 30_000},
    )
    # Capacity parity allows Lab window depth up to 100k.
    assert len(out) == 30_000


def test_parent_trim_lean_still_clamps_to_12k(monkeypatch):
    monkeypatch.setattr("app.config.ML_TRAIN_PROCESS_ISOLATION", True)
    monkeypatch.setattr("app.config.ML_TRAIN_TORCH_IN_PROCESS", False)
    monkeypatch.setattr("app.config.ML_TRAIN_IN_PROCESS_STRATEGIES", frozenset())
    candles = [{"time": i} for i in range(50_000)]
    out = _parent_trim_validate_candles(
        "RL_PPO_AGENT",
        candles,
        {"validate_max_bars": 30_000, "wf_capacity_parity": False},
    )
    assert len(out) == 12_000

def test_parent_trim_noop_for_thread_bound_or_non_torch(monkeypatch):
    monkeypatch.setattr("app.config.ML_TRAIN_PROCESS_ISOLATION", True)
    monkeypatch.setattr("app.config.ML_TRAIN_TORCH_IN_PROCESS", False)
    candles = [{"time": i} for i in range(20_000)]
    # Non-Torch strategy — the worker has no 12k cap, parent must not trim.
    assert len(_parent_trim_validate_candles("ML_SIGNAL_BOOST", candles, {})) == 20_000
    # Torch strategy but thread-bound (debugging opt-in) — no trim either.
    monkeypatch.setattr("app.config.ML_TRAIN_TORCH_IN_PROCESS", True)
    assert len(_parent_trim_validate_candles("LSTM_DIRECTION", candles, {})) == 20_000


# ── #41 Windows Job Object ceiling ────────────────────────────────────────


def test_windows_job_limit_falls_back_to_advisory_on_failure(monkeypatch):
    from app.services.bots import ml_train_limits

    monkeypatch.setattr(ml_train_limits.sys, "platform", "win32")
    monkeypatch.setattr("app.config.ML_TRAIN_RSS_LIMIT_MB", 4096)

    def _boom(limit_bytes, status, limit_mb):
        raise OSError("no job objects here")

    monkeypatch.setattr(ml_train_limits, "_apply_windows_job_limit", _boom)
    status = ml_train_limits.apply_ml_train_rss_limit()
    assert status["ok"] is True
    assert status["method"] == "advisory"
    assert "no job objects here" in status["error"]


def test_windows_job_limit_success_path(monkeypatch):
    from app.services.bots import ml_train_limits

    monkeypatch.setattr(ml_train_limits.sys, "platform", "win32")
    monkeypatch.setattr("app.config.ML_TRAIN_RSS_LIMIT_MB", 2048)
    monkeypatch.setattr(
        ml_train_limits,
        "_apply_windows_job_limit",
        lambda limit_bytes, status, limit_mb: {
            **status, "ok": True, "method": "JobObject",
        },
    )
    status = ml_train_limits.apply_ml_train_rss_limit()
    assert status["ok"] is True
    assert status["method"] == "JobObject"


def test_rss_limit_disabled(monkeypatch):
    from app.services.bots import ml_train_limits

    monkeypatch.setattr("app.config.ML_TRAIN_RSS_LIMIT_MB", 0)
    status = ml_train_limits.apply_ml_train_rss_limit()
    assert status["ok"] is True
    assert status.get("skipped") is True


# ── #32 manager POV volume-key eviction ───────────────────────────────────


def _bare_manager():
    from app.services.bots.manager import BotManagerService

    mgr = BotManagerService.__new__(BotManagerService)
    mgr.active_bots = {}
    return mgr


def _ohlcv(n=60):
    return [
        {"time": 1_700_000_000 + i * 60, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 10 + i}
        for i in range(n)
    ]


def test_recent_bar_volumes_keys_capped_at_32():
    mgr = _bare_manager()
    for i in range(40):
        asyncio.run(mgr._evaluate_bar_close_bots(f"SYM{i}", "1m", _ohlcv()))
    vols = mgr._recent_bar_volumes
    assert len(vols) <= 32
    # Most recent symbols survive; oldest were evicted.
    assert "SYM39" in vols
    assert "SYM0" not in vols
    # Values still capped at 50 bars.
    assert len(vols["SYM39"]) == 50


def test_recent_bar_volumes_protects_active_bot_symbols():
    mgr = _bare_manager()
    mgr.active_bots = {"b1": {"symbol": "KEEP", "status": "RUNNING"}}
    asyncio.run(mgr._evaluate_bar_close_bots("KEEP", "1m", _ohlcv()))
    for i in range(40):
        asyncio.run(mgr._evaluate_bar_close_bots(f"SYM{i}", "1m", _ohlcv()))
    assert "KEEP" in mgr._recent_bar_volumes


# ── #33 Alpaca per-symbol map pruning ─────────────────────────────────────


def _bare_feed(symbols=("BTCUSDT",)):
    from app.services.alpaca_feed import AlpacaFeedService

    feed = AlpacaFeedService.__new__(AlpacaFeedService)
    feed._symbols = {s: {"price": 1.0} for s in symbols}
    feed._last_quote_apply_ts = {}
    feed._crypto_last_trade_event_ts = {}
    feed._sealed_bar_ts = {}
    return feed


def test_prune_symbol_state_maps_drops_dead_symbols():
    feed = _bare_feed(symbols=("LIVE1", "LIVE2"))
    # Pretend many symbols were touched historically.
    for i in range(20):
        feed._last_quote_apply_ts[f"DEAD{i}"] = 1.0
        feed._crypto_last_trade_event_ts[f"DEAD{i}"] = "t"
        feed._sealed_bar_ts[f"DEAD{i}"] = 123
    feed._last_quote_apply_ts["LIVE1"] = 1.0

    feed._prune_symbol_state_maps()

    assert set(feed._last_quote_apply_ts) == {"LIVE1"}
    assert feed._crypto_last_trade_event_ts == {}
    assert feed._sealed_bar_ts == {}


def test_unsubscribe_pops_symbol_state():
    feed = _bare_feed()
    feed._sealed_bar_ts["BTCUSDT"] = 1
    asyncio.run(feed.unsubscribe("BTCUSDT"))
    assert feed._sealed_bar_ts == {}


# ── #34 feature-drift idle-key eviction ───────────────────────────────────


@pytest.fixture
def drift_monitor(tmp_path, monkeypatch):
    from app.services.bots import ml_feature_drift

    monkeypatch.setattr(ml_feature_drift, "DRIFT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ml_feature_drift, "_MAX_BUFFER_KEYS", 4)
    monkeypatch.setattr(ml_feature_drift, "_IDLE_EVICT_SEC", 0.05)
    monitor = ml_feature_drift.FeatureDriftMonitor(window_size=10)
    # Pin the schema gate off: these tests exercise eviction with arbitrary
    # vector widths — they must not depend on SIGNAL_FEATURE_NAMES resolving.
    monkeypatch.setattr(
        ml_feature_drift.FeatureDriftMonitor, "_expected_feature_dim", lambda self: 0
    )
    return monitor


def test_drift_idle_keys_evicted_and_reloaded_from_disk(drift_monitor, monkeypatch):
    from app.services.bots import ml_feature_drift

    # Fill phase: no eviction pressure (high cap, nothing idle).
    monkeypatch.setattr(ml_feature_drift, "_MAX_BUFFER_KEYS", 8)
    monkeypatch.setattr(ml_feature_drift, "_IDLE_EVICT_SEC", 9999.0)
    for i in range(6):
        drift_monitor.record_inference(f"SYM{i}", "S", [1.0, 2.0, 3.0])
    assert len(drift_monitor._buffers) == 6

    # Idle phase: everything ages out, cap drops below the survivor count.
    monkeypatch.setattr(ml_feature_drift, "_MAX_BUFFER_KEYS", 4)
    monkeypatch.setattr(ml_feature_drift, "_IDLE_EVICT_SEC", 0.05)
    time.sleep(0.06)
    drift_monitor.record_inference("FRESH", "S", [1.0])
    assert set(drift_monitor._buffers) == {"FRESH:S"}

    # Evicted keys were persisted — a later access lazily reloads them.
    buf = drift_monitor._load_buffer("SYM0", "S")
    assert buf and buf[0] == [1.0, 2.0, 3.0]


def test_drift_lru_eviction_when_nothing_is_idle(drift_monitor, monkeypatch):
    from app.services.bots import ml_feature_drift

    monkeypatch.setattr(ml_feature_drift, "_IDLE_EVICT_SEC", 9999.0)
    for i in range(7):
        drift_monitor.record_inference(f"SYM{i}", "S", [float(i)])
    assert len(drift_monitor._buffers) <= 4
    # Oldest-accessed keys evicted first; newest survive.
    keys = set(drift_monitor._buffers)
    assert "SYM6:S" in keys
    assert "SYM0:S" not in keys


# ── #8 portfolio preload warning ──────────────────────────────────────────


class _FakeBacktester:
    def run_backtest(self, symbol, strategy, config, candles, cancel_cb=None):
        return {"total_pnl": 1.0, "trade_count": 1, "trades": [], "equity_curve": []}


def _portfolio_config(n_symbols):
    from app.services.bots.backtest_portfolio import PortfolioBacktestConfig

    return PortfolioBacktestConfig(
        symbols=[
            {"symbol": f"S{i}", "strategy": "CHART_AGENT", "weight": 1.0}
            for i in range(n_symbols)
        ],
        total_capital=100_000.0,
    )


def test_portfolio_preload_warns_over_threshold(monkeypatch, caplog):
    from app.services.bots import backtest_portfolio

    monkeypatch.setattr(backtest_portfolio, "parallel_worker_count", lambda n: 1)
    n = backtest_portfolio.PORTFOLIO_PRELOAD_WARN_SYMBOLS + 2
    candles = {f"S{i}": [{"time": j, "close": 1.0} for j in range(10)] for i in range(n)}

    with caplog.at_level(logging.WARNING, logger=backtest_portfolio.logger.name):
        backtest_portfolio.run_portfolio_backtest(
            _FakeBacktester(), _portfolio_config(n), candles,
        )
    assert any("pre-materialized" in r.message for r in caplog.records)


def test_portfolio_preload_under_threshold_is_quiet(monkeypatch, caplog):
    from app.services.bots import backtest_portfolio

    monkeypatch.setattr(backtest_portfolio, "parallel_worker_count", lambda n: 1)
    candles = {f"S{i}": [{"time": j, "close": 1.0} for j in range(10)] for i in range(2)}

    with caplog.at_level(logging.WARNING, logger=backtest_portfolio.logger.name):
        backtest_portfolio.run_portfolio_backtest(
            _FakeBacktester(), _portfolio_config(2), candles,
        )
    assert not any("pre-materialized" in r.message for r in caplog.records)
