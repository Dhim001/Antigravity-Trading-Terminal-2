"""Walk-forward optimization — train sweep on in-sample, validate on out-of-sample."""

from __future__ import annotations

import copy
import json
from typing import Any, Callable

from app.services.bots.backtest_sweep import sweep_label


def split_train_test(
    candles: list[dict],
    meta: dict,
    train_pct: float = 70.0,
) -> tuple[list[dict], list[dict], dict, dict]:
    """Split candles into train (in-sample) and test (out-of-sample) windows."""
    if not candles:
        return [], [], dict(meta or {}), dict(meta or {})
    pct = max(50.0, min(90.0, float(train_pct)))
    split = int(len(candles) * pct / 100.0)
    split = max(50, min(split, len(candles) - 50))
    train = candles[:split]
    test = candles[split:]
    train_meta = copy.deepcopy(meta or {})
    test_meta = copy.deepcopy(meta or {})
    if train:
        train_meta["newest"] = train[-1].get("time", train_meta.get("newest"))
        train_meta["count"] = len(train)
        train_meta["window"] = "in_sample"
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
) -> list[tuple[list[dict], list[dict], dict, dict]]:
    """
    Build sequential IS/OOS windows for walk-forward.

    rolling_folds=1: single 70/30 (or train_pct) split over the full range.
    rolling_folds>1: divide the range into N equal slices; each fold IS/OOS-splits its slice.
    """
    n_folds = max(1, int(rolling_folds or 1))
    if not candles:
        return []

    if n_folds <= 1:
        train, test, train_meta, test_meta = split_train_test(candles, meta, train_pct)
        if len(train) < 50 or len(test) < 50:
            return []
        return [(train, test, train_meta, test_meta)]

    fold_size = len(candles) // n_folds
    if fold_size < 100:
        return []

    windows: list[tuple[list[dict], list[dict], dict, dict]] = []
    for i in range(n_folds):
        start = i * fold_size
        end = len(candles) if i == n_folds - 1 else (i + 1) * fold_size
        window = candles[start:end]
        if len(window) < 100:
            continue
        wmeta = copy.deepcopy(meta or {})
        if window:
            wmeta["oldest"] = window[0].get("time", wmeta.get("oldest"))
            wmeta["newest"] = window[-1].get("time", wmeta.get("newest"))
            wmeta["count"] = len(window)
            wmeta["fold"] = i + 1
        train, test, train_meta, test_meta = split_train_test(window, wmeta, train_pct)
        if len(train) < 50 or len(test) < 50:
            continue
        train_meta["fold"] = i + 1
        test_meta["fold"] = i + 1
        windows.append((train, test, train_meta, test_meta))
    return windows


VALID_SWEEP_OBJECTIVES = (
    "total_pnl",
    "sharpe_ratio",
    "profit_factor",
    "sortino_ratio",
    "calmar_ratio",
    "max_drawdown_penalty",
)


def row_trade_count(row: dict) -> int:
    return int(row.get("trade_count") or (row.get("summary") or {}).get("total_trades") or 0)


def _row_trade_count(row: dict) -> int:
    return row_trade_count(row)


def row_objective_value(row: dict, objective: str = "total_pnl") -> float:
    """Extract ranking metric from a sweep row."""
    summary = row.get("summary") or {}
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
    return {
        "label": sweep_label(cfg),
        "config": cfg,
        "summary": res.get("summary") or {},
        "total_pnl": res.get("total_pnl"),
        "trade_count": res.get("trade_count"),
        "window": window,
    }


def _metric_from_backtest(res: dict, objective: str) -> float:
    return row_objective_value(
        {
            "total_pnl": res.get("total_pnl"),
            "summary": res.get("summary") or {},
            "trade_count": res.get("trade_count"),
        },
        objective,
    )


def aggregate_fold_oos(
    fold_entries: list[dict],
    *,
    objective: str = "total_pnl",
) -> dict[str, Any]:
    """Aggregate OOS metrics across walk-forward folds."""
    oos_pnls: list[float] = []
    oos_sharpes: list[float] = []
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

    n = len(fold_entries)
    mean_pnl = sum(oos_pnls) / len(oos_pnls) if oos_pnls else None
    mean_sharpe = sum(oos_sharpes) / len(oos_sharpes) if oos_sharpes else None
    stability = (positive / n) if n else 0.0

    return {
        "fold_count": n,
        "mean_pnl": mean_pnl,
        "mean_sharpe": mean_sharpe,
        "stability_score": round(stability, 4),
        "positive_folds": positive,
        "objective": objective,
    }


def _config_key(cfg: dict) -> str:
    return json.dumps(cfg, sort_keys=True, default=str)


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
) -> dict[str, Any]:
    """Single 70/30 split — original walk-forward behavior."""
    total_runs = len(configs)
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
    )
    if cancel_cb and cancel_cb():
        return {"error": "Backtest cancelled", "cancelled": True}

    best_config, best_row = pick_best_config(
        sweep_rows,
        objective=sweep_objective,
        min_trades=min_trades,
    )
    if not best_config:
        return {"error": "Walk-forward sweep produced no valid in-sample runs"}

    def _oos_progress(done: int, total: int) -> None:
        if progress_cb:
            progress_cb(done, total, total_runs, total_runs, True, 0, 1)

    oos = run_backtest(
        symbol, strategy, best_config, test,
        progress_cb=_oos_progress if progress_cb else None,
        cancel_cb=cancel_cb,
    )
    if oos.get("cancelled"):
        return oos
    if oos.get("error"):
        return {"error": f"Out-of-sample run failed: {oos['error']}"}

    fold_entry = {
        "fold": 1,
        "best_config": best_config,
        "in_sample": {
            "summary": (best_row or {}).get("summary") or {},
            "total_pnl": (best_row or {}).get("total_pnl"),
            "trade_count": (best_row or {}).get("trade_count"),
            "meta": train_meta,
        },
        "out_of_sample": {
            "summary": oos.get("summary") or {},
            "total_pnl": oos.get("total_pnl"),
            "trade_count": oos.get("trade_count"),
            "meta": test_meta,
        },
    }
    aggregate = aggregate_fold_oos([fold_entry], objective=sweep_objective)

    merged = dict(oos)
    merged["meta"] = {**(oos.get("meta") or test_meta), **test_meta}
    merged["meta"]["train_pct"] = train_pct
    merged["meta"]["walk_forward"] = True
    merged["meta"]["rolling_folds"] = 1
    merged["sweep"] = {
        "configs_tested": len(configs),
        "best_config": best_config,
        "objective": sweep_objective,
        "min_trades": min_trades,
        "results": sort_sweep_rows(
            sweep_rows,
            objective=sweep_objective,
            min_trades=min_trades,
        ),
    }
    merged["walk_forward"] = {
        "train_pct": train_pct,
        "rolling_folds": 1,
        "folds": [fold_entry],
        "aggregate": aggregate,
        "in_sample": fold_entry["in_sample"],
        "out_of_sample": fold_entry["out_of_sample"],
        "best_config": best_config,
    }
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
) -> list[dict]:
    total_runs = total_runs or len(configs)
    sweep_rows: list[dict] = []
    for idx, cfg in enumerate(configs):
        if cancel_cb and cancel_cb():
            return sweep_rows

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
            return sweep_rows
        sweep_rows.append(_result_to_row(cfg, res, window="in_sample"))
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
) -> dict[str, Any]:
    """
    Optimize on train window(s), evaluate on out-of-sample.

    rolling_folds=1: single train/test split (backward compatible).
    rolling_folds>1: rolling sequential folds with aggregated OOS metrics.

    Returns merged result dict with walk_forward block.
    """
    n_folds = max(1, int(rolling_folds or 1))
    windows = build_rolling_fold_windows(candles, meta, n_folds, train_pct)
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
        )

    total_folds = len(windows)
    total_is_runs = len(configs) * total_folds
    fold_entries: list[dict] = []
    last_sweep_rows: list[dict] = []

    for fold_idx, (train, test, train_meta, test_meta) in enumerate(windows):
        if cancel_cb and cancel_cb():
            return {"error": "Backtest cancelled", "cancelled": True}

        run_offset = fold_idx * len(configs)
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
        )
        if cancel_cb and cancel_cb():
            return {"error": "Backtest cancelled", "cancelled": True}

        ranked = sort_sweep_rows(sweep_rows, objective=sweep_objective, min_trades=min_trades)
        last_sweep_rows = ranked
        best_config, best_row = pick_best_config(
            sweep_rows,
            objective=sweep_objective,
            min_trades=min_trades,
        )
        if not best_config:
            return {"error": f"Walk-forward fold {fold_idx + 1} produced no valid in-sample runs"}

        if cancel_cb and cancel_cb():
            return {"error": "Backtest cancelled", "cancelled": True}

        def _oos_progress(done: int, total: int) -> None:
            if progress_cb:
                progress_cb(
                    done, total, total_is_runs, total_is_runs, True,
                    fold_idx, total_folds,
                )

        oos = run_backtest(
            symbol, strategy, best_config, test,
            progress_cb=_oos_progress if progress_cb else None,
            cancel_cb=cancel_cb,
        )
        if oos.get("cancelled"):
            return oos
        if oos.get("error"):
            return {"error": f"Out-of-sample run failed (fold {fold_idx + 1}): {oos['error']}"}

        fold_entries.append({
            "fold": fold_idx + 1,
            "best_config": best_config,
            "in_sample": {
                "summary": (best_row or {}).get("summary") or {},
                "total_pnl": (best_row or {}).get("total_pnl"),
                "trade_count": (best_row or {}).get("trade_count"),
                "meta": train_meta,
            },
            "out_of_sample": {
                "summary": oos.get("summary") or {},
                "total_pnl": oos.get("total_pnl"),
                "trade_count": oos.get("trade_count"),
                "meta": test_meta,
            },
        })

    # Pick config with best mean OOS objective across all folds (not just last-fold IS winner)
    config_oos_scores: dict[str, dict] = {}
    for cfg in configs:
        key = _config_key(cfg)
        oos_values: list[float] = []
        for fold_idx, (_, test, _, _) in enumerate(windows):
            if cancel_cb and cancel_cb():
                return {"error": "Backtest cancelled", "cancelled": True}
            res = run_backtest(symbol, strategy, cfg, test, cancel_cb=cancel_cb)
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
    aggregate = aggregate_fold_oos(fold_entries, objective=sweep_objective)
    aggregate["mean_oos_objective"] = best_entry["mean_oos"]

    last_fold = fold_entries[-1]
    last_oos = run_backtest(
        symbol, strategy, best_config, windows[-1][1],
        cancel_cb=cancel_cb,
    )
    if last_oos.get("cancelled"):
        return last_oos
    if last_oos.get("error"):
        return {"error": f"Final OOS run failed: {last_oos['error']}"}

    merged = dict(last_oos)
    merged["meta"] = {**(last_oos.get("meta") or {}), **(meta or {})}
    merged["meta"]["train_pct"] = train_pct
    merged["meta"]["walk_forward"] = True
    merged["meta"]["rolling_folds"] = n_folds if n_folds > 1 else 1
    merged["sweep"] = {
        "configs_tested": len(configs),
        "best_config": best_config,
        "objective": sweep_objective,
        "min_trades": min_trades,
        "results": last_sweep_rows,
    }
    merged["walk_forward"] = {
        "train_pct": train_pct,
        "rolling_folds": n_folds if n_folds > 1 else 1,
        "folds": fold_entries,
        "aggregate": aggregate,
        "in_sample": last_fold["in_sample"],
        "out_of_sample": last_fold["out_of_sample"],
        "best_config": best_config,
    }
    return merged
