"""Research benchmark for backtest compute saturation.

Reports separate bars/sec for feature engineering, predict, and a lightweight
sim-phase stub on a synthetic fixture (no network).

Usage (from ``backend/``)::

    python -m tools.bench_backtest
    python -m tools.bench_backtest --bars 5000 --no-batch --no-vectorized
    python -m tools.bench_backtest --bars 2000 --vectorized --batch

Env flags (also exposed as CLI)::

    BACKTEST_BATCH_INFERENCE=true|false
    BACKTEST_VECTORIZED_FEATURES=true|false
    BACKTEST_NUMBA_FEATURES=true|false   # JIT CVD/VPIN/rolling kernels
    BACKTEST_PARALLEL_BACKEND=auto|thread|process
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any


def _make_bar(i: int, **overrides: Any) -> dict:
    base = 100.0 + i * 0.05
    bar = {
        "time": 1_700_000_000 + i * 60,
        "open": base,
        "high": base + 1.0,
        "low": base - 1.0,
        "close": base + 0.2,
        "volume": 1000.0 + i,
        "ATR_14": 1.5,
        "ATR_14_median_20": 1.4,
        "RSI_14": 50.0 + (i % 10),
        "MACDh_12_26_9": 0.1 * ((i % 5) - 2),
        "STOCHk_14_3_3": 55.0,
        "ADX_14": 22.0,
        "EMA_9": base,
        "EMA_21": base - 0.3,
        "BBU_20_2.0": base + 2.0,
        "BBL_20_2.0": base - 2.0,
        "BBM_20_2.0": base,
        "VWAP": base,
        "SUPERTd_14_3.0": 1.0 if i % 2 == 0 else -1.0,
        "_symbol": "BENCHUSDT",
    }
    bar.update(overrides)
    return bar


def _cpu_note() -> str:
    cores = os.cpu_count() or 0
    bits = [
        f"cpu_count={cores}",
        f"BATCH={os.environ.get('BACKTEST_BATCH_INFERENCE', 'true')}",
        f"VECTORIZED={os.environ.get('BACKTEST_VECTORIZED_FEATURES', 'true')}",
        f"INFER_DEV={os.environ.get('BACKTEST_INFERENCE_DEVICE', 'auto')}",
        f"PARALLEL={os.environ.get('BACKTEST_PARALLEL_BACKEND', 'thread')}",
    ]
    return " | ".join(bits)


def _bench_features(rows: list[dict], *, vectorized: bool) -> tuple[float, int]:
    from app.services.bots.ml_feature_engineering import (
        precompute_signal_feature_matrix,
        precompute_signal_feature_matrix_loop,
    )

    n = len(rows)
    t0 = time.perf_counter()
    if vectorized:
        precompute_signal_feature_matrix(rows)
    else:
        precompute_signal_feature_matrix_loop(rows)
    elapsed = time.perf_counter() - t0
    return elapsed, n


def _bench_predict(rows: list[dict], *, batch: bool) -> tuple[float, int]:
    import numpy as np

    from app.services.bots.strategies_ml import MlSignalBoostStrategy, get_ml_signal_store

    class _FakeGbm:
        def predict_proba(self, X):
            X = np.asarray(X, dtype=np.float64)
            out = np.zeros((X.shape[0], 3), dtype=np.float64)
            out[:, 1] = 0.5
            out[:, 0] = 0.25
            out[:, 2] = 0.25
            return out

    store = get_ml_signal_store()
    symbol = "BENCHUSDT"
    tf = "1m"
    key = store._cache_key(symbol, None, tf)
    store._models[key] = _FakeGbm()
    store._metadata[key] = {
        "reverse_map": {"0": "BUY", "1": "NONE", "2": "SELL"},
        "feature_schema_version": 4,
    }
    store._mtime[key] = -1.0

    cfg = {
        "symbol": symbol,
        "model_symbol": symbol,
        "timeframe": tf,
        "min_confidence": 0.35,
        "batch_inference": batch,
        "batch_inference_size": 256,
        "calibration_gate_enabled": False,
    }
    strat = MlSignalBoostStrategy(cfg)
    t0 = time.perf_counter()
    if batch:
        strat.precompute_backtest_signals(rows)
    else:
        for row in rows:
            strat.evaluate(row)
    elapsed = time.perf_counter() - t0
    return elapsed, len(rows)


def _bench_sim_stub(n: int) -> tuple[float, int]:
    """Cheap bar-loop stand-in (position bookkeeping only)."""
    equity = 10_000.0
    pos = 0
    t0 = time.perf_counter()
    for i in range(n):
        px = 100.0 + i * 0.01
        if pos == 0 and i % 17 == 0:
            pos = 1
            entry = px
        elif pos == 1 and i % 23 == 0:
            equity += (px - entry) * 0.1
            pos = 0
        _ = equity + px * 0.0
    elapsed = time.perf_counter() - t0
    return elapsed, n


def run_bench(
    *,
    bars: int = 3000,
    batch: bool = True,
    vectorized: bool = True,
) -> dict[str, Any]:
    os.environ["BACKTEST_BATCH_INFERENCE"] = "true" if batch else "false"
    os.environ["BACKTEST_VECTORIZED_FEATURES"] = "true" if vectorized else "false"

    rows = [_make_bar(i) for i in range(max(50, int(bars)))]
    feat_s, feat_n = _bench_features(rows, vectorized=vectorized)
    pred_s, pred_n = _bench_predict(rows, batch=batch)
    sim_s, sim_n = _bench_sim_stub(len(rows))

    def bps(seconds: float, n: int) -> float:
        return (n / seconds) if seconds > 0 else float("inf")

    report = {
        "bars": len(rows),
        "vectorized_features": vectorized,
        "batch_inference": batch,
        "feature_sec": round(feat_s, 4),
        "feature_bars_per_sec": round(bps(feat_s, feat_n), 1),
        "predict_sec": round(pred_s, 4),
        "predict_bars_per_sec": round(bps(pred_s, pred_n), 1),
        "sim_sec": round(sim_s, 4),
        "sim_bars_per_sec": round(bps(sim_s, sim_n), 1),
        "wall_sec": round(feat_s + pred_s + sim_s, 4),
        "cpu_note": _cpu_note(),
    }
    return report


def _print_report(report: dict[str, Any]) -> None:
    print("=== backtest compute bench ===")
    print(f"bars={report['bars']}  vectorized={report['vectorized_features']}  "
          f"batch={report['batch_inference']}")
    print(f"feature: {report['feature_sec']:.4f}s  "
          f"({report['feature_bars_per_sec']:.0f} bars/s)")
    print(f"predict: {report['predict_sec']:.4f}s  "
          f"({report['predict_bars_per_sec']:.0f} bars/s)")
    print(f"sim:     {report['sim_sec']:.4f}s  "
          f"({report['sim_bars_per_sec']:.0f} bars/s)")
    print(f"wall:    {report['wall_sec']:.4f}s")
    print(f"note:    {report['cpu_note']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bars", type=int, default=3000, help="Synthetic bar count")
    p.add_argument("--batch", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument(
        "--vectorized", action=argparse.BooleanOptionalAction, default=True,
        help="Use columnar feature matrix (BACKTEST_VECTORIZED_FEATURES)",
    )
    args = p.parse_args(argv)
    # Ensure backend package root is on path when run as script.
    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
    report = run_bench(bars=args.bars, batch=args.batch, vectorized=args.vectorized)
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
