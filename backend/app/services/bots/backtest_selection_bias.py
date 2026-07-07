"""Selection-bias metrics for optimization — WFE, DSR, trades-per-parameter."""

from __future__ import annotations

import math
from typing import Any

from app.services.bots.indicators import MIN_WARMUP_BARS

OOS_WARMUP_BARS = MIN_WARMUP_BARS
MIN_TRADES_PER_PARAM = 30
WF_MIN_TRADES_PER_PARAM = 5
DEFAULT_MIN_WFE = 0.5


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Approximate inverse normal CDF (Acklam's rational approximation)."""
    if p <= 0:
        return -10.0
    if p >= 1:
        return 10.0
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285084469e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580464365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709091636e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (
            (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
            / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        )
    if phigh < p:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(
            (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
            / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        )
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


def expected_max_sharpe(
    num_trials: int,
    num_observations: int,
    *,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Expected maximum Sharpe under null (Bailey & López de Prado, selection bias)."""
    if num_trials <= 1 or num_observations < 2:
        return 0.0
    euler = 0.5772156649015329
    z1 = _norm_ppf(1.0 - 1.0 / num_trials)
    z2 = _norm_ppf(1.0 - 1.0 / (num_trials * math.e))
    emax = ((1.0 - euler) * z1 + euler * z2) / math.sqrt(max(num_observations - 1, 1))
    _ = skew, kurtosis  # reserved for non-normal extensions
    return emax


def deflated_sharpe_ratio(
    sharpe: float | None,
    *,
    num_trials: int,
    num_observations: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float | None:
    """Probability that estimated Sharpe exceeds selection-bias-adjusted null."""
    if sharpe is None or num_trials < 1 or num_observations < 3:
        return None
    sr = float(sharpe)
    sr0 = expected_max_sharpe(num_trials, num_observations, skew=skew, kurtosis=kurtosis)
    denom_inner = 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr * sr
    if denom_inner <= 1e-12:
        return None
    z = (sr - sr0) * math.sqrt(num_observations - 1) / math.sqrt(denom_inner)
    return round(_norm_cdf(z), 4)


def walk_forward_efficiency(
    mean_in_sample: float | None,
    mean_out_of_sample: float | None,
) -> float | None:
    """WFE = mean(OOS objective) / mean(IS objective). None when IS mean <= 0."""
    if mean_in_sample is None or mean_out_of_sample is None:
        return None
    is_mean = float(mean_in_sample)
    oos_mean = float(mean_out_of_sample)
    if is_mean <= 0:
        return None
    return round(oos_mean / is_mean, 4)


def effective_min_trades(
    num_swept_params: int,
    *,
    base_min: int = 0,
    trades_per_param: int = MIN_TRADES_PER_PARAM,
) -> int:
    """Enforce min_trades >= trades_per_param × num_swept_params."""
    base = max(0, int(base_min or 0))
    params = max(1, int(num_swept_params or 1))
    return max(base, trades_per_param * params)


def build_oos_candles_with_warmup(
    train: list[dict],
    test: list[dict],
    *,
    warmup_bars: int = OOS_WARMUP_BARS,
) -> tuple[list[dict], int | None]:
    """Prepend IS tail bars for indicator warm-up; return score window start time."""
    if not test:
        return [], None
    score_from = test[0].get("time")
    if score_from is None:
        return list(test), None
    prefix_len = max(0, min(int(warmup_bars), len(train)))
    prefix = train[-prefix_len:] if prefix_len else []
    return prefix + list(test), int(score_from)


def apply_score_window(result: dict, score_from_time: int) -> dict:
    """Recompute PnL/trades/equity using only bars at or after score_from_time."""
    if not result or result.get("error") or score_from_time is None:
        return result

    out = dict(result)
    trades = list(out.get("trades") or [])

    filtered_closed: list[dict] = []
    # Pair entries → exits sequentially (stack-like) to avoid cross-matching
    pending_entry_time: int | None = None
    for t in trades:
        if not t.get("is_exit"):
            # Track the most recent entry time
            entry_t = int(t.get("time") or 0)
            if entry_t >= score_from_time:
                pending_entry_time = entry_t
            else:
                pending_entry_time = None  # entry is pre-OOS
            continue
        # This is an exit — only include if its matched entry was in OOS
        exit_t = int(t.get("time") or 0)
        if exit_t < score_from_time:
            pending_entry_time = None
            continue
        if pending_entry_time is not None and pending_entry_time >= score_from_time:
            filtered_closed.append(t)
        pending_entry_time = None  # consume the pairing
    closed = filtered_closed

    curve = [
        pt for pt in (out.get("equity_curve") or [])
        if int(pt.get("time") or 0) >= score_from_time
    ]
    starting_equity = float(out.get("starting_equity") or out.get("allocation") or 10_000)
    if curve:
        base_eq = float(curve[0].get("equity") or starting_equity)
        rebased = []
        for pt in curve:
            eq = float(pt.get("equity") or base_eq)
            rebased.append({
                "time": pt.get("time"),
                "equity": round(starting_equity + (eq - base_eq), 2),
            })
        curve = rebased
        ending = float(curve[-1].get("equity") or starting_equity)
        total_pnl = round(ending - starting_equity, 2)
    else:
        total_pnl = round(sum(float(t.get("pnl") or 0) for t in closed), 2)

    total_trades = len(closed)
    wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
    win_rate = (wins / total_trades * 100) if total_trades else 0.0

    summary = dict(out.get("summary") or {})
    summary["total_pnl"] = total_pnl
    summary["total_trades"] = total_trades
    summary["win_rate"] = round(win_rate, 2)
    summary["score_from_time"] = score_from_time
    summary["oos_warmup_bars"] = OOS_WARMUP_BARS

    out["summary"] = summary
    out["total_pnl"] = total_pnl
    out["trade_count"] = total_trades
    out["win_rate"] = summary["win_rate"]
    out["equity_curve"] = curve
    out["trades"] = [t for t in trades if int(t.get("time") or 0) >= score_from_time]
    meta = dict(out.get("meta") or {})
    meta["score_from_time"] = score_from_time
    meta["oos_scored"] = True
    out["meta"] = meta
    return out


def selection_bias_summary(
    *,
    fold_entries: list[dict],
    objective: str,
    num_trials: int,
    row_objective_fn,
) -> dict[str, Any]:
    """Aggregate WFE + DSR for walk-forward results."""
    is_vals: list[float] = []
    oos_vals: list[float] = []
    oos_sharpes: list[float] = []
    oos_obs = 0

    for entry in fold_entries:
        is_row = {
            "summary": (entry.get("in_sample") or {}).get("summary") or {},
            "total_pnl": (entry.get("in_sample") or {}).get("total_pnl"),
            "trade_count": (entry.get("in_sample") or {}).get("trade_count"),
        }
        oos = entry.get("out_of_sample") or {}
        oos_row = {
            "summary": oos.get("summary") or {},
            "total_pnl": oos.get("total_pnl"),
            "trade_count": oos.get("trade_count"),
        }
        is_v = row_objective_fn(is_row, objective)
        oos_v = row_objective_fn(oos_row, objective)
        if is_v > -1e17:
            is_vals.append(is_v)
        if oos_v > -1e17:
            oos_vals.append(oos_v)
        sharpe = (oos.get("summary") or {}).get("sharpe_ratio")
        if sharpe is not None:
            oos_sharpes.append(float(sharpe))
        tc = int(oos.get("trade_count") or (oos.get("summary") or {}).get("total_trades") or 0)
        oos_obs = max(oos_obs, tc)

    mean_is = sum(is_vals) / len(is_vals) if is_vals else None
    mean_oos = sum(oos_vals) / len(oos_vals) if oos_vals else None
    wfe = walk_forward_efficiency(mean_is, mean_oos)
    mean_sharpe = sum(oos_sharpes) / len(oos_sharpes) if oos_sharpes else None
    dsr = None
    if objective == "sharpe_ratio" and mean_sharpe is not None:
        obs = max(oos_obs * 10, 50)
        dsr = deflated_sharpe_ratio(
            mean_sharpe,
            num_trials=max(1, num_trials),
            num_observations=obs,
        )

    return {
        "num_trials": max(1, int(num_trials)),
        "mean_in_sample_objective": round(mean_is, 4) if mean_is is not None else None,
        "mean_out_of_sample_objective": round(mean_oos, 4) if mean_oos is not None else None,
        "walk_forward_efficiency": wfe,
        "deflated_sharpe_ratio": dsr,
        "expected_max_sharpe": (
            round(expected_max_sharpe(num_trials, max(oos_obs * 10, 50)), 4)
            if mean_sharpe is not None and num_trials > 1
            else None
        ),
        "min_wfe_threshold": DEFAULT_MIN_WFE,
    }
