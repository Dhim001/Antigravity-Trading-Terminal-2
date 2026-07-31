"""Backtest cost calibration from measured live execution (Phase 2).

Turns the TCA measurements in ``execution_quality_log`` into suggested
backtest cost parameters, champion-challenger style:

  * measured exec cost (avg spread + impact bps) → ``suggested_slippage_bps``
  * measured decision-to-arrival drift (avg delay bps, floored at 0)
    → ``suggested_latency_bps`` (maps to ``CostModel.latency_slippage_bps``)

Suggestions are recomputed nightly (server startup retention pass) and on
demand; nothing applies automatically — the operator approves via
``apply_cost_suggestion`` (one-click in the UI), which stamps ``applied_at``
and returns the config patch.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app import config
from app.database import get_connection

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def compute_cost_suggestions(
    *,
    min_samples: int | None = None,
    safety_factor: float | None = None,
) -> list[dict[str, Any]]:
    """Recompute per-symbol suggestions from execution_quality_log.

    Symbols below the sample floor are returned with ``insufficient_data``
    and are not persisted. Existing ``applied_at`` stamps survive recompute.
    Never raises — a calibration failure must not break the caller.
    """
    min_n = int(min_samples if min_samples is not None else config.EXEC_CAL_MIN_SAMPLES)
    safety = float(safety_factor if safety_factor is not None else config.EXEC_CAL_SAFETY_FACTOR)
    lo = float(config.EXEC_CAL_MIN_BPS)
    hi = float(config.EXEC_CAL_MAX_BPS)
    out: list[dict[str, Any]] = []
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT symbol, COUNT(*) AS n,
                   AVG(spread_bps) AS avg_spread_bps,
                   AVG(impact_bps) AS avg_impact_bps,
                   AVG(delay_bps) AS avg_delay_bps
            FROM execution_quality_log
            WHERE filled_qty > 0 AND avg_fill_price IS NOT NULL
            GROUP BY symbol
            ORDER BY n DESC
            """
        )
        rows = [dict(r) for r in cursor.fetchall()]

        now = _utcnow_iso()
        for row in rows:
            symbol = row.get("symbol") or "UNKNOWN"
            n = int(row.get("n") or 0)
            spread = float(row.get("avg_spread_bps") or 0.0)
            impact = float(row.get("avg_impact_bps") or 0.0)
            delay = float(row.get("avg_delay_bps") or 0.0)
            measured_exec = spread + impact
            entry = {
                "symbol": symbol,
                "sample_size": n,
                "measured_exec_bps": round(measured_exec, 2),
                "measured_delay_bps": round(delay, 2),
            }
            if n < min_n:
                entry["insufficient_data"] = True
                out.append(entry)
                continue
            suggested_slip = round(_clamp(measured_exec * safety, lo, hi), 2)
            suggested_latency = round(_clamp(max(0.0, delay), 0.0, hi), 2)
            entry.update(
                {
                    "suggested_slippage_bps": suggested_slip,
                    "suggested_latency_bps": suggested_latency,
                    "computed_at": now,
                }
            )
            # Upsert — on conflict only the measurement columns update, so a
            # previous operator approval (applied_at) survives recompute.
            # ON CONFLICT DO UPDATE is supported by sqlite ≥3.24 and Postgres.
            cursor.execute(
                """
                INSERT INTO execution_cost_calibration
                (symbol, sample_size, measured_exec_bps, measured_delay_bps,
                 suggested_slippage_bps, suggested_latency_bps, computed_at, applied_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT (symbol) DO UPDATE SET
                    sample_size = excluded.sample_size,
                    measured_exec_bps = excluded.measured_exec_bps,
                    measured_delay_bps = excluded.measured_delay_bps,
                    suggested_slippage_bps = excluded.suggested_slippage_bps,
                    suggested_latency_bps = excluded.suggested_latency_bps,
                    computed_at = excluded.computed_at
                """,
                (
                    symbol, n, entry["measured_exec_bps"], entry["measured_delay_bps"],
                    suggested_slip, suggested_latency, now,
                ),
            )
            out.append(entry)
        conn.commit()
        conn.close()
    except Exception:
        logger.debug("compute_cost_suggestions failed", exc_info=True)
    return out


def list_cost_suggestions() -> list[dict[str, Any]]:
    """Persisted suggestions (most recently computed), applied flag included."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT symbol, sample_size, measured_exec_bps, measured_delay_bps,
                   suggested_slippage_bps, suggested_latency_bps,
                   computed_at, applied_at
            FROM execution_cost_calibration
            ORDER BY sample_size DESC
            """
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        for row in rows:
            row["applied"] = row.get("applied_at") is not None
        return rows
    except Exception:
        logger.debug("list_cost_suggestions failed", exc_info=True)
        return []


def apply_cost_suggestion(symbol: str | None) -> dict[str, Any] | None:
    """Operator approval: stamp applied_at and return the config patch.

    Returns None when no suggestion exists for the symbol.
    """
    if not symbol:
        return None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT symbol, suggested_slippage_bps, suggested_latency_bps
            FROM execution_cost_calibration WHERE symbol = ?
            """,
            (symbol,),
        )
        row = cursor.fetchone()
        if row is None:
            conn.close()
            return None
        row = dict(row)
        now = _utcnow_iso()
        cursor.execute(
            "UPDATE execution_cost_calibration SET applied_at = ? WHERE symbol = ?",
            (now, symbol),
        )
        conn.commit()
        conn.close()
        return {
            "symbol": row["symbol"],
            "slippage_bps": row["suggested_slippage_bps"],
            "latency_slippage_bps": row["suggested_latency_bps"],
            "applied_at": now,
        }
    except Exception:
        logger.debug("apply_cost_suggestion failed for %s", symbol, exc_info=True)
        return None
