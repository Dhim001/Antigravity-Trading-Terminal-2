import random
from collections import Counter
from datetime import datetime, timezone

from app.config import BOT_DAILY_LOSS_LIMIT_PCT
from app.services.bots.backtest_costs import (
    CostModel,
    entry_fill_price,
    exit_fill_price,
    parse_cost_config,
    trade_fee,
)
from app.services.bots.indicators import first_eval_index, merge_strategy_config, prepare_strategy_df
from app.services.bots.strategies_chart_agent import (
    build_signal_from_insight,
)
from app.services.bots.tick_strategies import is_tick_strategy
from app.services.market.resample import resample_candles_for_timeframe
from app.services.market.timeframes import normalize_timeframe
from app.services.bots.risk_gate import RiskGate
from app.services.bots.risk_sizing import entry_quantity_from_risk, parse_risk_sizing_config
from app.services.bots.strategies import get_strategy, normalize_strategy_name
from app.services.bots.backtest_analytics import drawdown_curve, enrich_summary
from app.services.bots.backtest_parity import build_htf_bias_lookup
from app.services.bots.backtest_progress import ProgressThrottle
from app.services.bots.strategy_filter import build_filter_from_config
from app.services.bots.strategy_runtime import (
    ExecutionChain,
    apply_indicator_parity_gates,
    apply_shared_signal_gates,
    apply_vae_regime_meta_gate,
    chart_filter_reject_block,
    evaluate_parity_pretrade,
    scale_entry_quantity,
)
from app.services.bots.take_profit import merge_tp_config, resolve_take_profit
from app.services.bots.backtest_category_metrics import (
    CategoryMetricsCollector,
    load_ml_feature_importance,
)

_SIM_MODES = frozenset({"live_aligned", "research", "research_fast"})
_MIN_QTY = 0.001
_DEFAULT_ALLOCATION = 10_000.0
MAX_BLOCKED_EVENTS = 200
_DEFAULT_STOP_PCT = 0.02


def _resolve_entry_stop(
    side: str,
    price: float,
    signal_data: dict | None,
) -> tuple[float | None, str | None]:
    """Return (stop_price, error_reason). Tolerates None/missing stop_loss_distance."""
    data = signal_data or {}
    price = float(price or 0)
    if price <= 0:
        return None, "invalid price"

    raw_price = data.get("stop_loss_price")
    if raw_price is not None and raw_price != "":
        try:
            stop = float(raw_price)
        except (TypeError, ValueError):
            return None, "invalid stop_loss_price"
    else:
        raw_dist = data.get("stop_loss_distance")
        try:
            dist = float(raw_dist) if raw_dist is not None and raw_dist != "" else 0.0
        except (TypeError, ValueError):
            dist = 0.0
        if dist <= 0:
            atr_val = data.get("atr") or data.get("ATR_14") or data.get("ATRr_14")
            try:
                atr_f = float(atr_val) if atr_val is not None else 0.0
            except (TypeError, ValueError):
                atr_f = 0.0
            if atr_f > 0:
                dist = atr_f * 1.5
            else:
                dist = price * _DEFAULT_STOP_PCT
        stop = price - dist if str(side).upper() == "BUY" else price + dist

    if abs(stop - price) <= 0:
        return None, "zero stop distance"
    return stop, None


def _append_blocked_event(
    events: list[dict],
    *,
    total_counter: list[int],
    kind: str,
    reason: str,
    bar_time,
    side: str | None = None,
    signal: str | None = None,
    bucket: str | None = None,
) -> None:
    total_counter[0] += 1
    if len(events) >= MAX_BLOCKED_EVENTS:
        return
    events.append({
        "time": int(bar_time) if bar_time is not None else 0,
        "kind": kind,
        "reason": str(reason)[:240],
        "side": side,
        "signal": signal,
        "bucket": bucket,
    })


def _maybe_meta_label_explain(
    signal_data: dict | None,
    cfg: dict,
    *,
    symbol: str,
    timeframe: str,
    bar_time,
    side: str,
    strat_key: str,
) -> dict | None:
    if strat_key != "CHART_AGENT" or not signal_data:
        return None
    if not cfg.get("calibration_gate_enabled"):
        return None
    mode = str(cfg.get("meta_label_model_mode") or "wilson").lower()
    if mode not in ("gbm", "hybrid"):
        return None
    snap = signal_data.get("insight_snapshot")
    if not isinstance(snap, dict):
        return None
    bot_id = str(cfg.get("backtest_bot_id") or cfg.get("_bot_id") or "backtest")
    insight = {
        **snap,
        "sub_reports": signal_data.get("sub_reports") or snap.get("sub_reports"),
    }
    from app.services.bots.meta_label_model import explain_prediction

    return explain_prediction(
        bot_id,
        insight,
        symbol=symbol,
        side=side,
        timeframe=timeframe,
        bar_time=bar_time,
    )


def _utc_day_key(bar_time) -> str | None:
    if bar_time is None:
        return None
    try:
        return datetime.fromtimestamp(int(bar_time), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _check_long_sl_tp(position: dict, bar_low: float, bar_high: float, *, randomize_sl_tp: bool = False) -> tuple[str | None, float | None]:
    """Intra-bar SL/TP for a long position.

    When *randomize_sl_tp* is True, a coin-flip decides priority when both SL
    and TP are hit on the same bar (since intra-bar order is unknowable from OHLC).
    Otherwise defaults to conservative: SL before TP.
    """
    sl = position.get("stop_loss")
    tp = position.get("take_profit")

    sl_hit = sl is not None and bar_low <= sl
    tp_hit = tp is not None and bar_high >= tp

    if sl_hit and tp_hit and randomize_sl_tp:
        if random.random() < 0.5:
            return "TP", tp
        return "SL", sl

    if sl_hit:
        return "SL", sl
    if tp_hit:
        return "TP", tp
    return None, None


def _check_short_sl_tp(position: dict, bar_low: float, bar_high: float, *, randomize_sl_tp: bool = False) -> tuple[str | None, float | None]:
    """Intra-bar SL/TP for a short position.

    When *randomize_sl_tp* is True, a coin-flip decides priority when both SL
    and TP are hit on the same bar.
    """
    sl = position.get("stop_loss")
    tp = position.get("take_profit")

    sl_hit = sl is not None and bar_high >= sl
    tp_hit = tp is not None and bar_low <= tp

    if sl_hit and tp_hit and randomize_sl_tp:
        if random.random() < 0.5:
            return "TP", tp
        return "SL", sl

    if sl_hit:
        return "SL", sl
    if tp_hit:
        return "TP", tp
    return None, None


def _update_trailing_stop_short(position: dict, bar_low: float, bar_high: float, trailing_pct: float) -> None:
    if trailing_pct <= 0:
        return
    position["low_watermark"] = min(position.get("low_watermark", bar_high), bar_low)
    new_sl = position["low_watermark"] * (1 + trailing_pct / 100)
    current_sl = position.get("stop_loss")
    position["stop_loss"] = min(current_sl, new_sl) if current_sl is not None else new_sl


def _update_trailing_stop(position: dict, bar_low: float, bar_high: float, trailing_pct: float) -> None:
    if trailing_pct <= 0:
        return
    position["high_watermark"] = max(position["high_watermark"], bar_high)
    new_sl = position["high_watermark"] * (1 - trailing_pct / 100)
    position["stop_loss"] = max(position.get("stop_loss") or 0, new_sl)


# 3.3-C: Chandelier ATR trailing stop helpers.
def _update_chandelier_stop(
    position: dict,
    bar_high: float,
    atr: float,
    multiplier: float = 3.0,
) -> None:
    """Long Chandelier Exit: trail from highest-high minus N×ATR.

    When PnL already exceeds 2×ATR gained (the 'profit-lock' zone), tighten
    the multiplier to 2× to protect unrealised gains more aggressively.
    """
    if atr <= 0:
        return
    position["high_watermark"] = max(position.get("high_watermark", bar_high), bar_high)
    # Tighten once price has moved >2×ATR in our favour (profit-lock).
    entry_price = float(position.get("entry_price") or bar_high)
    profit_atr_units = (position["high_watermark"] - entry_price) / atr
    effective_mult = 2.0 if profit_atr_units >= 2.0 else multiplier
    new_sl = position["high_watermark"] - effective_mult * atr
    position["stop_loss"] = max(position.get("stop_loss") or 0, new_sl)


def _update_chandelier_stop_short(
    position: dict,
    bar_low: float,
    atr: float,
    multiplier: float = 3.0,
) -> None:
    """Short Chandelier Exit: trail from lowest-low plus N×ATR."""
    if atr <= 0:
        return
    position["low_watermark"] = min(position.get("low_watermark", bar_low), bar_low)
    entry_price = float(position.get("entry_price") or bar_low)
    profit_atr_units = (entry_price - position["low_watermark"]) / atr
    effective_mult = 2.0 if profit_atr_units >= 2.0 else multiplier
    new_sl = position["low_watermark"] + effective_mult * atr
    current_sl = position.get("stop_loss")
    position["stop_loss"] = min(current_sl, new_sl) if current_sl is not None else new_sl


def _sharpe_ratio(equity_curve: list[dict]) -> float | None:
    if len(equity_curve) < 3:
        return None
    returns: list[float] = []
    for j in range(1, len(equity_curve)):
        prev_eq = equity_curve[j - 1].get("equity")
        curr_eq = equity_curve[j].get("equity")
        if prev_eq and prev_eq > 0 and curr_eq is not None:
            returns.append((float(curr_eq) - float(prev_eq)) / float(prev_eq))
    if len(returns) < 2:
        return None
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = variance ** 0.5
    if std_r < 1e-12:
        return None
    t0 = equity_curve[0].get("time")
    t1 = equity_curve[-1].get("time")
    if t0 and t1 and t1 > t0:
        years = (int(t1) - int(t0)) / (365.25 * 86400)
        if years > 0:
            return round((mean_r / std_r) * (len(returns) / years) ** 0.5, 2)
    return round((mean_r / std_r) * (len(returns) ** 0.5), 2)


def _max_consecutive_losses(closed: list[dict]) -> int:
    streak = 0
    best = 0
    for trade in closed:
        if (trade.get("pnl") or 0) < 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def _compute_summary(
    closed: list[dict],
    *,
    total_pnl: float,
    win_rate: float,
    max_drawdown: float,
    trade_count: int,
    starting_equity: float,
    equity_curve: list[dict] | None = None,
    bars_in_market: int = 0,
    eval_bars: int = 0,
    blocked_entries: int = 0,
    filter_rejects: dict | None = None,
    blocked_events: list | None = None,
    blocked_events_total: int = 0,
    total_fees: float = 0.0,
    slippage_bps: float = 0.0,
    fee_bps: float = 0.0,
    halted: bool = False,
    daily_loss_halted: bool = False,
    streak_veto_latched: bool = False,
    streak_cooldown_active: bool = False,
    streak_cooldown_resumed: bool = False,
    streak_veto_blocks: int = 0,
) -> dict:
    wins = [float(t["pnl"]) for t in closed if (t.get("pnl") or 0) > 0]
    losses = [float(t["pnl"]) for t in closed if (t.get("pnl") or 0) < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 2)
    elif gross_profit > 0:
        profit_factor = None
    else:
        profit_factor = 0.0

    hold_secs = [int(t["hold_seconds"]) for t in closed if t.get("hold_seconds")]
    avg_hold_hours = (
        round(sum(hold_secs) / len(hold_secs) / 3600, 2) if hold_secs else 0.0
    )
    long_closed = [t for t in closed if t.get("position_side") == "BUY"]
    short_closed = [t for t in closed if t.get("position_side") == "SELL"]
    long_pnl = sum(float(t.get("pnl") or 0) for t in long_closed)
    short_pnl = sum(float(t.get("pnl") or 0) for t in short_closed)

    return {
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 2),
        "total_trades": trade_count,
        "max_drawdown": round(max_drawdown, 2),
        "profit_factor": profit_factor,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "expectancy": round(total_pnl / trade_count, 2) if trade_count else 0.0,
        "return_pct": round((total_pnl / starting_equity) * 100, 2) if starting_equity else 0.0,
        "avg_hold_hours": avg_hold_hours,
        "largest_win": round(max(wins), 2) if wins else 0.0,
        "largest_loss": round(min(losses), 2) if losses else 0.0,
        "long_trades": len(long_closed),
        "short_trades": len(short_closed),
        "long_pnl": round(long_pnl, 2),
        "short_pnl": round(short_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "sharpe_ratio": _sharpe_ratio(equity_curve or []),
        "max_consecutive_losses": _max_consecutive_losses(closed),
        "time_in_market_pct": round(
            (bars_in_market / eval_bars) * 100, 2,
        ) if eval_bars > 0 else 0.0,
        "blocked_entries": blocked_entries,
        "filter_rejects": filter_rejects or {},
        "blocked_events": blocked_events or [],
        "blocked_events_total": blocked_events_total,
        "blocked_events_truncated": blocked_events_total > len(blocked_events or []),
        "total_fees": round(total_fees, 2),
        "slippage_bps": slippage_bps,
        "fee_bps": fee_bps,
        "halted": bool(halted),
        "daily_loss_halted": bool(daily_loss_halted),
        "streak_veto_latched": bool(streak_veto_latched),
        "streak_cooldown_active": bool(streak_cooldown_active),
        "streak_cooldown_resumed": bool(streak_cooldown_resumed),
        "streak_veto_blocks": int(streak_veto_blocks),
    }


class BacktesterService:
    _CACHE_TTL_SEC = 300  # 5 min — auto-expire stale sweep DFs
    _CACHE_MAX_ENTRIES = 10

    def __init__(self, screener_service):
        self.screener = screener_service
        self._risk_gate = RiskGate()
        self._candle_cache: dict[str, tuple[float, "pd.DataFrame"]] = {}  # (timestamp, df)

    # ── Candle cache for parameter sweeps ──────────────────────────────────
    def _candle_cache_key(
        self,
        symbol: str,
        strategy_name: str,
        n_candles: int,
        config: dict | None = None,
    ) -> str:
        from app.services.bots.backtest_indicator_cache import indicator_fingerprint

        fp = indicator_fingerprint(strategy_name, config or {})
        return f"{symbol}:{strategy_name}:{n_candles}:{fp}"

    def cache_candles(self, symbol: str, strategy_name: str, candles: list, config: dict) -> str:
        """Pre-compute and cache the indicator DataFrame for sweep reuse.

        Returns the cache key for retrieval via get_cached_candles().
        Cache is keyed by indicator fingerprint — risk-only param sweeps share one DF.
        """
        import time as _time

        self._evict_expired_cache()
        key = self._candle_cache_key(symbol, strategy_name, len(candles), config)
        df = self.screener.process_candles(
            symbol, candles, config, strategy_name, full_history=True,
        )
        if not df.empty:
            self._candle_cache[key] = (_time.monotonic(), df)
        return key

    def get_cached_candles(
        self,
        symbol: str,
        strategy_name: str,
        n_candles: int,
        config: dict | None = None,
    ) -> "pd.DataFrame | None":
        """Return a deep copy of cached indicator DF, or None if not cached."""
        import time as _time

        key = self._candle_cache_key(symbol, strategy_name, n_candles, config)
        entry = self._candle_cache.get(key)
        if entry is not None:
            ts, df = entry
            if _time.monotonic() - ts > self._CACHE_TTL_SEC:
                self._candle_cache.pop(key, None)
                return None
            return df.copy()
        return None

    def _evict_expired_cache(self):
        """Remove stale cache entries and enforce max size."""
        import time as _time

        now = _time.monotonic()
        expired = [k for k, (ts, _) in self._candle_cache.items()
                   if now - ts > self._CACHE_TTL_SEC]
        for k in expired:
            self._candle_cache.pop(k, None)
        # Cap to max entries
        while len(self._candle_cache) > self._CACHE_MAX_ENTRIES:
            oldest_key = min(self._candle_cache, key=lambda k: self._candle_cache[k][0])
            self._candle_cache.pop(oldest_key, None)

    def clear_candle_cache(self):
        """Clear the candle cache after a sweep completes."""
        self._candle_cache.clear()

    def run_backtest(
        self,
        symbol: str,
        strategy_name: str,
        config: dict,
        candles: list,
        *,
        progress_cb=None,
        cancel_cb=None,
    ) -> dict:
        """
        Bar-close backtest aligned with live BotManager semantics:
        long-only entries, allocation-based sizing, risk gates, intra-bar SL/TP.
        Tick strategies replay simulated intra-bar paths from 1m candles.
        """
        if is_tick_strategy(strategy_name):
            from app.services.bots.backtest_tick import run_tick_backtest

            return run_tick_backtest(
                self, symbol, strategy_name, config, candles,
                progress_cb=progress_cb, cancel_cb=cancel_cb,
            )

        if not candles or len(candles) < 50:
            return {"error": "Not enough historical data"}

        # Copy so we can inject ML model lookup fields without mutating caller config.
        cfg = dict(config or {})
        sym_u = str(symbol or "").upper()
        if sym_u:
            cfg.setdefault("symbol", sym_u)
            # ML strategies resolve models via model_symbol / _symbol — mirror WF path.
            if not str(cfg.get("model_symbol") or "").strip():
                cfg["model_symbol"] = sym_u
        # Normalize TF for model-dir lookup (SYMBOL__5M etc.). Callers should set
        # this; default keeps 1m parity with live bots that omit the field.
        from app.services.bots.ml_model_artifacts import normalize_model_timeframe

        cfg["timeframe"] = normalize_model_timeframe(cfg.get("timeframe") or "1m")
        sim_mode = str(cfg.get("sim_mode") or "live_aligned").lower()
        if sim_mode not in _SIM_MODES:
            sim_mode = "live_aligned"
        research = sim_mode in ("research", "research_fast")
        research_fast = sim_mode == "research_fast"
        live_parity = cfg.get("live_parity")
        if live_parity is None:
            # research_fast skips the heaviest live_parity gates by default;
            # live_aligned keeps full parity. Explicit live_parity overrides.
            live_parity = sim_mode == "live_aligned"
        else:
            live_parity = bool(live_parity)
        if research_fast and cfg.get("live_parity") is None:
            live_parity = False
        allocation = float(cfg.get("allocation") or _DEFAULT_ALLOCATION)
        if allocation <= 0:
            allocation = _DEFAULT_ALLOCATION

        # Use cached indicator DF when available (sweep reuse)
        cached = self.get_cached_candles(symbol, strategy_name, len(candles), cfg)
        if cached is not None:
            df = cached
        else:
            df = self.screener.process_candles(
                symbol, candles, cfg, strategy_name, full_history=True,
            )
        if df.empty:
            return {"error": "Failed to calculate indicators"}

        df = prepare_strategy_df(df, strategy_name, cfg)
        strat_key = normalize_strategy_name(strategy_name)
        from app.services.agent.rule_engine import (
            clear_backtest_sentiment_cache,
            prime_backtest_sentiment_cache,
        )

        if strat_key == "CHART_AGENT":
            prime_backtest_sentiment_cache(symbol)

        def _release_chart_agent_cache() -> None:
            if strat_key == "CHART_AGENT":
                clear_backtest_sentiment_cache()

        strat_filter = build_filter_from_config(cfg) if live_parity else None
        if live_parity and strat_key != "CHART_AGENT":
            filter_name = str(cfg.get("filter_strategy") or "").strip()
            if filter_name:
                filt_key = normalize_strategy_name(filter_name)
                filt_cfg = merge_strategy_config(
                    filt_key,
                    (cfg or {}).get("filter_config") or {},
                )
                df = prepare_strategy_df(df, filt_key, filt_cfg)
        strategy = get_strategy(strategy_name, cfg)
        merged_config = merge_tp_config(strategy_name, cfg)
        min_confidence = float(merged_config.get("min_confidence", 0.55))
        trailing_pct = float(
            cfg.get("trailing_stop_percent") or cfg.get("stop_loss_percent") or 0,
        )
        # 3.3-C: Chandelier ATR trailing stop config.
        use_chandelier = bool(cfg.get("chandelier_stop_enabled", False))
        chandelier_mult = float(cfg.get("chandelier_multiplier") or 3.0)
        chandelier_mult = max(1.0, min(10.0, chandelier_mult))
        risk_cfg = parse_risk_sizing_config(cfg)
        loss_limit = allocation * (BOT_DAILY_LOSS_LIMIT_PCT / 100.0)
        slippage_bps, fee_bps = parse_cost_config(cfg)
        cost_model = CostModel.from_config(cfg)  # per-backtest copy for thread safety
        randomize_sl_tp = bool(cfg.get("randomize_sl_tp", False))
        total_fees = 0.0
        chart_cfg = merge_strategy_config("CHART_AGENT", cfg) if strat_key == "CHART_AGENT" else {}

        if strat_key == "CHART_AGENT":
            from app.services.agent.rule_engine import score_at_index

            confirm_tf = (chart_cfg.get("confirm_timeframe") or "").strip()
            htf_df = None
            if confirm_tf:
                try:
                    cf_tf = normalize_timeframe(confirm_tf)
                    htf_candles = resample_candles_for_timeframe(candles, cf_tf)
                    if htf_candles:
                        htf_df = self.screener.process_candles(
                            symbol, htf_candles, cfg, strategy_name, full_history=True,
                        )
                        htf_df = prepare_strategy_df(htf_df, strategy_name, cfg)
                except ValueError:
                    htf_df = None

            def _htf_closed_index(bar_time_val) -> int | None:
                if htf_df is None or htf_df.empty or bar_time_val is None:
                    return None
                mask = htf_df["time"] <= int(bar_time_val)
                if not mask.any():
                    return None
                return int(htf_df.index[mask][-1])

            def _chart_agent_signal(i: int) -> dict:
                insight = score_at_index(df, i, symbol)
                if not insight:
                    return {"signal": "NONE"}
                insight_dict = insight.to_dict()
                insight_dict.setdefault("timeframe", normalize_timeframe(cfg.get("timeframe") or "1m"))
                confirm_insight = None
                if htf_df is not None:
                    bar_time_val = df.iloc[i].get("time")
                    htf_idx = _htf_closed_index(bar_time_val)
                    if htf_idx is not None and htf_idx >= 1:
                        confirm = score_at_index(htf_df, htf_idx, symbol)
                        if confirm:
                            confirm_insight = confirm.to_dict()
                backtest_bot_id = str(cfg.get("_bot_id") or cfg.get("backtest_bot_id") or "backtest")
                return build_signal_from_insight(
                    insight_dict,
                    chart_cfg,
                    confirm_insight=confirm_insight,
                    bot_id=backtest_bot_id,
                    symbol=symbol,
                    timeframe=normalize_timeframe(cfg.get("timeframe") or "1m"),
                )
        else:
            _chart_agent_signal = None

        htf_bias_lookup: list[tuple[int, str]] = []
        if live_parity and strat_key != "CHART_AGENT":
            confirm_tf = str(cfg.get("confirm_timeframe") or "").strip()
            if confirm_tf:
                try:
                    cf_tf = normalize_timeframe(confirm_tf)
                    htf_candles = resample_candles_for_timeframe(candles, cf_tf)
                    htf_bias_lookup = build_htf_bias_lookup(htf_candles or [])
                except ValueError:
                    htf_bias_lookup = []

        parity_gate_blocks = 0
        trade_log = []
        position = None
        equity = allocation
        starting_equity = allocation
        peak_equity = equity
        max_drawdown = 0.0
        equity_curve = []
        sample_stride = max(1, (len(df) - 1) // 500)
        start_i = first_eval_index(df, strategy_name, cfg)
        last_signal_bar_time = None
        daily_pnl = 0.0
        daily_pnl_day: str | None = None
        halted = False
        daily_loss_halted = False
        bot_stub = {"status": "RUNNING", "allocation": allocation, "config": cfg, "symbol": symbol}
        blocked_entries = 0
        blocked_events: list[dict] = []
        blocked_events_total = [0]
        streak_veto_latched = False
        streak_cooldown_resumed = False
        streak_veto_blocks = 0
        filter_rejects: dict[str, int] = {
            "min_score": 0,
            "trend": 0,
            "vol": 0,
            "htf": 0,
            "confidence": 0,
            "calibration": 0,
            "other": 0,
        }
        bars_in_market = 0
        bt_timeframe = normalize_timeframe(cfg.get("timeframe") or "1m")
        cat_metrics = CategoryMetricsCollector(strat_key)
        signal_counts: dict[str, int] = {"BUY": 0, "SELL": 0, "CLOSE": 0, "NONE": 0}
        reject_reason_counts: Counter = Counter()
        evaluate_errors = 0
        bars_evaluated = 0

        def _record_blocked(
            kind: str,
            reason: str,
            bar_time,
            *,
            side: str | None = None,
            signal: str | None = None,
            bucket: str | None = None,
        ) -> None:
            _append_blocked_event(
                blocked_events,
                total_counter=blocked_events_total,
                kind=kind,
                reason=reason,
                bar_time=bar_time,
                side=side,
                signal=signal,
                bucket=bucket,
            )

        def _roll_daily(bar_time) -> None:
            nonlocal daily_pnl, daily_pnl_day, halted, daily_loss_halted
            day = _utc_day_key(bar_time)
            if day is None:
                return
            if daily_pnl_day != day:
                daily_pnl_day = day
                daily_pnl = 0.0
                # Daily loss halt is per UTC day — resume on the next day.
                if halted:
                    halted = False
                    daily_loss_halted = False
                    bot_stub["status"] = "RUNNING"

        def _closed_exit_series() -> tuple[list[float], list[float | int | None]]:
            pnls: list[float] = []
            times: list[float | int | None] = []
            for t in trade_log:
                if t.get("is_exit") and t.get("pnl") is not None:
                    pnls.append(float(t["pnl"]))
                    times.append(t.get("time"))
            return pnls, times

        def _apply_live_parity_pretrade(
            *,
            side: str,
            signal: str,
            signal_data: dict,
            bar_time,
            qty: float,
            chain: ExecutionChain,
            row_i: int,
        ) -> float | None:
            """Shared live_parity PreTrade + streak cool-down. None = blocked."""
            nonlocal blocked_entries, streak_veto_latched, streak_cooldown_resumed
            nonlocal streak_veto_blocks

            from app.services.bots.pretrade_context import (
                apply_reduce_size_multiplier,
                get_bot_streak_cooldown_hold,
                is_cool_until_active,
                set_bot_streak_cooldown,
            )

            now_f = None
            if bar_time is not None:
                try:
                    now_f = float(bar_time)
                except (TypeError, ValueError):
                    now_f = None

            # Live manager: cool-down blocks entries before PreTrade re-eval.
            hold = get_bot_streak_cooldown_hold(bot_stub, now=now_f)
            if hold:
                blocked_entries += 1
                rem = hold.get("remaining_sec")
                reason = (
                    hold.get("block_reason")
                    or (
                        f"Pre-Trade streak cool-down ({rem}s remaining) — "
                        "resume after cool-down / lookback decay"
                    )
                )
                chain.record("pretrade", ok=False, reason=reason)
                _record_blocked(
                    "pretrade_streak_cooldown",
                    reason,
                    bar_time,
                    side=side,
                    signal=signal,
                )
                return None

            exit_pnls, exit_times = _closed_exit_series()
            bt_bot_id = str(cfg.get("_bot_id") or cfg.get("backtest_bot_id") or "backtest")
            size_cfg = dict(cfg)
            size_cfg.setdefault("symbol", symbol)
            qty = scale_entry_quantity(
                qty,
                signal_data=signal_data,
                bot_config=size_cfg,
                bot_id=bt_bot_id,
                recent_closed_pnls=exit_pnls[-3:],
            )

            anomaly = None
            try:
                from app.services.agent.anomaly_detector import detect_bar_anomaly

                anomaly = detect_bar_anomaly(df, row_i)
            except Exception:
                anomaly = None

            pt = evaluate_parity_pretrade(
                side=side,
                symbol=symbol,
                bar_time=bar_time,
                bot_config=cfg,
                recent_exit_pnls=exit_pnls,
                recent_exit_times=exit_times,
                anomaly=anomaly,
                sentiment=None,
            )
            streak_action = pt.get("streak_action") if isinstance(pt, dict) else None
            if pt.get("verdict") == "VETO":
                blocked_entries += 1
                if pt.get("streak_veto"):
                    streak_veto_latched = True
                    streak_veto_blocks += 1
                    cool_sec = 0
                    if isinstance(streak_action, dict):
                        cool_sec = int(streak_action.get("cooldown_sec") or 0)
                        bot_stub["_pretrade_streak_count"] = int(
                            streak_action.get("streak") or 0
                        )
                    if cool_sec > 0 and now_f is not None:
                        set_bot_streak_cooldown(bot_stub, cool_sec, now=now_f)
                    reason = (
                        f"failures_streak VETO latched: {pt.get('reasoning')} "
                        f"(cool-down {cool_sec}s; resumes after lookback decay)"
                    )
                    kind = "pretrade_streak_veto"
                else:
                    reason = pt.get("reasoning") or "PreTrade parity veto"
                    kind = "pretrade"
                chain.record("pretrade", ok=False, reason=reason)
                _record_blocked(
                    kind,
                    reason,
                    bar_time,
                    side=side,
                    signal=signal,
                )
                return None

            # Lookback decay / cool-down expired → unlock after a prior latch.
            if streak_veto_latched and not pt.get("streak_veto"):
                if not is_cool_until_active(
                    bot_stub.get("_pretrade_streak_cool_until"), now=now_f
                ):
                    streak_veto_latched = False
                    streak_cooldown_resumed = True

            if pt.get("verdict") == "REDUCE_SIZE":
                qty, _size_note = apply_reduce_size_multiplier(
                    qty,
                    float(pt.get("size_multiplier") or 0.5),
                    vetoes=pt.get("vetoes") or [],
                    recent_closed_pnls=exit_pnls[-3:],
                    use_regime_sizing=bool(cfg.get("use_regime_sizing", True)),
                )
                chain.record(
                    "pretrade",
                    ok=True,
                    reason=pt.get("reasoning"),
                    size_multiplier=pt.get("size_multiplier"),
                    size_note=_size_note,
                )
            return qty

        def _close_position(bar_time, exit_price: float, reason: str) -> None:
            nonlocal position, equity, daily_pnl, halted, daily_loss_halted, total_fees
            if not position:
                return
            side = position["side"]
            qty = position["qty"]
            entry_fill = float(position.get("entry_fill") or position["entry_price"])
            exit_side = "SELL" if side == "BUY" else "BUY"
            exit_fill = exit_fill_price(exit_price, exit_side, slippage_bps)
            exit_fee = trade_fee(exit_fill * qty, fee_bps)
            if side == "BUY":
                profit = (exit_fill - entry_fill) * qty - exit_fee - float(position.get("entry_fee") or 0)
            else:
                profit = (entry_fill - exit_fill) * qty - exit_fee - float(position.get("entry_fee") or 0)
            equity += profit
            total_fees += exit_fee
            daily_pnl += profit
            entry_time = position.get("entry_time")
            hold_seconds = None
            if entry_time is not None and bar_time is not None:
                try:
                    hold_seconds = max(0, int(bar_time) - int(entry_time))
                except (TypeError, ValueError):
                    hold_seconds = None
            exit_row = {
                "time": int(bar_time) if bar_time is not None else 0,
                "side": "SELL" if side == "BUY" else "BUY",
                "price": round(exit_fill, 4),
                "quantity": round(qty, 6),
                "pnl": round(profit, 2),
                "is_exit": True,
                "reason": reason,
                "fee": round(exit_fee, 4),
            }
            if hold_seconds is not None:
                exit_row["hold_seconds"] = hold_seconds
            entry_px = float(position.get("entry_fill") or position["entry_price"])
            hi = float(position.get("excursion_high") or entry_px)
            lo = float(position.get("excursion_low") or entry_px)
            if side == "BUY":
                mfe_pct = (hi - entry_px) / entry_px * 100 if entry_px > 0 else 0.0
                mae_pct = (entry_px - lo) / entry_px * 100 if entry_px > 0 else 0.0
            else:
                mfe_pct = (entry_px - lo) / entry_px * 100 if entry_px > 0 else 0.0
                mae_pct = (hi - entry_px) / entry_px * 100 if entry_px > 0 else 0.0
            exit_row["position_side"] = side
            exit_row["mfe_pct"] = round(mfe_pct, 3)
            exit_row["mae_pct"] = round(mae_pct, 3)
            trade_log.append(exit_row)
            entry_conf = position.get("entry_confidence") if position else None
            entry_regime = position.get("entry_regime") if position else None
            # position cleared below — capture before None
            cat_metrics.record_closed_trade(
                confidence=float(entry_conf) if entry_conf is not None else None,
                regime=entry_regime,
                pnl=float(profit),
            )
            position = None
            if not research and loss_limit > 0 and daily_pnl <= -loss_limit:
                halted = True
                daily_loss_halted = True
                bot_stub["status"] = "ERROR"

        def _try_entry(signal: str, signal_data: dict, row: dict, bar_time) -> None:
            nonlocal position, last_signal_bar_time, blocked_entries, equity, total_fees
            if (not research and halted) or signal != "BUY":
                return
            if bar_time is not None and last_signal_bar_time == bar_time:
                return

            chain = ExecutionChain(bar_time)
            chain.record("signal", ok=True, signal="BUY", side="BUY")

            from app.services.altdata.event_policy import check_entry_gates

            gate_ok, gate_reason, gate_kind = check_entry_gates(
                symbol, bar_time, cfg, is_exit=False,
            )
            if not gate_ok and gate_reason:
                blocked_entries += 1
                chain.record("event_gate", ok=False, reason=gate_reason)
                _record_blocked(
                    gate_kind or "event",
                    gate_reason,
                    bar_time,
                    side="BUY",
                    signal="BUY",
                )
                return

            current_price = float(row["close"])
            stop_loss, stop_err = _resolve_entry_stop("BUY", current_price, signal_data)
            if stop_loss is None:
                blocked_entries += 1
                chain.record("size", ok=False, reason=stop_err or "bad stop")
                _record_blocked(
                    "size",
                    stop_err or "Could not resolve stop loss for sizing",
                    bar_time,
                    side="BUY",
                    signal="BUY",
                )
                return

            size_factor = float((signal_data or {}).get("size_factor") or 1.0)
            qty = entry_quantity_from_risk(
                risk_cfg=risk_cfg,
                simulated_equity=equity,
                price=current_price,
                stop_loss=stop_loss,
                size_factor=size_factor,
                apply_vol_sizing=bool(cfg.get("use_vol_sizing", True)),
            )
            if research:
                qty = min(qty, allocation / max(current_price, 1e-9))
            else:
                decision = self._risk_gate.validate_trade(
                    bot_stub,
                    "BUY",
                    qty,
                    current_price,
                    is_exit=False,
                    daily_pnl=daily_pnl,
                    position_size=0.0,
                    at_ts=bar_time,
                    backtest=True,
                )
                if not decision.allowed:
                    blocked_entries += 1
                    chain.record("risk_gate", ok=False, reason=decision.reason or "blocked")
                    _record_blocked(
                        "risk_gate",
                        decision.reason or "Risk gate blocked entry",
                        bar_time,
                        side="BUY",
                        signal="BUY",
                    )
                    return
                qty = decision.quantity if decision.quantity is not None else qty
                chain.record("risk_gate", ok=True, quantity=round(qty, 6))

            # Shared live/backtest sizing overlays (confidence/temp/Kelly/meta/regime).
            if live_parity:
                qty_out = _apply_live_parity_pretrade(
                    side="BUY",
                    signal="BUY",
                    signal_data=signal_data,
                    bar_time=bar_time,
                    qty=qty,
                    chain=chain,
                    row_i=i,
                )
                if qty_out is None:
                    return
                qty = qty_out

            if qty < _MIN_QTY:
                blocked_entries += 1
                chain.record("size", ok=False, reason=f"below {_MIN_QTY}")
                _record_blocked(
                    "size",
                    f"Quantity below minimum ({_MIN_QTY})",
                    bar_time,
                    side="BUY",
                    signal="BUY",
                )
                return
            chain.record("size", ok=True, quantity=round(qty, 6))

            _, tp_price = resolve_take_profit(
                merged_config, signal_data, signal, current_price,
            )
            entry_notional = current_price * qty
            bar_vol = float(row.get("volume") or 0)
            bar_vol_notional = bar_vol * current_price
            if cost_model.volume_participation:
                cost_model.reset_bar(bar_time)
                entry_fill = cost_model.fill_price(
                    current_price, "BUY",
                    order_notional=entry_notional,
                    bar_volume_notional=bar_vol_notional,
                )
                entry_fee = cost_model.compute_fee(entry_fill * qty)
            else:
                entry_fill = entry_fill_price(current_price, "BUY", slippage_bps)
                entry_fee = trade_fee(entry_fill * qty, fee_bps)
            equity -= entry_fee
            total_fees += entry_fee
            position = {
                "side": "BUY",
                "entry_price": current_price,
                "entry_fill": entry_fill,
                "entry_time": bar_time,
                "qty": qty,
                "stop_loss": stop_loss,
                "take_profit": tp_price,
                "high_watermark": current_price,
                "excursion_high": current_price,
                "excursion_low": current_price,
                "entry_fee": entry_fee,
                # 3.3-C: store ATR at entry for Chandelier scaling.
                "entry_atr": float(row.get("ATR_14") or row.get("ATRr_14") or 0),
                "entry_confidence": float((signal_data or {}).get("confidence") or 0) or None,
                "entry_regime": ((signal_data or {}).get("regime")
                                 or ((signal_data or {}).get("insight_snapshot") or {}).get("regime")),
            }
            entry_row = {
                "time": int(bar_time) if bar_time is not None else 0,
                "side": "BUY",
                "price": round(entry_fill, 4),
                "quantity": round(qty, 6),
                "pnl": None,
                "is_exit": False,
                "reason": "ENTRY",
                "fee": round(entry_fee, 4),
            }
            snap = signal_data.get("insight_snapshot")
            if snap:
                entry_row["insight_snapshot"] = snap
            meta_explain = _maybe_meta_label_explain(
                signal_data,
                cfg,
                symbol=symbol,
                timeframe=bt_timeframe,
                bar_time=bar_time,
                side="BUY",
                strat_key=strat_key,
            )
            if meta_explain:
                entry_row["meta_label_explain"] = meta_explain
            chain.record("fill", ok=True, price=round(entry_fill, 4), fee=round(entry_fee, 4))
            entry_row["execution_chain"] = chain.to_list()
            trade_log.append(entry_row)
            last_signal_bar_time = bar_time

        def _record_filter_reject(signal_data: dict | None, bar_time=None) -> None:
            block = chart_filter_reject_block(signal_data, bar_time)
            if not block:
                return
            if block.bucket:
                filter_rejects[block.bucket] = filter_rejects.get(block.bucket, 0) + 1
            _record_blocked(
                block.kind,
                block.reason,
                bar_time,
                signal=block.signal,
                bucket=block.bucket,
            )

        def _try_short_entry(signal: str, signal_data: dict, row: dict, bar_time) -> None:
            nonlocal position, last_signal_bar_time, blocked_entries, equity, total_fees
            if (not research and halted) or signal != "SELL":
                return
            if bar_time is not None and last_signal_bar_time == bar_time:
                return

            chain = ExecutionChain(bar_time)
            chain.record("signal", ok=True, signal="SELL", side="SELL")

            from app.services.altdata.event_policy import check_entry_gates

            gate_ok, gate_reason, gate_kind = check_entry_gates(
                symbol, bar_time, cfg, is_exit=False,
            )
            if not gate_ok and gate_reason:
                blocked_entries += 1
                chain.record("event_gate", ok=False, reason=gate_reason)
                _record_blocked(
                    gate_kind or "event",
                    gate_reason,
                    bar_time,
                    side="SELL",
                    signal="SELL",
                )
                return

            current_price = float(row["close"])
            stop_loss, stop_err = _resolve_entry_stop("SELL", current_price, signal_data)
            if stop_loss is None:
                blocked_entries += 1
                chain.record("size", ok=False, reason=stop_err or "bad stop")
                _record_blocked(
                    "size",
                    stop_err or "Could not resolve stop loss for sizing",
                    bar_time,
                    side="SELL",
                    signal="SELL",
                )
                return

            size_factor = float((signal_data or {}).get("size_factor") or 1.0)
            qty = entry_quantity_from_risk(
                risk_cfg=risk_cfg,
                simulated_equity=equity,
                price=current_price,
                stop_loss=stop_loss,
                size_factor=size_factor,
                apply_vol_sizing=bool(cfg.get("use_vol_sizing", True)),
            )
            if research:
                qty = min(qty, allocation / max(current_price, 1e-9))
            else:
                decision = self._risk_gate.validate_trade(
                    bot_stub,
                    "SELL",
                    qty,
                    current_price,
                    is_exit=False,
                    daily_pnl=daily_pnl,
                    position_size=0.0,
                    at_ts=bar_time,
                    backtest=True,
                )
                if not decision.allowed:
                    blocked_entries += 1
                    chain.record("risk_gate", ok=False, reason=decision.reason or "blocked")
                    _record_blocked(
                        "risk_gate",
                        decision.reason or "Risk gate blocked entry",
                        bar_time,
                        side="SELL",
                        signal="SELL",
                    )
                    return
                qty = decision.quantity if decision.quantity is not None else qty
                chain.record("risk_gate", ok=True, quantity=round(qty, 6))

            if live_parity:
                qty_out = _apply_live_parity_pretrade(
                    side="SELL",
                    signal="SELL",
                    signal_data=signal_data,
                    bar_time=bar_time,
                    qty=qty,
                    chain=chain,
                    row_i=i,
                )
                if qty_out is None:
                    return
                qty = qty_out

            if qty < _MIN_QTY:
                blocked_entries += 1
                chain.record("size", ok=False, reason=f"below {_MIN_QTY}")
                _record_blocked(
                    "size",
                    f"Quantity below minimum ({_MIN_QTY})",
                    bar_time,
                    side="SELL",
                    signal="SELL",
                )
                return
            chain.record("size", ok=True, quantity=round(qty, 6))

            _, tp_price = resolve_take_profit(
                merged_config, signal_data, "SELL", current_price,
            )
            entry_notional = current_price * qty
            bar_vol = float(row.get("volume") or 0)
            bar_vol_notional = bar_vol * current_price
            if cost_model.volume_participation:
                cost_model.reset_bar(bar_time)
                entry_fill = cost_model.fill_price(
                    current_price, "SELL",
                    order_notional=entry_notional,
                    bar_volume_notional=bar_vol_notional,
                )
                entry_fee = cost_model.compute_fee(entry_fill * qty)
            else:
                entry_fill = entry_fill_price(current_price, "SELL", slippage_bps)
                entry_fee = trade_fee(entry_fill * qty, fee_bps)
            equity -= entry_fee
            total_fees += entry_fee
            position = {
                "side": "SELL",
                "entry_price": current_price,
                "entry_fill": entry_fill,
                "entry_time": bar_time,
                "qty": qty,
                "stop_loss": stop_loss,
                "take_profit": tp_price,
                "low_watermark": current_price,
                "excursion_high": current_price,
                "excursion_low": current_price,
                "entry_fee": entry_fee,
                "entry_confidence": float((signal_data or {}).get("confidence") or 0) or None,
                "entry_regime": ((signal_data or {}).get("regime")
                                 or ((signal_data or {}).get("insight_snapshot") or {}).get("regime")),
            }
            trade_log.append({
                "time": int(bar_time) if bar_time is not None else 0,
                "side": "SELL",
                "price": round(entry_fill, 4),
                "quantity": round(qty, 6),
                "pnl": None,
                "is_exit": False,
                "reason": "ENTRY_SHORT",
                "fee": round(entry_fee, 4),
            })
            snap = signal_data.get("insight_snapshot")
            if snap:
                trade_log[-1]["insight_snapshot"] = snap
            meta_explain = _maybe_meta_label_explain(
                signal_data,
                cfg,
                symbol=symbol,
                timeframe=bt_timeframe,
                bar_time=bar_time,
                side="SELL",
                strat_key=strat_key,
            )
            if meta_explain:
                trade_log[-1]["meta_label_explain"] = meta_explain
            chain.record("fill", ok=True, price=round(entry_fill, 4), fee=round(entry_fee, 4))
            trade_log[-1]["execution_chain"] = chain.to_list()
            last_signal_bar_time = bar_time

        eval_bars = max(len(df) - start_i, 1)
        # Prefer frequent UI updates on long 1m ML runs (was eval_bars//40 ≈ 2k bars,
        # so the progress stayed at "bar 0" for tens of minutes and looked stuck).
        progress_stride = max(1, min(200, eval_bars // 100))
        progress_emit = ProgressThrottle(
            progress_cb,
            total=eval_bars,
            stride=progress_stride,
            min_interval_sec=2.0,
        )

        # Batch-precompute ML signals when enabled, then keep the bar loop for
        # exits / cancel / progress / live_parity gates. ONNX CUDA sessions are
        # only used when sim_mode is research/research_fast (see strategies).
        precomputed_signals = None
        if _chart_agent_signal is None:
            try:
                from app.services.bots.ml_batch_inference import (
                    try_precompute_signals_from_df,
                )

                def _precompute_progress(done: int, total: int) -> None:
                    """Map feature/batch phase onto the first ~10% of bar progress."""
                    if not progress_cb:
                        return
                    tot = max(1, int(total or 1))
                    frac = min(1.0, max(0.0, float(done) / tot))
                    # Use the throttled emitter so wall-clock heartbeats still fire
                    # when vectorized precompute reports the same quantized bar.
                    mapped = max(0, int(frac * eval_bars * 0.10))
                    progress_emit(mapped, eval_bars)

                if progress_cb:
                    progress_emit(0, eval_bars)
                # Prefer DF path (columnar features / fewer dict materializations).
                precomputed_signals = try_precompute_signals_from_df(
                    strategy,
                    df,
                    start_i,
                    symbol=sym_u,
                    strategy_key=strat_key,
                    config=cfg,
                    cancel_cb=cancel_cb,
                    progress_cb=_precompute_progress if progress_cb else None,
                )
            except InterruptedError:
                _release_chart_agent_cache()
                return {"error": "Backtest cancelled", "cancelled": True}

        # research_fast + precomputed signals: columnar OHLC avoids full to_dict()
        # per bar (major Python overhead on long 1m runs).
        _col_fast = None
        if research_fast and precomputed_signals is not None:
            try:
                import numpy as _np

                def _col(name, default=0.0):
                    if name not in df.columns:
                        return _np.full(len(df), default, dtype=_np.float64)
                    return _np.asarray(df[name].to_numpy(), dtype=_np.float64)

                _col_fast = {
                    "open": _col("open"),
                    "high": _col("high"),
                    "low": _col("low"),
                    "close": _col("close"),
                    # Required for volume-participation cost model fills.
                    "volume": _col("volume"),
                    "atr": _col("ATR_14"),
                    "atr_alt": _col("ATRr_14"),
                    "time": df["time"].to_numpy() if "time" in df.columns else None,
                }
            except Exception:
                _col_fast = None

        for i in range(start_i, len(df)):
            if cancel_cb and cancel_cb():
                _release_chart_agent_cache()
                return {"error": "Backtest cancelled", "cancelled": True}

            if _col_fast is not None:
                bar_close = float(_col_fast["close"][i] or 0)
                bar_low = float(_col_fast["low"][i] or bar_close)
                bar_high = float(_col_fast["high"][i] or bar_close)
                bar_open = float(_col_fast["open"][i] or bar_close)
                bar_vol = float(_col_fast["volume"][i] or 0)
                bar_atr = float(_col_fast["atr"][i] or 0) or float(_col_fast["atr_alt"][i] or 0)
                bar_time = _col_fast["time"][i] if _col_fast["time"] is not None else None
                row = {
                    "time": bar_time,
                    "open": bar_open,
                    "high": bar_high,
                    "low": bar_low,
                    "close": bar_close,
                    "volume": bar_vol,
                    "ATR_14": bar_atr,
                }
            else:
                row = df.iloc[i].to_dict()
            if sym_u:
                row["_symbol"] = sym_u
            bar_time = row.get("time")
            bar_low = float(row.get("low") or row.get("close") or 0)
            bar_high = float(row.get("high") or row.get("close") or 0)
            bar_close = float(row.get("close") or 0)
            _roll_daily(bar_time)

            if position:
                bars_in_market += 1
                entry_px = float(position.get("entry_fill") or position.get("entry_price") or bar_close)
                position["excursion_high"] = max(position.get("excursion_high", entry_px), bar_high)
                position["excursion_low"] = min(position.get("excursion_low", entry_px), bar_low)
                if position["side"] == "BUY":
                    # 3.3-C: Use Chandelier ATR stop when enabled; fall back to pct trailing.
                    if use_chandelier:
                        bar_atr = float(row.get("ATR_14") or row.get("ATRr_14") or position.get("entry_atr") or 0)
                        _update_chandelier_stop(position, bar_high, bar_atr, chandelier_mult)
                    else:
                        _update_trailing_stop(position, bar_low, bar_high, trailing_pct)
                    trigger, exit_px = _check_long_sl_tp(position, bar_low, bar_high, randomize_sl_tp=randomize_sl_tp)
                else:
                    if use_chandelier:
                        bar_atr = float(row.get("ATR_14") or row.get("ATRr_14") or position.get("entry_atr") or 0)
                        _update_chandelier_stop_short(position, bar_low, bar_atr, chandelier_mult)
                    else:
                        _update_trailing_stop_short(position, bar_low, bar_high, trailing_pct)
                    trigger, exit_px = _check_short_sl_tp(position, bar_low, bar_high, randomize_sl_tp=randomize_sl_tp)
                if trigger:
                    _close_position(bar_time, exit_px, trigger)

            # Time stop (bars) — parity with live BotManager.
            if position and live_parity:
                time_stop_bars = int(cfg.get("time_stop_bars") or 0)
                if time_stop_bars > 0:
                    try:
                        from app.services.bots.position_duration import bars_held_since_open

                        held = bars_held_since_open(
                            position.get("entry_time"),
                            bar_time,
                            bt_timeframe,
                        )
                        if held is not None and held >= time_stop_bars:
                            _close_position(bar_time, bar_close, "TIME_STOP")
                    except Exception:
                        pass

            signal_data = None
            if _chart_agent_signal is not None:
                signal_data = _chart_agent_signal(i)
            elif precomputed_signals is not None:
                signal_data = precomputed_signals[i - start_i]
            else:
                row["_current_side"] = position["side"] if position else "NONE"
                signal_data = strategy.evaluate(row)
            signal = (signal_data or {}).get("signal")
            bars_evaluated += 1
            sig_key = str(signal or "NONE").upper()
            if sig_key not in signal_counts:
                signal_counts[sig_key] = 0
            signal_counts[sig_key] += 1
            rr = (signal_data or {}).get("reject_reason")
            if rr:
                reject_reason_counts[str(rr)[:160]] += 1
                if str(rr).startswith("evaluate error"):
                    evaluate_errors += 1

            # research_fast columnar `row` is OHLCV+ATR only. Gates / filters that
            # read RSI/MACD/EMA/etc. need a full indicator row — hydrate on demand
            # (entry bars only) so the hot path stays slim.
            gate_row = row
            needs_full_gate_row = (
                _col_fast is not None
                and signal in ("BUY", "SELL")
                and (
                    (live_parity and strat_filter is not None)
                    or bool(cfg.get("vae_regime_gate_enabled"))
                )
            )
            if needs_full_gate_row:
                try:
                    gate_row = df.iloc[i].to_dict()
                    if sym_u:
                        gate_row["_symbol"] = sym_u
                except Exception:
                    gate_row = row

            confirm_tf = str(cfg.get("confirm_timeframe") or "").strip()
            parity_out = apply_indicator_parity_gates(
                signal,
                row=gate_row,
                bar_time=bar_time,
                live_parity=live_parity,
                strat_key=strat_key,
                confirm_tf=confirm_tf,
                htf_bias_lookup=htf_bias_lookup,
                strat_filter=strat_filter,
            )
            if parity_out.block:
                if parity_out.block.kind.startswith("parity"):
                    parity_gate_blocks += 1
                    blocked_entries += 1
                _record_blocked(
                    parity_out.block.kind,
                    parity_out.block.reason,
                    bar_time,
                    side=parity_out.block.side,
                    signal=parity_out.block.signal,
                    bucket=parity_out.block.bucket,
                )
            signal = parity_out.signal

            # VAE meta-layer gate — new entries only (never block exits/reversals).
            if signal in ("BUY", "SELL") and not position:
                lookback = []
                try:
                    # Recent prepared rows for feature context (best-effort).
                    start = max(0, i - 24)
                    for j in range(start, i):
                        lookback.append(dict(df.iloc[j]))
                except Exception:
                    lookback = []
                vae_out = apply_vae_regime_meta_gate(
                    signal,
                    row=gate_row,
                    symbol=str(cfg.get("symbol") or gate_row.get("_symbol") or ""),
                    bot_config=cfg,
                    lookback_rows=lookback,
                )
                if vae_out.block:
                    blocked_entries += 1
                    _record_blocked(
                        vae_out.block.kind,
                        vae_out.block.reason,
                        bar_time,
                        signal=vae_out.block.signal,
                    )
                    signal = None
                else:
                    signal = vae_out.signal

            # Shared conformal + HMM gates — new entries only (parity with live).
            if signal in ("BUY", "SELL") and not position:
                hmm_feats = None
                try:
                    from app.services.bots.hmm_regime import recent_regime_features

                    lookback_n = int(cfg.get("hmm_regime_vol_lookback") or 20)
                    # Prefer raw candle closes when available; fall back to df slice.
                    start = max(0, i - lookback_n - 5)
                    candle_slice = [
                        {
                            "close": float(df.iloc[j].get("close") or 0),
                        }
                        for j in range(start, i + 1)
                    ]
                    hmm_feats = recent_regime_features(
                        candle_slice, vol_lookback=lookback_n,
                    )
                except Exception:
                    hmm_feats = None
                gate_cfg = dict(cfg)
                gate_cfg.setdefault(
                    "_bot_id",
                    cfg.get("_bot_id") or cfg.get("backtest_bot_id") or "backtest",
                )
                shared_out = apply_shared_signal_gates(
                    signal,
                    signal_data,
                    bot_config=gate_cfg,
                    recent_features=hmm_feats,
                )
                if shared_out.block:
                    blocked_entries += 1
                    _record_blocked(
                        shared_out.block.kind,
                        shared_out.block.reason,
                        bar_time,
                        signal=shared_out.block.signal,
                    )
                    signal = None
                    if shared_out.signal_data is not None:
                        signal_data = shared_out.signal_data
                else:
                    signal = shared_out.signal
                    if shared_out.signal_data is not None:
                        signal_data = shared_out.signal_data
                # LLM debate is live-only (async LLM). When enabled, record skip
                # so operators know backtest did not apply advisory vetoes.
                if signal in ("BUY", "SELL") and cfg.get("llm_debate_enabled"):
                    if isinstance(signal_data, dict):
                        signal_data = dict(signal_data)
                        signal_data["llm_debate_skipped"] = "backtest_no_llm"

            if strat_key == "CHART_AGENT" and signal not in ("BUY", "SELL", "CLOSE"):
                _record_filter_reject(signal_data, bar_time)

            if position:
                close_signal = (
                    (position["side"] == "BUY" and signal in ("SELL", "CLOSE"))
                    or (position["side"] == "SELL" and signal in ("BUY", "CLOSE"))
                )
                if close_signal:
                    if bar_time is not None and last_signal_bar_time == bar_time:
                        signal = None
                    else:
                        _close_position(bar_time, bar_close, "SIGNAL")
                        last_signal_bar_time = bar_time
                        signal = None

            executed_this_bar = False
            if not position and signal == "BUY":
                direction_mode = str(cfg.get("direction_mode", "LONG_ONLY")).upper()
                if research or direction_mode in ("BOTH", "LONG_ONLY"):
                    before = position
                    _try_entry(signal, signal_data, row, bar_time)
                    executed_this_bar = position is not None and position is not before
                else:
                    blocked_entries += 1
                    _record_blocked(
                        "direction_mode",
                        f"{direction_mode} blocks BUY entries",
                        bar_time,
                        side="BUY",
                        signal="BUY",
                    )
            elif not position and signal == "SELL":
                direction_mode = str(cfg.get("direction_mode", "LONG_ONLY")).upper()
                if research or direction_mode in ("BOTH", "SHORT_ONLY"):
                    before = position
                    _try_short_entry(signal, signal_data, row, bar_time)
                    executed_this_bar = position is not None and position is not before
                else:
                    blocked_entries += 1
                    _record_blocked(
                        "direction_mode",
                        f"{direction_mode} blocks SELL/short entries",
                        bar_time,
                        side="SELL",
                        signal="SELL",
                    )

            if cat_metrics.collect_ml or cat_metrics.collect_rl or cat_metrics.collect_agent:
                future_close = None
                if i + 1 < len(df):
                    try:
                        future_close = float(df.iloc[i + 1].get("close") or 0)
                    except Exception:
                        future_close = None
                filter_bucket = None
                if (signal_data or {}).get("reject_reason"):
                    block = chart_filter_reject_block(signal_data, bar_time)
                    filter_bucket = block.bucket if block else "other"
                cat_metrics.record_bar(
                    bar_index=i,
                    signal_data=signal_data,
                    signal=signal,
                    close=bar_close,
                    future_close=future_close,
                    position_side=position["side"] if position else None,
                    executed=executed_this_bar,
                    filter_bucket=filter_bucket,
                    regime=(signal_data or {}).get("regime"),
                )

            peak_equity = max(peak_equity, equity)
            drawdown = (peak_equity - equity) / peak_equity * 100 if peak_equity else 0
            max_drawdown = max(max_drawdown, drawdown)

            if bar_time is not None and (i % sample_stride == 0 or i == len(df) - 1):
                equity_curve.append({"time": int(bar_time), "equity": round(equity, 2)})

            if progress_cb:
                progress_emit(i - start_i, eval_bars)

        if progress_cb:
            progress_emit(eval_bars, eval_bars)

        if position:
            last_row = df.iloc[-1].to_dict()
            last_bar_time = last_row.get("time")
            last_close = float(last_row.get("close") or 0)
            _close_position(last_bar_time, last_close, "END_OF_DATA")

        closed = [t for t in trade_log if t.get("is_exit")]
        winning_trades = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
        total_trades = len(closed)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        total_pnl = round(equity - starting_equity, 2)
        last_bar_ts = None
        if equity_curve:
            try:
                last_bar_ts = float(equity_curve[-1].get("time"))
            except (TypeError, ValueError, IndexError):
                last_bar_ts = None
        if last_bar_ts is None and trade_log:
            try:
                last_bar_ts = float(trade_log[-1].get("time"))
            except (TypeError, ValueError):
                last_bar_ts = None
        from app.services.bots.pretrade_context import is_cool_until_active

        streak_cooldown_active = is_cool_until_active(
            bot_stub.get("_pretrade_streak_cool_until"),
            now=last_bar_ts,
        )
        # Still latched if cool-down active or lookback streak would still VETO.
        if live_parity and closed and last_bar_ts is not None:
            exit_pnls, exit_times = _closed_exit_series()
            final_pt = evaluate_parity_pretrade(
                side="BUY",
                symbol=symbol,
                bar_time=last_bar_ts,
                bot_config=cfg,
                recent_exit_pnls=exit_pnls,
                recent_exit_times=exit_times,
                anomaly=None,
                sentiment=None,
            )
            if final_pt.get("streak_veto"):
                streak_veto_latched = True
            elif streak_veto_latched and not streak_cooldown_active:
                streak_veto_latched = False
                streak_cooldown_resumed = True
        summary = _compute_summary(
            closed,
            total_pnl=total_pnl,
            win_rate=win_rate,
            max_drawdown=max_drawdown,
            trade_count=total_trades,
            starting_equity=starting_equity,
            equity_curve=equity_curve,
            bars_in_market=bars_in_market,
            eval_bars=eval_bars,
            blocked_entries=blocked_entries,
            filter_rejects=filter_rejects,
            blocked_events=blocked_events,
            blocked_events_total=blocked_events_total[0],
            total_fees=total_fees,
            slippage_bps=slippage_bps,
            fee_bps=fee_bps,
            halted=halted,
            daily_loss_halted=daily_loss_halted,
            streak_veto_latched=streak_veto_latched,
            streak_cooldown_active=streak_cooldown_active,
            streak_cooldown_resumed=streak_cooldown_resumed,
            streak_veto_blocks=streak_veto_blocks,
        )
        summary["live_parity"] = live_parity
        summary["parity_gate_blocks"] = parity_gate_blocks
        if strat_key == "CHART_AGENT":
            summary["filter_rejects_total"] = sum(filter_rejects.values())
        summary = enrich_summary(
            summary,
            equity_curve=equity_curve,
            candles=candles,
            starting_equity=starting_equity,
            feed=getattr(self.screener, "feed", None),
            symbol=symbol,
        )
        if summary.get("max_drawdown") and float(summary["max_drawdown"]) > 0:
            ret = float(summary.get("return_pct") or 0)
            summary["calmar_ratio"] = round(ret / float(summary["max_drawdown"]), 3)
        dd_curve = drawdown_curve(equity_curve)

        from app.services.bots.monte_carlo import monte_carlo_trade_bands

        monte_carlo = monte_carlo_trade_bands(
            trade_log,
            starting_equity=starting_equity,
        )

        result = {
            "win_rate": summary["win_rate"],
            "total_pnl": summary["total_pnl"],
            "max_drawdown": summary["max_drawdown"],
            "trade_count": total_trades,
            "equity_curve": equity_curve,
            "drawdown_curve": dd_curve,
            "starting_equity": round(starting_equity, 2),
            "allocation": round(allocation, 2),
            "risk_base_mode": risk_cfg["mode"],
            "risk_base": round(risk_cfg["snapshot"], 2),
            "trades": trade_log,
            "trades_total": len(trade_log),
            "summary": summary,
            "sim_mode": sim_mode,
            "live_parity": live_parity,
            "regime": summary.get("regime"),
            "benchmark_overlays": summary.get("benchmark_overlays"),
            "costs": {
                **cost_model.to_dict(),
                "total_fees": round(total_fees, 2),
            },
            "monte_carlo": monte_carlo,
            "execution_runtime": "strategy_runtime/v1",
        }

        cat_payload = cat_metrics.finalize(
            summary=summary,
            filter_rejects=filter_rejects,
            feature_importance=load_ml_feature_importance(strat_key, symbol),
            oos_summary=cfg.get("_oos_summary") if isinstance(cfg.get("_oos_summary"), dict) else None,
            equity_curve=equity_curve,
        )
        result.update(cat_payload)

        from app.services.bots.strategy_readiness import build_strategy_readiness

        result["strategy_readiness"] = build_strategy_readiness(
            strat_key,
            trade_count=total_trades,
            bars_evaluated=bars_evaluated,
            signal_counts=signal_counts,
            reject_reasons=reject_reason_counts,
            evaluate_errors=evaluate_errors,
            blocked_entries=blocked_entries,
            blocked_events=blocked_events,
            direction_mode=str(cfg.get("direction_mode") or "LONG_ONLY"),
            sim_mode=sim_mode,
        )

        score_from = cfg.get("score_from_time")
        if score_from is not None:
            from app.services.bots.backtest_selection_bias import apply_score_window

            result = apply_score_window(result, int(score_from))

        _release_chart_agent_cache()
        return result


def thread_local_backtest_runner(backtester: BacktesterService):
    """Return a run_backtest callable with one BacktesterService per worker thread."""
    import threading

    local = threading.local()
    screener = backtester.screener

    def run_backtest(
        symbol: str,
        strategy_name: str,
        config: dict,
        candles: list,
        *,
        progress_cb=None,
        cancel_cb=None,
    ) -> dict:
        bt = getattr(local, "instance", None)
        if bt is None:
            bt = BacktesterService(screener)
            local.instance = bt
        return bt.run_backtest(
            symbol, strategy_name, config, candles,
            progress_cb=progress_cb, cancel_cb=cancel_cb,
        )

    return run_backtest
