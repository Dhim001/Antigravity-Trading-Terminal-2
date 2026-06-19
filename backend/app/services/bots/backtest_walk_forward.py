"""Walk-forward optimization — train sweep on in-sample, validate on out-of-sample."""

from __future__ import annotations

import copy
from typing import Any, Callable


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


def pick_best_config(sweep_rows: list[dict]) -> tuple[dict | None, dict | None]:
    """Return (best_config, best_row) by total_pnl."""
    best_row = None
    best_pnl = -1e18
    for row in sweep_rows:
        if row.get("error"):
            continue
        pnl = float(row.get("total_pnl") or row.get("summary", {}).get("total_pnl") or -1e18)
        if pnl > best_pnl:
            best_pnl = pnl
            best_row = row
    if not best_row:
        return None, None
    return best_row.get("config"), best_row


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
    progress_cb=None,
    cancel_cb=None,
) -> dict[str, Any]:
    """
    Optimize on train window, evaluate best config on test window.
    Returns merged result dict with walk_forward block.
    """
    train, test, train_meta, test_meta = split_train_test(candles, meta, train_pct)
    if len(train) < 50 or len(test) < 50:
        return {"error": "Not enough bars for walk-forward split"}

    sweep_rows: list[dict] = []
    for idx, cfg in enumerate(configs):
        if cancel_cb and cancel_cb():
            return {"error": "Backtest cancelled", "cancelled": True}
        res = run_backtest(symbol, strategy, cfg, train, progress_cb=progress_cb, cancel_cb=cancel_cb)
        if res.get("cancelled"):
            return res
        if res.get("error"):
            sweep_rows.append({"config": cfg, "error": res["error"]})
            continue
        sweep_rows.append({
            "config": cfg,
            "summary": res.get("summary") or {},
            "total_pnl": res.get("total_pnl"),
            "trade_count": res.get("trade_count"),
            "window": "in_sample",
        })

    best_config, best_row = pick_best_config(sweep_rows)
    if not best_config:
        return {"error": "Walk-forward sweep produced no valid in-sample runs"}

    if cancel_cb and cancel_cb():
        return {"error": "Backtest cancelled", "cancelled": True}

    oos = run_backtest(
        symbol, strategy, best_config, test,
        progress_cb=progress_cb, cancel_cb=cancel_cb,
    )
    if oos.get("cancelled"):
        return oos
    if oos.get("error"):
        return {"error": f"Out-of-sample run failed: {oos['error']}"}

    merged = dict(oos)
    merged["meta"] = {**(oos.get("meta") or test_meta), **test_meta}
    merged["meta"]["train_pct"] = train_pct
    merged["meta"]["walk_forward"] = True
    merged["sweep"] = {
        "configs_tested": len(configs),
        "best_config": best_config,
        "results": sorted(
            sweep_rows,
            key=lambda r: float(r.get("total_pnl") or -1e18),
            reverse=True,
        ),
    }
    merged["walk_forward"] = {
        "train_pct": train_pct,
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
        "best_config": best_config,
    }
    return merged
