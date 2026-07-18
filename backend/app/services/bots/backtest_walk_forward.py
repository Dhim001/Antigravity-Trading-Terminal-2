"""Walk-forward optimization — train sweep on in-sample, validate on out-of-sample."""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from app.services.bots.backtest_perf import parallel_worker_count
from app.services.bots.backtest_selection_bias import (
    MIN_TRADES_PER_PARAM,
    WF_MIN_TRADES_PER_PARAM,
    build_oos_candles_with_warmup,
    effective_min_trades,
    selection_bias_summary,
)
from app.services.bots.backtest_sweep import count_sweep_axes, count_varying_param_axes, sweep_label

def _downsample_curve(curve: list[dict] | None, max_points: int = 120) -> list[dict]:
    if not curve or len(curve) <= max_points:
        return list(curve or [])
    step = max(1, (len(curve) + max_points - 1) // max_points)
    out = [curve[i] for i in range(0, len(curve), step)]
    if out[-1].get("time") != curve[-1].get("time"):
        out.append(curve[-1])
    return out


def stitch_oos_equity_curves(
    folds: list[dict],
    *,
    starting_equity: float,
) -> list[dict]:
    """Chain OOS equity curves across walk-forward folds."""
    stitched: list[dict] = []
    cumulative_pnl = 0.0
    base = float(starting_equity) if starting_equity > 0 else 10_000.0
    for fold in folds:
        oos_curve = (fold.get("out_of_sample") or {}).get("equity_curve") or []
        if not oos_curve:
            continue
        oos_start = float(oos_curve[0].get("equity") or base)
        for pt in oos_curve:
            oos_eq = float(pt.get("equity") or oos_start)
            fold_delta = oos_eq - oos_start
            stitched.append({
                "time": pt.get("time"),
                "equity": round(base + cumulative_pnl + fold_delta, 2),
                "fold": fold.get("fold"),
            })
        if oos_curve:
            oos_end = float(oos_curve[-1].get("equity") or oos_start)
            cumulative_pnl += oos_end - oos_start
    return stitched


def _oos_snapshot(oos: dict) -> dict:
    return {
        "summary": oos.get("summary") or {},
        "total_pnl": oos.get("total_pnl"),
        "trade_count": oos.get("trade_count"),
        "meta": oos.get("meta") or {},
        "equity_curve": _downsample_curve(oos.get("equity_curve")),
    }


def split_train_test(
    candles: list[dict],
    meta: dict,
    train_pct: float = 70.0,
    *,
    wf_options: dict | None = None,
) -> tuple[list[dict], list[dict], dict, dict]:
    """Split candles into train (in-sample) and test (out-of-sample) windows."""
    if not candles:
        return [], [], dict(meta or {}), dict(meta or {})

    opts = wf_options or {}
    pct = max(50.0, min(90.0, float(train_pct)))
    split = int(len(candles) * pct / 100.0)
    split = max(50, min(split, len(candles) - 50))
    train = candles[:split]
    test = candles[split:]

    purge_meta: dict[str, Any] = {}
    if opts.get("purged_splits") and opts.get("purge_bars", 0) > 0:
        from app.services.bots.backtest_purged_cv import purge_train_before_test
        train, purge_meta = purge_train_before_test(train, test, int(opts["purge_bars"]))

    train_meta = copy.deepcopy(meta or {})
    test_meta = copy.deepcopy(meta or {})
    if train:
        train_meta["newest"] = train[-1].get("time", train_meta.get("newest"))
        train_meta["count"] = len(train)
        train_meta["window"] = "in_sample"
        if purge_meta:
            train_meta["purge"] = purge_meta
    if test:
        test_meta["oldest"] = test[0].get("time", test_meta.get("oldest"))
        test_meta["count"] = len(test)
        test_meta["window"] = "out_of_sample"
    return train, test, train_meta, test_meta


def build_rolling_fold_windows(
    candles: list[dict],
    meta: dict,
    rolling_folds: int = 1,
    train_pct: float = 70.0,
    *,
    wf_options: dict | None = None,
) -> list[tuple[list[dict], list[dict], dict, dict]]:
    """
    Build sequential IS/OOS windows for walk-forward (rolling / fixed slices).

    rolling_folds=1: single 70/30 (or train_pct) split over the full range.
    rolling_folds>1: divide the range into N equal slices; each fold IS/OOS-splits its slice.
    """
    opts = wf_options or {}
    n_folds = max(1, int(rolling_folds or 1))
    if not candles:
        return []

    if n_folds <= 1:
        train, test, train_meta, test_meta = split_train_test(
            candles, meta, train_pct, wf_options=opts,
        )
        if len(train) < 50 or len(test) < 50:
            return []
        return [(train, test, train_meta, test_meta)]

    fold_size = len(candles) // n_folds
    if fold_size < 100:
        return []

    windows: list[tuple[list[dict], list[dict], dict, dict]] = []
    embargo_pct = float(opts.get("embargo_pct") or 0)
    prev_test_end = 0

    for i in range(n_folds):
        start = i * fold_size
        end = len(candles) if i == n_folds - 1 else (i + 1) * fold_size
        if opts.get("purged_splits") and embargo_pct > 0 and i > 0:
            from app.services.bots.backtest_purged_cv import apply_embargo_after_test, embargo_bars_for_segment
            emb = embargo_bars_for_segment(end - start, embargo_pct)
            start = apply_embargo_after_test(candles, prev_test_end, emb)
        window = candles[start:end]
        if len(window) < 100:
            continue
        wmeta = copy.deepcopy(meta or {})
        if window:
            wmeta["oldest"] = window[0].get("time", wmeta.get("oldest"))
            wmeta["newest"] = window[-1].get("time", wmeta.get("newest"))
            wmeta["count"] = len(window)
            wmeta["fold"] = i + 1
        train, test, train_meta, test_meta = split_train_test(window, wmeta, train_pct, wf_options=opts)
        if len(train) < 50 or len(test) < 50:
            continue
        train_meta["fold"] = i + 1
        test_meta["fold"] = i + 1
        windows.append((train, test, train_meta, test_meta))
        prev_test_end = end
    return windows


def build_anchored_fold_windows(
    candles: list[dict],
    meta: dict,
    *,
    train_pct: float = 70.0,
    wf_step_pct: float = 25.0,
    max_folds: int = 5,
    wf_options: dict | None = None,
) -> list[tuple[list[dict], list[dict], dict, dict]]:
    """Expanding in-sample window anchored at series start; OOS slides forward."""
    opts = wf_options or {}
    if not candles or len(candles) < 150:
        return []

    n = len(candles)
    train_end = max(50, int(n * train_pct / 100.0))
    train_end = min(train_end, n - 50)
    step = max(1, int(train_end * float(wf_step_pct) / 100.0))
    min_test = 50

    windows: list[tuple[list[dict], list[dict], dict, dict]] = []
    cursor = train_end
    fold = 0

    while fold < max(1, int(max_folds or 1)):
        test_size = max(min_test, int(cursor * (100.0 - train_pct) / train_pct))
        test_end = min(n, cursor + test_size)
        if test_end - cursor < min_test:
            break

        train = list(candles[:cursor])
        test = list(candles[cursor:test_end])
        wmeta = copy.deepcopy(meta or {})
        wmeta["fold"] = fold + 1
        wmeta["wf_mode"] = "anchored"

        purge_meta: dict[str, Any] = {}
        if opts.get("purged_splits") and opts.get("purge_bars", 0) > 0:
            from app.services.bots.backtest_purged_cv import purge_train_before_test
            train, purge_meta = purge_train_before_test(train, test, int(opts["purge_bars"]))

        train_meta = copy.deepcopy(wmeta)
        test_meta = copy.deepcopy(wmeta)
        train_meta["window"] = "in_sample"
        train_meta["count"] = len(train)
        test_meta["window"] = "out_of_sample"
        test_meta["count"] = len(test)
        if purge_meta:
            train_meta["purge"] = purge_meta
        if train:
            train_meta["newest"] = train[-1].get("time", train_meta.get("newest"))
        if test:
            test_meta["oldest"] = test[0].get("time", test_meta.get("oldest"))

        if len(train) < 50 or len(test) < 50:
            break
        windows.append((train, test, train_meta, test_meta))

        cursor += step
        fold += 1
        if cursor + min_test > n:
            break

    return windows


def build_fold_windows(
    candles: list[dict],
    meta: dict,
    *,
    rolling_folds: int = 1,
    train_pct: float = 70.0,
    wf_options: dict | None = None,
) -> list[tuple[list[dict], list[dict], dict, dict]]:
    """Dispatch rolling vs anchored walk-forward window builders."""
    opts = wf_options or {}
    mode = str(opts.get("wf_mode") or "rolling").lower()
    if mode == "anchored" and int(rolling_folds or 1) > 1:
        return build_anchored_fold_windows(
            candles,
            meta,
            train_pct=train_pct,
            wf_step_pct=float(opts.get("wf_step_pct") or 25.0),
            max_folds=int(rolling_folds or 1),
            wf_options=opts,
        )
    return build_rolling_fold_windows(
        candles,
        meta,
        rolling_folds,
        train_pct,
        wf_options=opts,
    )


VALID_SWEEP_OBJECTIVES = (
    "total_pnl",
    "sharpe_ratio",
    "profit_factor",
    "sortino_ratio",
    "calmar_ratio",
    "max_drawdown_penalty",
    "expectancy",
    "win_rate",
    "max_consecutive_losses",
    "robust_score",
    "stress_pnl",
    # ML / category metrics (from row["ml_metrics"])
    "auc_roc",
    "log_loss",
    "alpha_decay_half_life",
    "oos_is_ratio",
)


def slim_ml_metrics_for_sweep(ml: dict | None) -> dict | None:
    """Keep only fields needed for ML sweep scoring / leaderboard (compact rows)."""
    if not isinstance(ml, dict):
        return None
    out: dict = {}
    for key in ("accuracy", "auc_roc", "log_loss"):
        if ml.get(key) is not None:
            out[key] = ml[key]
    ad = ml.get("alpha_decay")
    if isinstance(ad, dict) and ad.get("half_life_days") is not None:
        out["alpha_decay"] = {"half_life_days": ad.get("half_life_days")}
    is_oos = ml.get("is_vs_oos")
    if isinstance(is_oos, dict):
        slim_oos = {
            k: is_oos.get(k)
            for k in ("is_sharpe", "oos_sharpe", "is_pnl", "oos_pnl")
            if is_oos.get(k) is not None
        }
        if slim_oos:
            out["is_vs_oos"] = slim_oos
    return out or None


def _ml_oos_is_ratio(ml: dict | None, summary: dict | None) -> float | None:
    ml = ml or {}
    summary = summary or {}
    is_oos = ml.get("is_vs_oos") if isinstance(ml.get("is_vs_oos"), dict) else {}
    is_s = is_oos.get("is_sharpe")
    oos_s = is_oos.get("oos_sharpe")
    try:
        if is_s is not None and oos_s is not None and abs(float(is_s)) > 1e-9:
            return float(oos_s) / float(is_s)
    except (TypeError, ValueError):
        pass
    # Prefer PnL ratio when Sharpe missing
    is_pnl = is_oos.get("is_pnl")
    oos_pnl = is_oos.get("oos_pnl")
    try:
        if is_pnl is not None and oos_pnl is not None and abs(float(is_pnl)) > 1e-9:
            return float(oos_pnl) / float(is_pnl)
    except (TypeError, ValueError):
        pass
    wfe = summary.get("walk_forward_efficiency")
    try:
        return float(wfe) if wfe is not None else None
    except (TypeError, ValueError):
        return None


def row_trade_count(row: dict) -> int:
    return int(row.get("trade_count") or (row.get("summary") or {}).get("total_trades") or 0)


def _row_trade_count(row: dict) -> int:
    return row_trade_count(row)


def row_objective_value(row: dict, objective: str = "total_pnl") -> float:
    """Extract ranking metric from a sweep row."""
    summary = row.get("summary") or {}
    ml = row.get("ml_metrics") if isinstance(row.get("ml_metrics"), dict) else {}
    obj = objective if objective in VALID_SWEEP_OBJECTIVES else "total_pnl"
    if obj == "total_pnl":
        return float(row.get("total_pnl") or summary.get("total_pnl") or -1e18)
    if obj == "sharpe_ratio":
        val = summary.get("sharpe_ratio")
        return float(val) if val is not None else -1e18
    if obj == "profit_factor":
        val = summary.get("profit_factor")
        return float(val) if val is not None else -1e18
    if obj == "sortino_ratio":
        val = summary.get("sortino_ratio")
        return float(val) if val is not None else -1e18
    if obj == "calmar_ratio":
        val = summary.get("calmar_ratio")
        if val is not None:
            return float(val)
        ret = summary.get("return_pct")
        dd = summary.get("max_drawdown")
        if ret is not None and dd is not None and float(dd) > 0:
            return float(ret) / float(dd)
        return -1e18
    if obj == "max_drawdown_penalty":
        pnl = float(row.get("total_pnl") or summary.get("total_pnl") or 0)
        dd = float(summary.get("max_drawdown") or 0)
        return pnl - dd * 10.0
    if obj == "expectancy":
        val = summary.get("expectancy")
        return float(val) if val is not None else -1e18
    if obj == "win_rate":
        val = summary.get("win_rate")
        return float(val) if val is not None else -1e18
    if obj == "max_consecutive_losses":
        val = summary.get("max_consecutive_losses")
        if val is None:
            return -1e18
        return -float(val)
    if obj == "robust_score":
        from app.services.bots.backtest_multi_objective import robust_score
        return robust_score(row)
    if obj == "stress_pnl":
        from app.services.bots.backtest_multi_objective import stress_pnl_value
        return stress_pnl_value(row)
    if obj == "auc_roc":
        val = ml.get("auc_roc")
        return float(val) if val is not None else -1e18
    if obj == "log_loss":
        # Optuna / sort always maximize — return negative log-loss
        val = ml.get("log_loss")
        return -float(val) if val is not None else -1e18
    if obj == "alpha_decay_half_life":
        ad = ml.get("alpha_decay") if isinstance(ml.get("alpha_decay"), dict) else {}
        val = ad.get("half_life_days")
        return float(val) if val is not None else -1e18
    if obj == "oos_is_ratio":
        ratio = _ml_oos_is_ratio(ml, summary)
        return float(ratio) if ratio is not None else -1e18
    return float(row.get("total_pnl") or -1e18)


def sort_sweep_rows(
    sweep_rows: list[dict],
    *,
    objective: str = "total_pnl",
    min_trades: int = 0,
) -> list[dict]:
    """Filter by min_trades and sort descending by objective."""
    min_trades = max(0, int(min_trades or 0))
    eligible = [
        r for r in sweep_rows
        if not r.get("error") and _row_trade_count(r) >= min_trades
    ]
    return sorted(
        eligible,
        key=lambda r: row_objective_value(r, objective),
        reverse=True,
    )


def pick_best_config(
    sweep_rows: list[dict],
    *,
    objective: str = "total_pnl",
    min_trades: int = 0,
) -> tuple[dict | None, dict | None]:
    """Return (best_config, best_row) ranked by the selected objective."""
    ranked = sort_sweep_rows(sweep_rows, objective=objective, min_trades=min_trades)
    if not ranked:
        return None, None
    best_row = ranked[0]
    return best_row.get("config"), best_row


def _result_to_row(cfg: dict, res: dict, *, window: str = "in_sample") -> dict:
    if res.get("error"):
        return {"label": sweep_label(cfg), "config": cfg, "error": res["error"]}
    row = {
        "label": sweep_label(cfg),
        "config": cfg,
        "summary": res.get("summary") or {},
        "total_pnl": res.get("total_pnl"),
        "trade_count": res.get("trade_count"),
        "window": window,
    }
    slim = slim_ml_metrics_for_sweep(res.get("ml_metrics"))
    if slim:
        row["ml_metrics"] = slim
    return row


def _metric_from_backtest(res: dict, objective: str) -> float:
    return row_objective_value(
        {
            "total_pnl": res.get("total_pnl"),
            "summary": res.get("summary") or {},
            "trade_count": res.get("trade_count"),
        },
        objective,
    )


def aggregate_regime_oos(fold_entries: list[dict]) -> dict[str, Any]:
    """Regime-aware WF — bucket OOS PnL by dominant vol regime per fold."""
    by_regime: dict[str, list[float]] = {}
    regimes_seen: list[str] = []
    for entry in fold_entries:
        regime = entry.get("oos_regime") or {}
        label = str(regime.get("dominant_regime") or "unknown")
        regimes_seen.append(label)
        oos = entry.get("out_of_sample") or {}
        pnl = oos.get("total_pnl")
        if pnl is None:
            continue
        by_regime.setdefault(label, []).append(float(pnl))

    per_regime: dict[str, dict[str, Any]] = {}
    for label, pnls in by_regime.items():
        positive = sum(1 for p in pnls if p > 0)
        per_regime[label] = {
            "folds": len(pnls),
            "mean_pnl": round(sum(pnls) / len(pnls), 2) if pnls else None,
            "positive_folds": positive,
            "win_rate": round(positive / len(pnls), 3) if pnls else 0.0,
        }

    unique = sorted(set(regimes_seen))
    profitable_regimes = [
        r for r, stats in per_regime.items()
        if stats.get("mean_pnl") is not None and stats["mean_pnl"] > 0
    ]
    single_regime_only = len(profitable_regimes) <= 1 and len(per_regime) <= 1
    stable = len(profitable_regimes) >= 2 or (
        len(per_regime) == 1 and (per_regime.get(unique[0], {}).get("win_rate") or 0) >= 0.6
    )

    return {
        "per_regime": per_regime,
        "regimes_seen": unique,
        "profitable_regimes": profitable_regimes,
        "regime_stable": stable,
        "single_regime_risk": single_regime_only and len(fold_entries) >= 2,
        "note": (
            "Strategy OOS PnL concentrated in one vol regime — may not generalize."
            if single_regime_only and len(fold_entries) >= 2
            else None
        ),
    }


def aggregate_fold_oos(
    fold_entries: list[dict],
    *,
    objective: str = "total_pnl",
    num_trials: int = 1,
) -> dict[str, Any]:
    """Aggregate OOS metrics across walk-forward folds."""
    oos_pnls: list[float] = []
    oos_sharpes: list[float] = []
    is_objectives: list[float] = []
    oos_objectives: list[float] = []
    positive = 0

    for entry in fold_entries:
        oos = entry.get("out_of_sample") or {}
        summary = oos.get("summary") or {}
        pnl = oos.get("total_pnl")
        if pnl is not None:
            pnl_f = float(pnl)
            oos_pnls.append(pnl_f)
            if pnl_f > 0:
                positive += 1
        sharpe = summary.get("sharpe_ratio")
        if sharpe is not None:
            oos_sharpes.append(float(sharpe))

        is_row = {
            "summary": (entry.get("in_sample") or {}).get("summary") or {},
            "total_pnl": (entry.get("in_sample") or {}).get("total_pnl"),
            "trade_count": (entry.get("in_sample") or {}).get("trade_count"),
        }
        oos_row = {
            "summary": summary,
            "total_pnl": oos.get("total_pnl"),
            "trade_count": oos.get("trade_count"),
        }
        is_v = row_objective_value(is_row, objective)
        oos_v = row_objective_value(oos_row, objective)
        if is_v > -1e17:
            is_objectives.append(is_v)
        if oos_v > -1e17:
            oos_objectives.append(oos_v)

    n = len(fold_entries)
    mean_pnl = sum(oos_pnls) / len(oos_pnls) if oos_pnls else None
    mean_sharpe = sum(oos_sharpes) / len(oos_sharpes) if oos_sharpes else None
    stability = (positive / n) if n else 0.0
    mean_is_obj = sum(is_objectives) / len(is_objectives) if is_objectives else None
    mean_oos_obj = sum(oos_objectives) / len(oos_objectives) if oos_objectives else None

    bias = selection_bias_summary(
        fold_entries=fold_entries,
        objective=objective,
        num_trials=num_trials,
        row_objective_fn=row_objective_value,
    )

    return {
        "fold_count": n,
        "mean_pnl": mean_pnl,
        "mean_sharpe": mean_sharpe,
        "stability_score": round(stability, 4),
        "positive_folds": positive,
        "objective": objective,
        "mean_in_sample_objective": mean_is_obj,
        "mean_out_of_sample_objective": mean_oos_obj,
        "walk_forward_efficiency": bias.get("walk_forward_efficiency"),
        "deflated_sharpe_ratio": bias.get("deflated_sharpe_ratio"),
        "selection_bias": bias,
        "regime_analysis": aggregate_regime_oos(fold_entries),
    }


def _config_key(cfg: dict) -> str:
    return json.dumps(cfg, sort_keys=True, default=str)


def _ensure_live_parity(cfg: dict) -> dict:
    """Walk-forward / optimizer runs must mirror live gates (HTF, filters)."""
    out = copy.deepcopy(cfg or {})
    out["sim_mode"] = "live_aligned"
    out["live_parity"] = True
    return out


def _resolve_min_trades(
    min_trades: int,
    configs: list[dict],
    sweep: dict | None = None,
    *,
    walk_forward: bool = False,
) -> tuple[int, dict[str, Any]]:
    """Apply trades-per-parameter floor (relaxed on WF IS windows)."""
    # Prefer axes that actually vary across combos; ignore single-value "sweep" lists.
    axes = count_varying_param_axes(configs)
    if axes <= 0:
        axes = count_sweep_axes(sweep)
    per_param = WF_MIN_TRADES_PER_PARAM if walk_forward else MIN_TRADES_PER_PARAM
    effective = effective_min_trades(axes, base_min=min_trades, trades_per_param=per_param)
    meta = {
        "requested_min_trades": max(0, int(min_trades or 0)),
        "effective_min_trades": effective,
        "swept_param_axes": axes,
        "trades_per_param_rule": per_param,
        "walk_forward_floor": bool(walk_forward),
    }
    return effective, meta


def _format_no_valid_is_error(
    sweep_rows: list[dict],
    *,
    effective_min: int,
    min_meta: dict,
    fold_idx: int | None = None,
    train_bars: int | None = None,
    candle_meta: dict | None = None,
) -> str:
    """Actionable message when every IS sweep row fails eligibility."""
    total = len(sweep_rows)
    errs = sum(1 for r in sweep_rows if r.get("error"))
    below = sum(
        1 for r in sweep_rows
        if not r.get("error") and _row_trade_count(r) < effective_min
    )
    trade_counts = [_row_trade_count(r) for r in sweep_rows if not r.get("error")]
    best_trades = max(trade_counts) if trade_counts else 0
    axes = int(min_meta.get("swept_param_axes") or 0)
    per_param = int(min_meta.get("trades_per_param_rule") or MIN_TRADES_PER_PARAM)
    prefix = (
        f"Walk-forward fold {fold_idx + 1}"
        if fold_idx is not None
        else "Walk-forward sweep"
    )
    msg = (
        f"{prefix} produced no valid in-sample runs "
        f"({total} tested: {errs} errors, {below} below min {effective_min} trades; "
        f"best IS trade count: {best_trades}). "
        f"Floor is max(requested min, {per_param}×{axes} varying sweep params). "
        f"Try more days, lower min trades, fewer swept params, or disable final holdout."
    )
    cm = candle_meta or {}
    detail_bits: list[str] = []
    if train_bars is not None:
        detail_bits.append(f"IS bars={train_bars}")
    if cm.get("replayed_days") is not None:
        detail_bits.append(
            f"history≈{cm.get('replayed_days')}d"
            f" (requested {cm.get('days_requested') or cm.get('days') or '?'}d)"
        )
    if cm.get("resolution_note"):
        detail_bits.append(str(cm["resolution_note"]))
    if cm.get("range_note"):
        detail_bits.append(str(cm["range_note"]))
    if detail_bits:
        msg = f"{msg} Data: {' · '.join(detail_bits)}."
    return msg


def _run_oos_backtest(
    run_backtest: Callable[..., dict],
    *,
    symbol: str,
    strategy: str,
    config: dict,
    train: list[dict],
    test: list[dict],
    progress_cb=None,
    cancel_cb=None,
) -> dict:
    """OOS run with IS warm-up prefix; PnL scored only on the OOS window."""
    oos_candles, score_from = build_oos_candles_with_warmup(train, test)
    oos_cfg = _ensure_live_parity(config)
    if score_from is not None:
        oos_cfg["score_from_time"] = score_from
    return run_backtest(
        symbol,
        strategy,
        oos_cfg,
        oos_candles,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
    )


def run_final_holdout_validation(
    *,
    run_backtest: Callable[..., dict],
    symbol: str,
    strategy: str,
    config: dict,
    optimization_candles: list[dict],
    holdout_candles: list[dict],
    holdout_meta: dict | None = None,
    min_trades: int = 0,
    min_pnl: float = 0.0,
    cancel_cb=None,
) -> dict[str, Any]:
    """One-shot validation on a reserved trailing segment never used in optimization."""
    if not holdout_candles:
        return {"skipped": True, "note": "No holdout segment reserved"}

    oos = _run_oos_backtest(
        run_backtest,
        symbol=symbol,
        strategy=strategy,
        config=config,
        train=optimization_candles,
        test=holdout_candles,
        cancel_cb=cancel_cb,
    )
    if oos.get("cancelled"):
        return {"cancelled": True}
    if oos.get("error"):
        return {"error": oos["error"], "passed": False}

    summary = oos.get("summary") or {}
    pnl = float(oos.get("total_pnl") or summary.get("total_pnl") or 0)
    trades = int(oos.get("trade_count") or summary.get("total_trades") or 0)
    passed = trades >= max(0, int(min_trades)) and pnl >= float(min_pnl)
    return {
        "total_pnl": round(pnl, 4),
        "trade_count": trades,
        "summary": summary,
        "meta": holdout_meta or {},
        "passed": passed,
        "min_trades": int(min_trades),
        "min_pnl": float(min_pnl),
        "equity_curve": _downsample_curve(oos.get("equity_curve")),
    }


def _run_single_walk_forward(
    *,
    run_backtest: Callable[..., dict],
    symbol: str,
    strategy: str,
    configs: list[dict],
    train: list[dict],
    test: list[dict],
    train_meta: dict,
    test_meta: dict,
    meta: dict,
    train_pct: float,
    sweep_objective: str,
    min_trades: int,
    progress_cb=None,
    cancel_cb=None,
    sweep: dict | None = None,
    wf_options: dict | None = None,
) -> dict[str, Any]:
    """Single 70/30 split — original walk-forward behavior."""
    configs = [_ensure_live_parity(c) for c in configs]
    effective_min, min_meta = _resolve_min_trades(
        min_trades, configs, sweep, walk_forward=True,
    )
    from app.services.bots.backtest_bayesian import is_bayesian_sweep
    from app.services.bots.backtest_sweep import _max_combos_for_mode
    trial_budget = _max_combos_for_mode(sweep or {}, "bayesian") if is_bayesian_sweep(sweep) else len(configs)
    total_runs = trial_budget
    sweep_rows = _run_in_sample_sweep(
        run_backtest=run_backtest,
        symbol=symbol,
        strategy=strategy,
        configs=configs,
        train=train,
        sweep_objective=sweep_objective,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
        fold_idx=0,
        total_folds=1,
        run_offset=0,
        total_runs=total_runs,
        sweep=sweep,
        base_config=configs[0] if configs else {},
        min_trades=effective_min,
    )
    if cancel_cb and cancel_cb():
        return {"error": "Backtest cancelled", "cancelled": True}

    best_config, best_row = pick_best_config(
        sweep_rows,
        objective=sweep_objective,
        min_trades=effective_min,
    )
    if not best_config:
        return {"error": _format_no_valid_is_error(
            sweep_rows,
            effective_min=effective_min,
            min_meta=min_meta,
            train_bars=len(train or []),
            candle_meta=meta,
        )}

    def _oos_progress(done: int, total: int) -> None:
        if progress_cb:
            progress_cb(done, total, total_runs, total_runs, True, 0, 1)

    oos = _run_oos_backtest(
        run_backtest,
        symbol=symbol,
        strategy=strategy,
        config=best_config,
        train=train,
        test=test,
        progress_cb=_oos_progress if progress_cb else None,
        cancel_cb=cancel_cb,
    )
    if oos.get("cancelled"):
        return oos
    if oos.get("error"):
        return {"error": f"Out-of-sample run failed: {oos['error']}"}

    from app.services.bots.backtest_analytics import classify_backtest_regime

    fold_entry = {
        "fold": 1,
        "best_config": best_config,
        "oos_regime": classify_backtest_regime(test),
        "in_sample": {
            "summary": (best_row or {}).get("summary") or {},
            "total_pnl": (best_row or {}).get("total_pnl"),
            "trade_count": (best_row or {}).get("trade_count"),
            "meta": train_meta,
        },
        "out_of_sample": _oos_snapshot(oos),
    }
    aggregate = aggregate_fold_oos(
        [fold_entry],
        objective=sweep_objective,
        num_trials=len(configs),
    )

    merged = dict(oos)
    merged["meta"] = {**(oos.get("meta") or test_meta), **test_meta}
    merged["meta"]["train_pct"] = train_pct
    merged["meta"]["walk_forward"] = True
    merged["meta"]["rolling_folds"] = 1
    merged["meta"]["min_trades"] = min_meta
    merged["sweep"] = {
        "configs_tested": len(configs),
        "best_config": best_config,
        "objective": sweep_objective,
        "min_trades": effective_min,
        "min_trades_meta": min_meta,
        "results": sort_sweep_rows(
            sweep_rows,
            objective=sweep_objective,
            min_trades=effective_min,
        ),
    }
    merged["walk_forward"] = {
        "train_pct": train_pct,
        "rolling_folds": 1,
        "wf_mode": (wf_options or {}).get("wf_mode", "rolling"),
        "validation": wf_options or {},
        "folds": [fold_entry],
        "aggregate": aggregate,
        "in_sample": fold_entry["in_sample"],
        "out_of_sample": fold_entry["out_of_sample"],
        "best_config": best_config,
        "oos_equity_stitch": stitch_oos_equity_curves(
            [fold_entry],
            starting_equity=float(oos.get("starting_equity") or oos.get("allocation") or 10_000),
        ),
    }
    from app.services.bots.backtest_category_metrics import attach_is_vs_oos, is_vs_oos_from_windows
    attach_is_vs_oos(
        merged,
        is_vs_oos_from_windows(fold_entry.get("in_sample"), fold_entry.get("out_of_sample")),
    )
    return merged


def _run_in_sample_sweep(
    *,
    run_backtest: Callable[..., dict],
    symbol: str,
    strategy: str,
    configs: list[dict],
    train: list[dict],
    sweep_objective: str,
    progress_cb=None,
    cancel_cb=None,
    fold_idx: int = 0,
    total_folds: int = 1,
    run_offset: int = 0,
    total_runs: int | None = None,
    sweep: dict | None = None,
    base_config: dict | None = None,
    min_trades: int = 0,
) -> list[dict]:
    from app.services.bots.backtest_bayesian import is_bayesian_sweep, run_bayesian_sweep

    if is_bayesian_sweep(sweep):
        budget = int((sweep or {}).get("max_combos") or 50)

        def _evaluate(cfg: dict) -> dict:
            if cancel_cb and cancel_cb():
                return {"cancelled": True}
            return run_backtest(symbol, strategy, cfg, train, cancel_cb=cancel_cb)

        def _bayes_progress(done: int, total: int) -> None:
            if progress_cb:
                progress_cb(
                    done, total, run_offset + done - 1, max(total_runs or budget, budget),
                    False, fold_idx, total_folds,
                )

        rows, _meta = run_bayesian_sweep(
            base_config=base_config or (configs[0] if configs else {}),
            sweep=sweep,
            evaluate_fn=_evaluate,
            objective=sweep_objective,
            min_trades=min_trades,
            progress_cb=_bayes_progress if progress_cb else None,
            cancel_cb=cancel_cb,
        )
        for row in rows:
            row["window"] = "in_sample"
        return rows

    total_runs = total_runs or len(configs)
    sweep_rows: list[dict] = []

    def _run_one(idx: int, cfg: dict) -> tuple[int, dict | None]:
        if cancel_cb and cancel_cb():
            return idx, None

        def _is_progress(done: int, total: int, _idx: int = idx) -> None:
            if progress_cb:
                global_run = run_offset + _idx
                progress_cb(
                    done, total, global_run, total_runs, False,
                    fold_idx, total_folds,
                )

        res = run_backtest(
            symbol, strategy, cfg, train,
            progress_cb=_is_progress if progress_cb else None,
            cancel_cb=cancel_cb,
        )
        if res.get("cancelled"):
            return idx, None
        return idx, _result_to_row(cfg, res, window="in_sample")

    workers = parallel_worker_count(len(configs))
    if workers > 1:
        rows_by_idx: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bt-wf-sweep") as pool:
            futures = [pool.submit(_run_one, idx, cfg) for idx, cfg in enumerate(configs)]
            for fut in as_completed(futures):
                if cancel_cb and cancel_cb():
                    return sweep_rows
                idx, row = fut.result()
                if row is None:
                    if cancel_cb and cancel_cb():
                        return sweep_rows
                    continue
                rows_by_idx[idx] = row
        for idx in sorted(rows_by_idx):
            sweep_rows.append(rows_by_idx[idx])
        return sweep_rows

    for idx, cfg in enumerate(configs):
        if cancel_cb and cancel_cb():
            return sweep_rows
        _, row = _run_one(idx, cfg)
        if row is None:
            return sweep_rows
        sweep_rows.append(row)
    return sweep_rows


def run_walk_forward(
    *,
    run_backtest: Callable[..., dict],
    symbol: str,
    strategy: str,
    base_config: dict,
    candles: list[dict],
    meta: dict,
    configs: list[dict],
    train_pct: float = 70.0,
    rolling_folds: int = 1,
    sweep_objective: str = "total_pnl",
    min_trades: int = 0,
    progress_cb=None,
    cancel_cb=None,
    sweep: dict | None = None,
    wf_options: dict | None = None,
) -> dict[str, Any]:
    """
    Optimize on train window(s), evaluate on out-of-sample.

    rolling_folds=1: single train/test split (backward compatible).
    rolling_folds>1: rolling sequential folds with aggregated OOS metrics.

    Returns merged result dict with walk_forward block.
    """
    configs = [_ensure_live_parity(c) for c in configs]
    effective_min, min_meta = _resolve_min_trades(
        min_trades, configs, sweep, walk_forward=True,
    )
    n_folds = max(1, int(rolling_folds or 1))
    windows = build_fold_windows(
        candles,
        meta,
        rolling_folds=n_folds,
        train_pct=train_pct,
        wf_options=wf_options,
    )
    if not windows:
        return {"error": "Not enough bars for walk-forward split"}

    if n_folds <= 1 and len(windows) == 1:
        return _run_single_walk_forward(
            run_backtest=run_backtest,
            symbol=symbol,
            strategy=strategy,
            configs=configs,
            train=windows[0][0],
            test=windows[0][1],
            train_meta=windows[0][2],
            test_meta=windows[0][3],
            meta=meta,
            train_pct=train_pct,
            sweep_objective=sweep_objective,
            min_trades=min_trades,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
            sweep=sweep,
            wf_options=wf_options,
        )

    total_folds = len(windows)
    from app.services.bots.backtest_bayesian import is_bayesian_sweep
    from app.services.bots.backtest_sweep import _max_combos_for_mode
    per_fold_runs = (
        _max_combos_for_mode(sweep or {}, "bayesian")
        if is_bayesian_sweep(sweep)
        else len(configs)
    )
    total_is_runs = per_fold_runs * total_folds
    fold_entries: list[dict] = []
    last_sweep_rows: list[dict] = []

    for fold_idx, (train, test, train_meta, test_meta) in enumerate(windows):
        if cancel_cb and cancel_cb():
            return {"error": "Backtest cancelled", "cancelled": True}

        run_offset = fold_idx * per_fold_runs
        sweep_rows = _run_in_sample_sweep(
            run_backtest=run_backtest,
            symbol=symbol,
            strategy=strategy,
            configs=configs,
            train=train,
            sweep_objective=sweep_objective,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
            fold_idx=fold_idx,
            total_folds=total_folds,
            run_offset=run_offset,
            total_runs=total_is_runs,
            sweep=sweep,
            base_config=configs[0] if configs else {},
            min_trades=effective_min,
        )
        if cancel_cb and cancel_cb():
            return {"error": "Backtest cancelled", "cancelled": True}

        ranked = sort_sweep_rows(sweep_rows, objective=sweep_objective, min_trades=effective_min)
        last_sweep_rows = ranked
        best_config, best_row = pick_best_config(
            sweep_rows,
            objective=sweep_objective,
            min_trades=effective_min,
        )
        if not best_config:
            return {"error": _format_no_valid_is_error(
                sweep_rows,
                effective_min=effective_min,
                min_meta=min_meta,
                fold_idx=fold_idx,
                train_bars=len(train or []),
                candle_meta=meta,
            )}

        if cancel_cb and cancel_cb():
            return {"error": "Backtest cancelled", "cancelled": True}

        def _oos_progress(done: int, total: int) -> None:
            if progress_cb:
                progress_cb(
                    done, total, total_is_runs, total_is_runs, True,
                    fold_idx, total_folds,
                )

        oos = _run_oos_backtest(
            run_backtest,
            symbol=symbol,
            strategy=strategy,
            config=best_config,
            train=train,
            test=test,
            progress_cb=_oos_progress if progress_cb else None,
            cancel_cb=cancel_cb,
        )
        if oos.get("cancelled"):
            return oos
        if oos.get("error"):
            return {"error": f"Out-of-sample run failed (fold {fold_idx + 1}): {oos['error']}"}

        from app.services.bots.backtest_analytics import classify_backtest_regime

        oos_regime = classify_backtest_regime(test)
        fold_entries.append({
            "fold": fold_idx + 1,
            "best_config": best_config,
            "oos_regime": oos_regime,
            "in_sample": {
                "summary": (best_row or {}).get("summary") or {},
                "total_pnl": (best_row or {}).get("total_pnl"),
                "trade_count": (best_row or {}).get("trade_count"),
                "meta": train_meta,
            },
            "out_of_sample": _oos_snapshot(oos),
        })

    # Pick config with best mean OOS objective across all folds (not just last-fold IS winner)
    config_oos_scores: dict[str, dict] = {}
    for cfg in configs:
        key = _config_key(cfg)
        oos_values: list[float] = []
        for fold_idx, (train, test, _, _) in enumerate(windows):
            if cancel_cb and cancel_cb():
                return {"error": "Backtest cancelled", "cancelled": True}
            res = _run_oos_backtest(
                run_backtest,
                symbol=symbol,
                strategy=strategy,
                config=cfg,
                train=train,
                test=test,
                cancel_cb=cancel_cb,
            )
            if res.get("cancelled"):
                return res
            if res.get("error"):
                oos_values.append(-1e18)
            else:
                oos_values.append(_metric_from_backtest(res, sweep_objective))
        if oos_values:
            config_oos_scores[key] = {
                "config": cfg,
                "mean_oos": sum(oos_values) / len(oos_values),
                "oos_values": oos_values,
            }

    if not config_oos_scores:
        return {"error": "Walk-forward produced no OOS scores"}

    best_entry = max(config_oos_scores.values(), key=lambda e: e["mean_oos"])
    best_config = best_entry["config"]
    aggregate = aggregate_fold_oos(
        fold_entries,
        objective=sweep_objective,
        num_trials=len(configs),
    )
    aggregate["mean_oos_objective"] = best_entry["mean_oos"]

    last_train, last_test, last_train_meta, last_test_meta = windows[-1]
    last_is = run_backtest(
        symbol, strategy, best_config, last_train,
        cancel_cb=cancel_cb,
    )
    if last_is.get("cancelled"):
        return last_is
    if last_is.get("error"):
        return {"error": f"Final in-sample run failed: {last_is['error']}"}

    last_oos = _run_oos_backtest(
        run_backtest,
        symbol=symbol,
        strategy=strategy,
        config=best_config,
        train=last_train,
        test=last_test,
        cancel_cb=cancel_cb,
    )
    if last_oos.get("cancelled"):
        return last_oos
    if last_oos.get("error"):
        return {"error": f"Final OOS run failed: {last_oos['error']}"}

    merged = dict(last_oos)
    merged["meta"] = {**(last_oos.get("meta") or {}), **(meta or {}), **last_test_meta}
    merged["meta"]["train_pct"] = train_pct
    merged["meta"]["walk_forward"] = True
    merged["meta"]["rolling_folds"] = total_folds
    merged["meta"]["min_trades"] = min_meta
    merged["sweep"] = {
        "configs_tested": len(configs),
        "best_config": best_config,
        "objective": sweep_objective,
        "min_trades": effective_min,
        "min_trades_meta": min_meta,
        "results": last_sweep_rows,
    }
    merged["walk_forward"] = {
        "train_pct": train_pct,
        "rolling_folds": total_folds,
        "wf_mode": (wf_options or {}).get("wf_mode", "rolling"),
        "validation": wf_options or {},
        "folds": fold_entries,
        "aggregate": aggregate,
        "in_sample": {
            "summary": last_is.get("summary") or {},
            "total_pnl": last_is.get("total_pnl"),
            "trade_count": last_is.get("trade_count"),
            "meta": last_train_meta,
        },
        "out_of_sample": _oos_snapshot(last_oos),
        "best_config": best_config,
        "oos_equity_stitch": stitch_oos_equity_curves(
            fold_entries,
            starting_equity=float(last_oos.get("starting_equity") or last_oos.get("allocation") or 10_000),
        ),
    }
    from app.services.bots.backtest_category_metrics import attach_is_vs_oos, is_vs_oos_from_windows
    # Prefer aggregate IS/OOS means across folds when available; else last fold.
    agg = aggregate or {}
    is_vs = {
        "is_sharpe": None,
        "oos_sharpe": agg.get("mean_sharpe"),
        "is_pnl": agg.get("mean_in_sample_objective") if agg.get("objective") == "total_pnl" else None,
        "oos_pnl": agg.get("mean_pnl"),
    }
    # Overlay last-fold explicit Sharpes when aggregate lacks them
    last_pair = is_vs_oos_from_windows(
        merged["walk_forward"]["in_sample"],
        merged["walk_forward"]["out_of_sample"],
    ) or {}
    for k, v in last_pair.items():
        if is_vs.get(k) is None and v is not None:
            is_vs[k] = v
    attach_is_vs_oos(merged, is_vs)
    return merged
