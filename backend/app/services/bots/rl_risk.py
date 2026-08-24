"""RL_PPO_AGENT risk, cost, and deploy-payoff constants.

Live ADA bots were sized off 2%/3% of a $3k book (~$60 hole) while the
policy already emitted ATR×1.5. Train/serve used a 10 bps toy cost and no
stops. These helpers keep live sizing, TradingEnv, and the deploy gate on
the same ATR + $15–25 + real-fee contract.
"""

from __future__ import annotations

import math
from typing import Any

RL_STRATEGY = "RL_PPO_AGENT"

ATR_STOP_MULT = 1.5
TAKE_PROFIT_R = 1.5  # 1.5R → ATR × 2.25 from entry
RISK_USD_MIN = 15.0
RISK_USD_MAX = 25.0
RISK_USD_DEFAULT = 20.0
MIN_ENT_COEF = 0.01
DEFAULT_FEE_BPS = 10.0
DEFAULT_SLIPPAGE_BPS = 5.0
PREFERRED_TRAIN_TIMEFRAME = "5m"
MIN_PROFIT_FACTOR = 1.3
MIN_EPISODES = 1
# 4-way softmax → uniform ≈0.25; live/OOS require a real edge above chance.
DEFAULT_MIN_CONFIDENCE = 0.40
DEFAULT_SCALER_FIT_FRAC = 0.8
# Path reward: Δlog-equity − λ·Δdrawdown − turnover (R-multiple is aux only).
REWARD_DD_LAMBDA = 0.5
REWARD_TURNOVER_COEF = 0.0002


def is_rl_strategy(strategy: str | None) -> bool:
    return str(strategy or "").strip().upper() == RL_STRATEGY


def atr_stop_distance(atr: float, *, mult: float = ATR_STOP_MULT) -> float | None:
    try:
        val = float(atr)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    return val * float(mult)


def stop_take_prices(
    side: str,
    entry: float,
    atr: float,
    *,
    stop_mult: float = ATR_STOP_MULT,
    take_profit_r: float = TAKE_PROFIT_R,
) -> tuple[float | None, float | None, float | None]:
    """Return (stop_distance, stop_price, take_profit_price) for an ATR×mult stop."""
    dist = atr_stop_distance(atr, mult=stop_mult)
    try:
        px = float(entry)
    except (TypeError, ValueError):
        px = 0.0
    if dist is None or px <= 0:
        return dist, None, None
    side_u = str(side or "").upper()
    tp_dist = dist * float(take_profit_r)
    if side_u in ("BUY", "LONG"):
        return dist, px - dist, px + tp_dist
    if side_u in ("SELL", "SHORT"):
        return dist, px + dist, px - tp_dist
    return dist, None, None


def clamp_risk_usd(value: float | None) -> float:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        raw = RISK_USD_DEFAULT
    if raw <= 0:
        raw = RISK_USD_DEFAULT
    return max(RISK_USD_MIN, min(RISK_USD_MAX, raw))


def resolve_rl_risk_usd(config: dict | None) -> float:
    cfg = config or {}
    raw = cfg.get("risk_per_trade_usd")
    if raw is None:
        raw = cfg.get("max_risk_usd")
    return clamp_risk_usd(raw if raw is not None else RISK_USD_DEFAULT)


def resolve_rl_costs(config: dict | None) -> tuple[float, float]:
    """Fee/slippage for RL train + OOS. Missing keys get real defaults (not 0)."""
    cfg = config or {}
    fee = cfg.get("fee_bps")
    slip = cfg.get("slippage_bps")
    try:
        fee_f = DEFAULT_FEE_BPS if fee is None else max(0.0, float(fee))
    except (TypeError, ValueError):
        fee_f = DEFAULT_FEE_BPS
    try:
        slip_f = DEFAULT_SLIPPAGE_BPS if slip is None else max(0.0, float(slip))
    except (TypeError, ValueError):
        slip_f = DEFAULT_SLIPPAGE_BPS
    return fee_f, slip_f


def costs_are_applied(config: dict | None) -> bool:
    """True when fee/slippage were explicitly set and are non-zero.

    Unlike ``resolve_rl_costs``, this does NOT substitute defaults — a missing
    key means the run was configured without real costs, which the deploy gate
    treats as costless.
    """
    cfg = config or {}
    fee = cfg.get("fee_bps")
    slip = cfg.get("slippage_bps")
    try:
        fee_f = max(0.0, float(fee)) if fee is not None else 0.0
    except (TypeError, ValueError):
        fee_f = 0.0
    try:
        slip_f = max(0.0, float(slip)) if slip is not None else 0.0
    except (TypeError, ValueError):
        slip_f = 0.0
    return fee_f > 0 or slip_f > 0


def clamp_ent_coef(value: Any) -> float:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        raw = MIN_ENT_COEF
    if raw < MIN_ENT_COEF:
        return MIN_ENT_COEF
    return raw


def resolve_atr_stop_mult(config: dict | None) -> float:
    cfg = config or {}
    raw = cfg.get("atr_stop_mult")
    if raw is None:
        raw = cfg.get("chandelier_multiplier")
    try:
        val = float(raw) if raw is not None else ATR_STOP_MULT
    except (TypeError, ValueError):
        val = ATR_STOP_MULT
    return max(0.5, min(5.0, val))


def resolve_take_profit_r(config: dict | None) -> float:
    cfg = config or {}
    raw = cfg.get("take_profit_r")
    try:
        val = float(raw) if raw is not None else TAKE_PROFIT_R
    except (TypeError, ValueError):
        val = TAKE_PROFIT_R
    return max(0.5, min(5.0, val))


def uses_percent_stops(config: dict | None) -> bool:
    """RL uses ATR exits unless the operator explicitly opts back into % stops."""
    return bool((config or {}).get("rl_percent_stops"))


def apply_rl_live_defaults(config: dict | None) -> dict:
    """Persist ATR×1.5 + $15–25 risk; drop inherited 2%/3% percent stops."""
    cfg = dict(config or {})
    if uses_percent_stops(cfg):
        return cfg
    cfg.pop("trailing_stop_percent", None)
    cfg.pop("stop_loss_percent", None)
    cfg.pop("take_profit_percent", None)
    cfg.setdefault("atr_stop_mult", ATR_STOP_MULT)
    cfg.setdefault("take_profit_r", TAKE_PROFIT_R)
    cfg.setdefault("chandelier_stop_enabled", True)
    cfg.setdefault("chandelier_multiplier", ATR_STOP_MULT)
    cfg.setdefault("tp_mode", "strategy")
    cfg["risk_per_trade_usd"] = resolve_rl_risk_usd(cfg)
    fee, slip = resolve_rl_costs(cfg)
    cfg.setdefault("fee_bps", fee)
    cfg.setdefault("slippage_bps", slip)
    cfg.setdefault("paper_first", True)
    cfg["rl_percent_stops"] = False
    return cfg


def payoff_passes(
    *,
    avg_win: float | None,
    avg_loss: float | None,
    profit_factor: float | None,
    min_pf: float = MIN_PROFIT_FACTOR,
) -> tuple[bool, str]:
    """avg win ≥ avg loss (magnitudes) and PF > 1.3."""
    try:
        win = float(avg_win or 0)
    except (TypeError, ValueError):
        win = 0.0
    try:
        loss = abs(float(avg_loss or 0))
    except (TypeError, ValueError):
        loss = 0.0
    try:
        pf = float(profit_factor) if profit_factor is not None else None
    except (TypeError, ValueError):
        pf = None

    if win <= 0 and loss <= 0:
        return False, "no closed wins/losses to score payoff"
    if loss > 0 and win + 1e-12 < loss:
        return False, f"avg win ${win:.2f} < avg loss ${loss:.2f}"
    if pf is None:
        return False, "profit factor missing"
    if pf <= min_pf:
        return False, f"profit factor {pf:.2f} ≤ {min_pf:.2f}"
    return True, f"avg win ${win:.2f} ≥ avg loss ${loss:.2f}, PF {pf:.2f}"


def resolve_min_confidence(config: dict | None) -> float:
    cfg = config or {}
    raw = cfg.get("min_confidence")
    try:
        val = float(raw) if raw is not None else DEFAULT_MIN_CONFIDENCE
    except (TypeError, ValueError):
        val = DEFAULT_MIN_CONFIDENCE
    return max(0.0, min(0.95, val))


def greedy_serve_action(logits, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> int:
    """Argmax with a BUY/SELL confidence floor — CLOSE is never thresholded.

    Train still samples Categorical. Live ONNX and walk-forward OOS must use
    this rule so the deployed policy matches the one we score.
    """
    import numpy as np

    arr = np.asarray(logits, dtype=np.float64).reshape(-1)
    if arr.size == 0:
        return 0
    shifted = arr - float(arr.max())
    probs = np.exp(shifted)
    total = float(probs.sum())
    if total <= 0 or not np.isfinite(total):
        return 0
    probs = probs / total
    action = int(np.argmax(probs))
    # 1=BUY, 2=SELL — match rl_trading_env without importing it.
    if action in (1, 2) and float(probs[action]) < float(min_confidence):
        return 0
    return action


def log_equity_delta(prev_eq: float, eq: float) -> float:
    prev = max(float(prev_eq), 1e-12)
    cur = max(float(eq), 1e-12)
    return float(math.log(cur / prev))


def drawdown_increase(peak_before: float, prev_eq: float, eq: float) -> float:
    peak_before = max(float(peak_before), 1e-12)
    peak_after = max(peak_before, float(eq))
    prev_dd = max(0.0, (peak_before - float(prev_eq)) / peak_before)
    now_dd = max(0.0, (peak_after - float(eq)) / peak_after) if peak_after > 1e-12 else 0.0
    return max(0.0, now_dd - prev_dd)


def path_step_reward(
    prev_eq: float,
    eq: float,
    peak_before: float,
    *,
    traded: bool,
    dd_lambda: float = REWARD_DD_LAMBDA,
    turnover_coef: float = REWARD_TURNOVER_COEF,
) -> float:
    """Primary env reward: log-equity path minus incremental DD and churn."""
    reward = log_equity_delta(prev_eq, eq)
    reward -= float(dd_lambda) * drawdown_increase(peak_before, prev_eq, eq)
    if traded:
        reward -= float(turnover_coef)
    return float(reward)


def live_close_log_reward(pnl: float | None, entry_price: float | None, quantity: float | None) -> float:
    """Map a live close onto the same Δlog-equity units the env trains on."""
    if pnl is None or not entry_price or not quantity:
        return 0.0
    try:
        notional = abs(float(entry_price) * float(quantity))
        frac = float(pnl) / notional if notional > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0
    return float(math.log(max(1e-12, 1.0 + frac)))


def trade_payoff_stats(pnls: list[float]) -> dict[str, float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        pf = round(gross_win / gross_loss, 4)
    elif gross_win > 0:
        pf = 99.0
    else:
        pf = 0.0
    return {
        "n_wins": len(wins),
        "n_losses": len(losses),
        "avg_win": round(sum(wins) / len(wins), 6) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 6) if losses else 0.0,
        "profit_factor": pf,
        "closed_trades": len(pnls),
    }
