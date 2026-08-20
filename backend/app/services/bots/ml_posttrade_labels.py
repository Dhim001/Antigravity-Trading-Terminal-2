"""Closed-loop feature feedback — post-trade outcomes → training labels.

Implements AI-FT-PTL-001 §4.2. After each closed bot trade, the post-trade
learner writes a ``posttrade_labels`` row capturing the outcome class, MAE/MFE
excursion, execution shortfall, and regime. On the next retrain, the
triple-barrier labeller reads the trailing window of these rows and:

  1. Scales barrier widths per-symbol from the median excursion (MAE/MFE).
  2. Down-weights samples that fall inside hostile-regime windows
     (``regime_mismatch`` lessons).
  3. Excludes bars whose mean execution shortfall exceeds a threshold
     (unreliable labels).

All functions are defensive: on any DB error they return neutral defaults so
training never breaks because of the feedback loop.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from app.config import (
    POSTTRADE_LABELS_BARRIER_SCALE_MAX,
    POSTTRADE_LABELS_BARRIER_SCALE_MIN,
    POSTTRADE_LABELS_ENABLED,
    POSTTRADE_LABELS_HOSTILE_WEIGHT,
    POSTTRADE_LABELS_LOOKBACK_DAYS,
    POSTTRADE_LABELS_MAX_IS_BPS,
)
from app.database import get_connection

logger = logging.getLogger(__name__)

# Outcome classes that mark a hostile regime for the symbol.
_HOSTILE_OUTCOMES = frozenset({"regime_mismatch"})

# Minimum samples before we trust the excursion median enough to scale barriers.
_MIN_EXCURSION_SAMPLES = 6


def _utcnow_naive() -> str:
    """Naive-UTC timestamp string matching CURRENT_TIMESTAMP's format."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def record_posttrade_label(
    *,
    bot_id: str,
    symbol: str,
    bar_time: int | None,
    outcome_class: str | None,
    mae: float | None,
    mfe: float | None,
    execution_shortfall_bps: float | None,
    regime: str | None,
) -> None:
    """Persist one post-trade label row. Never raises."""
    if not POSTTRADE_LABELS_ENABLED:
        return
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO posttrade_labels
            (bot_id, symbol, bar_time, outcome_class, mae, mfe,
             execution_shortfall_bps, regime, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bot_id,
                str(symbol).upper(),
                bar_time,
                outcome_class,
                mae,
                mfe,
                execution_shortfall_bps,
                regime,
                _utcnow_naive(),
            ),
        )
        conn.commit()
    except Exception:
        logger.debug("record_posttrade_label failed for %s", symbol, exc_info=True)
    finally:
        # A failed INSERT must not leak the pooled connection.
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _fetch_recent_rows(symbol: str, lookback_days: int) -> list[dict[str, Any]]:
    cutoff_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=max(1, lookback_days)
    )
    cutoff = cutoff_dt.isoformat(sep=" ")
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT bar_time, outcome_class, mae, mfe, execution_shortfall_bps, regime
            FROM posttrade_labels
            WHERE symbol = ? AND created_at >= ?
            ORDER BY created_at DESC
            """,
            (str(symbol).upper(), cutoff),
        )
        rows = cursor.fetchall()
    except Exception:
        logger.debug("posttrade_labels fetch failed for %s", symbol, exc_info=True)
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    out: list[dict[str, Any]] = []
    for row in rows or []:
        if isinstance(row, dict):
            out.append(dict(row))
        else:
            out.append({
                "bar_time": row[0],
                "outcome_class": row[1],
                "mae": row[2],
                "mfe": row[3],
                "execution_shortfall_bps": row[4],
                "regime": row[5],
            })
    return out


def barrier_width_scale(symbol: str, *, lookback_days: int | None = None) -> float:
    """Scale factor for triple-barrier ATR multipliers from median excursion.

    Returns 1.0 (neutral) when there is insufficient data or the feature is off.
    A value > 1 widens barriers (price moved more than the default barrier
    expected); < 1 tightens them.
    """
    if not POSTTRADE_LABELS_ENABLED:
        return 1.0
    days = lookback_days or POSTTRADE_LABELS_LOOKBACK_DAYS
    rows = _fetch_recent_rows(symbol, days)
    excursions: list[float] = []
    for r in rows:
        mae = r.get("mae")
        mfe = r.get("mfe")
        vals = [abs(float(v)) for v in (mae, mfe) if v is not None]
        if vals:
            excursions.append(max(vals))
    if len(excursions) < _MIN_EXCURSION_SAMPLES:
        return 1.0

    med = median(excursions)
    if med <= 0:
        return 1.0
    # Median excursion is a % move; the default barrier is ~2×ATR. We map the
    # median excursion to a scale relative to a 2% reference move, clamped.
    scale = med / 2.0
    return round(max(POSTTRADE_LABELS_BARRIER_SCALE_MIN, min(POSTTRADE_LABELS_BARRIER_SCALE_MAX, scale)), 4)


def hostile_regime_windows(symbol: str, *, lookback_days: int | None = None) -> list[tuple[int, int]]:
    """Return [start_bar_time, end_bar_time] windows of hostile regimes.

    Each ``regime_mismatch`` row marks a ±1-day window around its bar_time as
    hostile. Returns an empty list when disabled / no data.
    """
    if not POSTTRADE_LABELS_ENABLED:
        return []
    days = lookback_days or POSTTRADE_LABELS_LOOKBACK_DAYS
    rows = _fetch_recent_rows(symbol, days)
    windows: list[tuple[int, int]] = []
    for r in rows:
        if str(r.get("outcome_class") or "") not in _HOSTILE_OUTCOMES:
            continue
        bt = r.get("bar_time")
        if bt is None:
            continue
        try:
            bt = int(bt)
        except (TypeError, ValueError):
            continue
        day = 86_400
        windows.append((bt - day, bt + day))
    return windows


def mean_execution_shortfall_bps(symbol: str, *, lookback_days: int | None = None) -> float | None:
    """Mean execution shortfall (bps) over the trailing window, or None."""
    if not POSTTRADE_LABELS_ENABLED:
        return None
    days = lookback_days or POSTTRADE_LABELS_LOOKBACK_DAYS
    rows = _fetch_recent_rows(symbol, days)
    vals = [
        float(r["execution_shortfall_bps"])
        for r in rows
        if r.get("execution_shortfall_bps") is not None
    ]
    if not vals:
        return None
    return sum(vals) / len(vals)


def tp_is_adjusted_upper_mult(
    symbol: str,
    base_upper_mult: float,
    candles: list[dict],
) -> float:
    """Widen the take-profit barrier by the expected IS cost (P1 #6).

    Conservative labelling: the upper (profit) barrier is padded by the
    trailing mean implementation shortfall, converted from bps-of-price into
    ATR multiples using the series' median ATR%. Returns ``base_upper_mult``
    unchanged when IS or ATR data is unavailable.
    """
    try:
        from app.config import TCA_REWARD_FEEDBACK_ENABLED, TCA_REWARD_LOOKBACK_DAYS

        if not TCA_REWARD_FEEDBACK_ENABLED:
            return base_upper_mult
        from app.services.bots.execution_tca import mean_is_bps_for_symbol

        is_bps = mean_is_bps_for_symbol(symbol, lookback_days=TCA_REWARD_LOOKBACK_DAYS)
        if not is_bps or is_bps <= 0:
            return base_upper_mult

        atr_fracs: list[float] = []
        for c in candles[-500:]:
            atr = c.get("ATR_14") or c.get("ATRr_14") or c.get("atr")
            close = c.get("close")
            try:
                a, p = float(atr), float(close)
            except (TypeError, ValueError):
                continue
            if a > 0 and p > 0:
                atr_fracs.append(a / p)
        if not atr_fracs:
            return base_upper_mult
        med_atr_frac = median(atr_fracs)
        if med_atr_frac <= 0:
            return base_upper_mult
        pad_mult = (float(is_bps) / 10_000.0) / med_atr_frac
        return round(base_upper_mult + min(pad_mult, 1.0), 4)
    except Exception:
        logger.debug("tp_is_adjusted_upper_mult failed for %s", symbol, exc_info=True)
        return base_upper_mult


def apply_posttrade_feedback(
    candles: list[dict],
    labels: list[dict],
    *,
    symbol: str,
) -> list[dict]:
    """Apply closed-loop feedback to triple-barrier labels for ``symbol``.

    - Drops labels on bars whose execution shortfall exceeds the threshold.
    - Down-weights (via ``uniqueness``) labels inside hostile-regime windows.

    Returns the (possibly modified) ``labels`` list. Never raises.
    """
    if not POSTTRADE_LABELS_ENABLED or not labels:
        return labels
    try:
        mean_is = mean_execution_shortfall_bps(symbol)
        hostile = hostile_regime_windows(symbol)
        if mean_is is None and not hostile:
            return labels

        exclude_unreliable = (
            mean_is is not None and mean_is > POSTTRADE_LABELS_MAX_IS_BPS
        )
        hostile_w = POSTTRADE_LABELS_HOSTILE_WEIGHT

        out: list[dict] = []
        for lab in labels:
            bt = lab.get("time")
            try:
                bt_int = int(bt) if bt is not None else None
            except (TypeError, ValueError):
                bt_int = None

            if exclude_unreliable:
                # Whole symbol's recent execution is unreliable → drop the label.
                lab = dict(lab)
                lab["label"] = 0
                lab["label_name"] = "NONE"
                lab["barrier_hit"] = "invalid"
                out.append(lab)
                continue

            if bt_int is not None and hostile:
                in_hostile = any(start <= bt_int <= end for start, end in hostile)
                if in_hostile:
                    lab = dict(lab)
                    lab["uniqueness"] = round(
                        float(lab.get("uniqueness", 1.0)) * hostile_w, 4
                    )
            out.append(lab)
        return out
    except Exception:
        logger.debug("apply_posttrade_feedback failed for %s", symbol, exc_info=True)
        return labels
