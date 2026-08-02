"""Backtest-only batch / vectorized ML inference helpers.

Live bots keep per-bar ``evaluate()``. Backtests can precompute signals in
chunks (256–2048) so ONNX / sklearn use SIMD / BLAS across many bars, then
feed the series into the existing bar loop (exits, cancel, progress, gates).

Enable / tune:
  BACKTEST_BATCH_INFERENCE=true|false   (default true)
  BACKTEST_INFERENCE_BATCH_SIZE=512
  config ``batch_inference`` / ``batch_inference_size`` overrides
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# Strategies with a ``precompute_backtest_signals`` implementation.
BATCH_ML_STRATEGIES = frozenset({
    "ML_SIGNAL_BOOST",
    "LSTM_DIRECTION",
    "TCN_MULTI_HORIZON",
    "TRANSFORMER_SIGNAL",
})


def _env_truthy(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def batch_inference_enabled(
    strategy_key: str = "",
    config: dict | None = None,
) -> bool:
    """Whether backtest should try batch precompute for this strategy."""
    cfg = config if isinstance(config, dict) else {}
    if "batch_inference" in cfg:
        return bool(cfg.get("batch_inference"))
    if not _env_truthy("BACKTEST_BATCH_INFERENCE", "true"):
        return False
    key = str(strategy_key or cfg.get("strategy") or "").upper()
    if key and key not in BATCH_ML_STRATEGIES:
        # Unknown / TA — skip quietly (hasattr guard still applies).
        if key and not any(
            tok in key for tok in ("ML_", "LSTM", "TCN", "TRANSFORMER", "RL_", "VAE", "GNN")
        ):
            return False
    return True


def inference_batch_size(config: dict | None = None) -> int:
    cfg = config if isinstance(config, dict) else {}
    raw = cfg.get("batch_inference_size")
    if raw is None:
        raw = os.environ.get("BACKTEST_INFERENCE_BATCH_SIZE", "512")
    try:
        size = int(raw)
    except (TypeError, ValueError):
        size = 512
    size = max(32, min(size, 4096))
    # Larger chunks only when research sim_mode can engage CUDA sessions.
    if cfg.get("batch_inference_size") is None:
        try:
            from app.services.bots.ml_onnx_runtime import (
                backtest_research_inference,
                backtest_wants_gpu,
            )

            if backtest_research_inference(cfg) and backtest_wants_gpu(cfg):
                size = max(size, min(2048, 4096))
        except Exception:
            pass
    return size


def chunked_indices(n: int, batch_size: int) -> list[tuple[int, int]]:
    """Inclusive-exclusive (start, end) slices covering ``range(n)``."""
    if n <= 0:
        return []
    bs = max(1, int(batch_size))
    return [(i, min(i + bs, n)) for i in range(0, n, bs)]


def run_predict_chunks(
    predict_fn: Callable[[np.ndarray], np.ndarray | None],
    matrix: np.ndarray,
    *,
    batch_size: int = 512,
    cancel_cb: Any | None = None,
) -> list[Any]:
    """Apply ``predict_fn`` to row-chunks of ``matrix``; flatten results.

    ``predict_fn`` receives a contiguous ``(B, ...)`` array and should return
    an array-like of length B (or None on failure for the whole chunk).
    """
    n = int(matrix.shape[0]) if matrix is not None else 0
    out: list[Any] = [None] * n
    for start, end in chunked_indices(n, batch_size):
        if cancel_cb is not None and cancel_cb():
            raise InterruptedError("ml_batch_cancel_requested")
        chunk = matrix[start:end]
        try:
            preds = predict_fn(chunk)
        except Exception as exc:
            logger.warning("Batch predict chunk [%s:%s] failed: %s", start, end, exc)
            continue
        if preds is None:
            continue
        for j, pred in enumerate(preds):
            out[start + j] = pred
    return out


def stack_sliding_windows(feat_matrix: np.ndarray, lookback: int) -> tuple[np.ndarray, np.ndarray]:
    """Build ``(N_valid, lookback, F)`` windows and their end-bar indices.

    Window ending at index ``i`` uses rows ``[i-lookback+1, i]`` inclusive.
    Prefer :func:`iter_sliding_window_batches` for long backtests to cap RSS.
    """
    n = int(feat_matrix.shape[0]) if feat_matrix is not None else 0
    lb = max(1, int(lookback))
    n_feat = int(feat_matrix.shape[1]) if n and feat_matrix.ndim > 1 else 0
    if n < lb:
        return np.zeros((0, lb, n_feat), dtype=np.float32), np.zeros(0, dtype=np.int64)
    count = n - lb + 1
    # As_strided would be faster but riskier with non-C layouts; copy is fine for BT.
    windows = np.stack(
        [feat_matrix[i : i + lb] for i in range(count)],
        axis=0,
    ).astype(np.float32, copy=False)
    ends = np.arange(lb - 1, n, dtype=np.int64)
    return windows, ends


def iter_sliding_window_batches(
    feat_matrix: np.ndarray,
    lookback: int,
    batch_size: int = 512,
):
    """Yield ``(windows, end_indices)`` chunks to avoid materializing all windows.

    Same indexing as :func:`stack_sliding_windows`, but peak memory is
    ``O(batch_size * lookback * F)`` instead of ``O(N * lookback * F)``.
    """
    n = int(feat_matrix.shape[0]) if feat_matrix is not None else 0
    lb = max(1, int(lookback))
    if n < lb:
        return
    count = n - lb + 1
    for start, end in chunked_indices(count, batch_size):
        windows = np.stack(
            [feat_matrix[i : i + lb] for i in range(start, end)],
            axis=0,
        ).astype(np.float32, copy=False)
        ends = np.arange(lb - 1 + start, lb - 1 + end, dtype=np.int64)
        yield windows, ends


def rows_from_df_slice(df, start_i: int, *, symbol: str = "") -> list[dict]:
    """Materialize eval rows (``to_dict('records')`` — faster than per-iloc loop)."""
    sym = str(symbol or "").upper()
    start = int(start_i)
    if start <= 0:
        chunk = df
    else:
        chunk = df.iloc[start:]
    if hasattr(chunk, "to_dict"):
        rows = chunk.to_dict("records")
    else:
        rows = [df.iloc[i].to_dict() for i in range(start, len(df))]
    if sym:
        for row in rows:
            row["_symbol"] = sym
    return rows


def try_precompute_signals(
    strategy: Any,
    rows: Sequence[dict],
    *,
    strategy_key: str = "",
    config: dict | None = None,
    cancel_cb: Any | None = None,
    progress_cb: Any | None = None,
) -> list[dict] | None:
    """Call strategy.precompute_backtest_signals when enabled; else None."""
    if not rows:
        return None
    if not batch_inference_enabled(strategy_key, config):
        return None
    fn = getattr(strategy, "precompute_backtest_signals", None)
    if not callable(fn):
        return None
    try:
        result = fn(list(rows), cancel_cb=cancel_cb, progress_cb=progress_cb)
    except TypeError:
        # Older strategy signatures without progress_cb.
        try:
            result = fn(list(rows), cancel_cb=cancel_cb)
        except InterruptedError:
            raise
        except Exception as exc:
            logger.warning(
                "Batch precompute failed for %s — falling back to per-bar evaluate: %s",
                strategy_key or type(strategy).__name__,
                exc,
            )
            return None
    except InterruptedError:
        raise
    except Exception as exc:
        logger.warning(
            "Batch precompute failed for %s — falling back to per-bar evaluate: %s",
            strategy_key or type(strategy).__name__,
            exc,
        )
        return None
    if not isinstance(result, list) or len(result) != len(rows):
        logger.warning(
            "Batch precompute length mismatch for %s (%s vs %s) — per-bar fallback",
            strategy_key or type(strategy).__name__,
            len(result) if isinstance(result, list) else type(result),
            len(rows),
        )
        return None
    return result


def try_precompute_signals_from_df(
    strategy: Any,
    df,
    start_i: int,
    *,
    symbol: str = "",
    strategy_key: str = "",
    config: dict | None = None,
    cancel_cb: Any | None = None,
    progress_cb: Any | None = None,
) -> list[dict] | None:
    """Prefer DataFrame-aware precompute; else materialize records once."""
    if df is None or len(df) <= int(start_i):
        return None
    if not batch_inference_enabled(strategy_key, config):
        return None
    fn_df = getattr(strategy, "precompute_backtest_signals_df", None)
    if callable(fn_df):
        try:
            result = fn_df(
                df,
                int(start_i),
                symbol=str(symbol or "").upper(),
                cancel_cb=cancel_cb,
                progress_cb=progress_cb,
            )
        except TypeError:
            result = None
        except InterruptedError:
            raise
        except Exception as exc:
            logger.warning(
                "DF batch precompute failed for %s — trying row path: %s",
                strategy_key or type(strategy).__name__,
                exc,
            )
            result = None
        if isinstance(result, list) and len(result) == len(df) - int(start_i):
            return result
    rows = rows_from_df_slice(df, start_i, symbol=symbol)
    return try_precompute_signals(
        strategy,
        rows,
        strategy_key=strategy_key,
        config=config,
        cancel_cb=cancel_cb,
        progress_cb=progress_cb,
    )
