"""Closed-loop decision evaluation — grades agent decisions after the fact.

Sprint 4 of the agent platform. Nothing in the pipeline used to score a
PreTradeIntel veto, a regime rotation, a post-trade config patch, or a
sentinel pause once the market had a chance to prove it right or wrong.
This module closes that loop:

1. **Register** — scan the durable decision sources (``bot_logs`` veto meta,
   ``agent_events`` rotations/patches/pauses) and insert one pending row per
   decision into ``agent_decision_outcomes``. The ``decision_key`` UNIQUE
   index makes registration idempotent.

2. **Grade** — once a decision is at least ``AGENT_EVAL_MIN_AGE_SEC`` old, a
   per-type scorer compares the counterfactual (price path / trade series
   after the decision) against the decision's intent and stores
   ``score``/``outcome``/``detail``. Decisions older than
   ``AGENT_EVAL_MAX_AGE_SEC`` that still lack data are closed out as
   ``insufficient_data`` so every decision is graded at most once.

3. **Summarize** — a rolling per-agent accuracy rollup is written to
   ``agent_eval_summary`` for the Strategy Advisor / Copilot to read
   (``advisor_confidence_weight``).

All scorers are robust to missing data: they return ``None`` (skip) and the
loop never raises.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.config import (
    AGENT_EVAL_MAX_AGE_SEC,
    AGENT_EVAL_MIN_AGE_SEC,
    AGENT_EVAL_PAUSE_HOURS,
    AGENT_EVAL_RETENTION_DAYS,
    AGENT_EVAL_TRADE_WINDOW,
    AGENT_EVAL_VETO_BARS,
)
from app.db.connection import db_session, is_postgres

logger = logging.getLogger(__name__)

DECISION_TYPES = ("veto", "rotation", "patch", "pause")

# |move %| below this is scored flat (neither right nor wrong).
_FLAT_EPS_PCT = 0.05
# Discovery scans at most this many source rows per run.
_DISCOVERY_LIMIT = 2000
# Grading pass processes at most this many pending rows per run.
_GRADE_LIMIT = 500

# Legacy debate vetoes predate structured meta: "LLM debate blocked BUY: <reason>".
_LEGACY_DEBATE_RE = re.compile(r"LLM debate blocked (BUY|SELL)\b", re.IGNORECASE)

BarProvider = Callable[[str, float, float], list[dict[str, Any]]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(raw: Any) -> float | None:
    """Coerce epoch seconds / ISO strings / SQLite CURRENT_TIMESTAMP text."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        # Millisecond epochs slip in from bar_time fields.
        return val / 1000.0 if val > 1e12 else val
    text = str(raw).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _default_bar_provider(symbol: str, from_ts: float, to_ts: float) -> list[dict[str, Any]]:
    """1m archive bars covering [from_ts, to_ts] (epoch seconds)."""
    from app.services.archive.query import query_1m

    return query_1m(str(symbol).upper(), int(from_ts), int(to_ts))


def _insert_ignore_sql() -> str:
    base = (
        "INSERT {verb} INTO agent_decision_outcomes "
        "(decision_key, decision_type, agent, bot_id, symbol, decided_at, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    if is_postgres():
        return base.format(verb="") + " ON CONFLICT (decision_key) DO NOTHING"
    return base.format(verb="OR IGNORE")


def _register_decision(
    conn,
    *,
    decision_key: str,
    decision_type: str,
    agent: str | None,
    bot_id: str | None,
    symbol: str | None,
    decided_at: float,
    detail: dict[str, Any] | None = None,
) -> bool:
    """Insert one pending decision row. Idempotent via decision_key."""
    try:
        cur = conn.cursor()
        cur.execute(
            _insert_ignore_sql(),
            (
                decision_key,
                decision_type,
                agent,
                bot_id,
                symbol,
                float(decided_at),
                json.dumps(detail or {}, default=str),
                _now_iso(),
            ),
        )
        return bool(getattr(cur, "rowcount", 0))
    except Exception as exc:
        logger.debug("decision_eval register failed for %s: %s", decision_key, exc)
        return False


def _bot_symbol_map(conn) -> dict[str, str]:
    try:
        rows = conn.cursor().execute("SELECT id, symbol FROM bots").fetchall()
    except Exception as exc:
        logger.debug("decision_eval bot symbol map failed: %s", exc)
        return {}
    out: dict[str, str] = {}
    for row in rows:
        item = _row_dict(row)
        bid = item.get("id")
        sym = item.get("symbol")
        if bid and sym:
            out[str(bid)] = str(sym).upper()
    return out


def _window_prices(
    bars: list[dict[str, Any]],
    decided_at: float,
    horizon_sec: float,
) -> tuple[float, float, int] | None:
    """(price at decision, price at decision+horizon, end bar time).

    Price-at is the close of the last bar at/before the decision; price-after
    is the close of the first bar at/after the horizon. Returns None when the
    horizon has not formed yet (caller retries next cycle).
    """
    if not bars:
        return None
    ordered = sorted(bars, key=lambda b: _parse_ts(b.get("time")) or 0.0)
    start_bar = None
    for bar in ordered:
        ts = _parse_ts(bar.get("time"))
        if ts is not None and ts <= decided_at:
            start_bar = bar
        elif ts is not None and ts > decided_at:
            break
    if start_bar is None:
        start_bar = ordered[0]
    target = decided_at + horizon_sec
    end_bar = None
    for bar in ordered:
        ts = _parse_ts(bar.get("time"))
        if ts is not None and ts >= target:
            end_bar = bar
            break
    if end_bar is None:
        return None
    try:
        start_px = float(start_bar.get("close"))
        end_px = float(end_bar.get("close"))
    except (TypeError, ValueError):
        return None
    if start_px <= 0 or end_px <= 0:
        return None
    return start_px, end_px, int(_parse_ts(end_bar.get("time")) or 0)


def _closed_pnls_around(
    bot_id: str,
    decided_at: float,
    window: int,
) -> tuple[list[float], list[float]]:
    """(up-to-N closed exit pnls before, up-to-N after) the decision time."""
    pnls_before: list[float] = []
    pnls_after: list[float] = []
    try:
        with db_session(commit=False) as conn:
            rows = conn.cursor().execute(
                """
                SELECT pnl, timestamp FROM bot_trades
                WHERE bot_id = ? AND is_exit = 1 AND pnl IS NOT NULL
                ORDER BY timestamp ASC
                """,
                (str(bot_id),),
            ).fetchall()
    except Exception as exc:
        logger.debug("decision_eval trade fetch failed for %s: %s", bot_id, exc)
        return [], []
    for row in rows:
        item = _row_dict(row)
        ts = _parse_ts(item.get("timestamp"))
        try:
            pnl = float(item.get("pnl"))
        except (TypeError, ValueError):
            continue
        if ts is None:
            continue
        if ts <= decided_at:
            pnls_before.append(pnl)
        else:
            pnls_after.append(pnl)
    return pnls_before[-window:], pnls_after[:window]


def _trade_window_score(
    row: dict[str, Any],
    *,
    window: int,
) -> tuple[float, str, dict[str, Any]] | None:
    """Shared patch/rotation grader: win rate / avg PnL delta before→after."""
    bot_id = row.get("bot_id")
    decided_at = float(row.get("decided_at") or 0.0)
    if not bot_id or decided_at <= 0:
        return None
    before, after = _closed_pnls_around(str(bot_id), decided_at, window)
    if not before or not after:
        return None

    def _stats(pnls: list[float]) -> dict[str, Any]:
        wins = sum(1 for p in pnls if p > 0)
        return {
            "n": len(pnls),
            "win_rate": round(wins / len(pnls), 4),
            "avg_pnl": round(sum(pnls) / len(pnls), 4),
            "total_pnl": round(sum(pnls), 4),
        }

    b = _stats(before)
    a = _stats(after)
    pnl_delta = round(a["avg_pnl"] - b["avg_pnl"], 4)
    win_delta = round(a["win_rate"] - b["win_rate"], 4)
    if abs(pnl_delta) < 1e-9 and abs(win_delta) < 1e-9:
        outcome = "flat"
    elif pnl_delta > 0 or (abs(pnl_delta) < 1e-9 and win_delta > 0):
        outcome = "improved"
    else:
        outcome = "degraded"
    detail = {
        "before": b,
        "after": a,
        "avg_pnl_delta": pnl_delta,
        "win_rate_delta": win_delta,
        "window": window,
    }
    return pnl_delta, outcome, detail


# ---------------------------------------------------------------------------
# Scorers — return (score, outcome, detail) or None when data is missing.
# ---------------------------------------------------------------------------


def _score_veto(
    row: dict[str, Any],
    provider: BarProvider,
) -> tuple[float, str, dict[str, Any]] | None:
    symbol = row.get("symbol")
    decided_at = float(row.get("decided_at") or 0.0)
    meta = _parse_json_dict(row.get("detail"))
    side = str(meta.get("side") or "").upper()
    if not symbol or decided_at <= 0 or side not in ("BUY", "SELL"):
        return None
    horizon_sec = max(1, AGENT_EVAL_VETO_BARS) * 60.0
    bars = provider(str(symbol), decided_at - 300.0, decided_at + horizon_sec + 300.0)
    prices = _window_prices(bars, decided_at, horizon_sec)
    if prices is None:
        return None
    price_at, price_after, end_time = prices
    # Prefer the price recorded at decision time when we have it.
    try:
        recorded = float(meta.get("price"))
        if recorded > 0:
            price_at = recorded
    except (TypeError, ValueError):
        pass
    move_pct = (price_after - price_at) / price_at * 100.0
    if abs(move_pct) < _FLAT_EPS_PCT:
        score, outcome = 0.0, "flat"
    else:
        moved_against = (side == "BUY" and move_pct < 0) or (side == "SELL" and move_pct > 0)
        score, outcome = (1.0, "correct") if moved_against else (-1.0, "wrong")
    detail = {
        **meta,
        "side": side,
        "price_at": round(price_at, 8),
        "price_after": round(price_after, 8),
        "counterfactual_move_pct": round(move_pct, 4),
        "horizon_bars": int(AGENT_EVAL_VETO_BARS),
        "evaluated_bar_time": end_time,
    }
    return score, outcome, detail


def _score_pause(
    row: dict[str, Any],
    provider: BarProvider,
) -> tuple[float, str, dict[str, Any]] | None:
    symbol = row.get("symbol")
    decided_at = float(row.get("decided_at") or 0.0)
    if not symbol or decided_at <= 0:
        return None
    horizon_sec = max(0.25, AGENT_EVAL_PAUSE_HOURS) * 3600.0
    bars = provider(str(symbol), decided_at - 300.0, decided_at + horizon_sec + 300.0)
    prices = _window_prices(bars, decided_at, horizon_sec)
    if prices is None:
        return None
    price_at, price_after, end_time = prices
    move_pct = (price_after - price_at) / price_at * 100.0
    # A pause protects a (typically long) book: further drop = money saved.
    if abs(move_pct) < _FLAT_EPS_PCT:
        score, outcome = 0.0, "flat"
    elif move_pct < 0:
        score, outcome = 1.0, "saved"
    else:
        score, outcome = -1.0, "premature"
    detail = {
        **_parse_json_dict(row.get("detail")),
        "price_at": round(price_at, 8),
        "price_after": round(price_after, 8),
        "move_pct": round(move_pct, 4),
        "horizon_hours": float(AGENT_EVAL_PAUSE_HOURS),
        "evaluated_bar_time": end_time,
    }
    return score, outcome, detail


def _score_rotation(
    row: dict[str, Any],
    provider: BarProvider,
) -> tuple[float, str, dict[str, Any]] | None:
    result = _trade_window_score(row, window=max(1, AGENT_EVAL_TRADE_WINDOW))
    if result is None:
        return None
    score, outcome, detail = result
    return score, outcome, detail


def _score_patch(
    row: dict[str, Any],
    provider: BarProvider,
) -> tuple[float, str, dict[str, Any]] | None:
    result = _trade_window_score(row, window=max(1, AGENT_EVAL_TRADE_WINDOW))
    if result is None:
        return None
    score, outcome, detail = result
    return score, outcome, detail


_SCORERS = {
    "veto": _score_veto,
    "rotation": _score_rotation,
    "patch": _score_patch,
    "pause": _score_pause,
}


# ---------------------------------------------------------------------------
# Discovery — register pending rows from the durable decision sources.
# ---------------------------------------------------------------------------


def _register_vetoes(conn, *, now: float, cutoff: float) -> int:
    """PreTradeIntel VETO / LLM debate blocks logged with structured meta."""
    try:
        rows = conn.cursor().execute(
            """
            SELECT id, bot_id, message, timestamp, meta
            FROM bot_logs
            WHERE meta IS NOT NULL OR message LIKE '%debate blocked%'
            ORDER BY id DESC
            LIMIT ?
            """,
            (_DISCOVERY_LIMIT,),
        ).fetchall()
    except Exception as exc:
        logger.debug("decision_eval veto discovery failed: %s", exc)
        return 0

    symbols = _bot_symbol_map(conn)
    registered = 0
    for row in rows:
        try:
            item = _row_dict(row)
            meta = _parse_json_dict(item.get("meta"))
            event_type = str(meta.get("event_type") or "")
            legacy_match = None
            if event_type not in ("pretrade_veto", "debate_veto"):
                # Legacy fallback: debate blocks logged before structured meta.
                legacy_match = _LEGACY_DEBATE_RE.search(str(item.get("message") or ""))
                if not legacy_match:
                    continue
                event_type = "debate_veto_legacy"
            decided_at = _parse_ts(item.get("timestamp"))
            if decided_at is None or decided_at < cutoff or decided_at > now:
                continue
            bot_id = str(item.get("bot_id") or "") or None
            symbol = str(meta.get("symbol") or "").upper() or symbols.get(bot_id or "")
            side = str(meta.get("side") or "").upper()
            if side not in ("BUY", "SELL") and legacy_match is not None:
                side = legacy_match.group(1).upper()
            if side not in ("BUY", "SELL"):
                continue
            agent = "PRETRADE_INTEL" if event_type == "pretrade_veto" else "LLM_DEBATE"
            if _register_decision(
                conn,
                decision_key=f"veto:log:{item.get('id')}",
                decision_type="veto",
                agent=agent,
                bot_id=bot_id,
                symbol=symbol or None,
                decided_at=decided_at,
                detail={
                    "source": event_type,
                    "side": side,
                    "price": meta.get("price"),
                    "bar_time": meta.get("bar_time"),
                    "vetoes": meta.get("vetoes") or [],
                    "reason": meta.get("reason") or str(item.get("message") or "")[:240],
                },
            ):
                registered += 1
        except Exception as exc:
            logger.debug("decision_eval veto row skipped: %s", exc)
    return registered


def _register_events(conn, *, now: float, cutoff: float) -> int:
    """Rotations / patches / pauses from the durable agent_events log."""
    try:
        rows = conn.cursor().execute(
            """
            SELECT id, event_type, source, bot_id, payload, ts
            FROM agent_events
            WHERE event_type IN ('REGIME_CHANGED', 'POSTTRADE_LESSON', 'BOT_PAUSED')
              AND ts >= ? AND ts <= ?
            ORDER BY ts ASC
            LIMIT ?
            """,
            (cutoff, now, _DISCOVERY_LIMIT),
        ).fetchall()
    except Exception as exc:
        logger.debug("decision_eval event discovery failed: %s", exc)
        return 0

    symbols = _bot_symbol_map(conn)
    registered = 0
    for row in rows:
        try:
            item = _row_dict(row)
            event_type = str(item.get("event_type") or "")
            event_id = item.get("id")
            payload = _parse_json_dict(item.get("payload"))
            decided_at = _parse_ts(item.get("ts"))
            if decided_at is None:
                continue
            bot_id = str(payload.get("bot_id") or item.get("bot_id") or "") or None
            agent = str(item.get("source") or "") or None

            if event_type == "REGIME_CHANGED":
                if not payload.get("new_strategy"):
                    continue
                if _register_decision(
                    conn,
                    decision_key=f"rotation:evt:{event_id}",
                    decision_type="rotation",
                    agent=agent or "REGIME_ROTATION",
                    bot_id=bot_id,
                    symbol=str(payload.get("symbol") or "").upper() or symbols.get(bot_id or ""),
                    decided_at=decided_at,
                    detail={
                        "old_strategy": payload.get("old_strategy"),
                        "new_strategy": payload.get("new_strategy"),
                        "regime": payload.get("new_regime") or payload.get("regime"),
                    },
                ):
                    registered += 1
            elif event_type == "POSTTRADE_LESSON":
                lesson = payload.get("lesson")
                lesson = lesson if isinstance(lesson, dict) else {}
                patch = lesson.get("config_patch")
                # Only applied patches are gradeable decisions.
                if not lesson.get("applied") or not patch:
                    continue
                if _register_decision(
                    conn,
                    decision_key=f"patch:evt:{event_id}",
                    decision_type="patch",
                    agent=agent or "POSTTRADE_LEARNER",
                    bot_id=bot_id,
                    symbol=str(payload.get("symbol") or "").upper() or symbols.get(bot_id or ""),
                    decided_at=decided_at,
                    detail={
                        "patch": patch,
                        "outcome_class": lesson.get("outcome_class"),
                    },
                ):
                    registered += 1
            elif event_type == "BOT_PAUSED":
                symbol = str(payload.get("symbol") or "").upper() or symbols.get(bot_id or "")
                if not symbol:
                    continue
                if _register_decision(
                    conn,
                    decision_key=f"pause:evt:{event_id}",
                    decision_type="pause",
                    agent=agent or "RISK_SENTINEL",
                    bot_id=bot_id,
                    symbol=symbol,
                    decided_at=decided_at,
                    detail={"reason": payload.get("reason")},
                ):
                    registered += 1
        except Exception as exc:
            logger.debug("decision_eval event row skipped: %s", exc)
    return registered


# ---------------------------------------------------------------------------
# Grading pass / expiry / retention / summary
# ---------------------------------------------------------------------------


def _grade_pending(conn, provider: BarProvider, *, now: float, min_age: float, max_age: float) -> dict[str, int]:
    stats = {"graded": 0, "expired": 0, "skipped": 0}
    try:
        rows = conn.cursor().execute(
            """
            SELECT id, decision_key, decision_type, agent, bot_id, symbol, decided_at, detail
            FROM agent_decision_outcomes
            WHERE evaluated_at IS NULL AND decided_at <= ?
            ORDER BY decided_at ASC
            LIMIT ?
            """,
            (now - min_age, _GRADE_LIMIT),
        ).fetchall()
    except Exception as exc:
        logger.debug("decision_eval pending fetch failed: %s", exc)
        return stats

    for row in rows:
        item = _row_dict(row)
        row_id = item.get("id")
        decision_type = str(item.get("decision_type") or "")
        scorer = _SCORERS.get(decision_type)
        decided_at = float(item.get("decided_at") or 0.0)
        try:
            result = scorer(item, provider) if scorer else None
        except Exception as exc:
            # Never let one bad row break the loop.
            logger.debug("decision_eval scorer failed for %s: %s", item.get("decision_key"), exc)
            result = None

        if result is None:
            if decided_at < now - max_age:
                conn.cursor().execute(
                    """
                    UPDATE agent_decision_outcomes
                    SET evaluated_at = ?, outcome = 'insufficient_data', score = NULL
                    WHERE id = ? AND evaluated_at IS NULL
                    """,
                    (now, row_id),
                )
                stats["expired"] += 1
            else:
                stats["skipped"] += 1
            continue

        score, outcome, detail = result
        # Keep the registration context (new_strategy / patch / veto meta)
        # alongside the scorer's grading evidence.
        merged = {**_parse_json_dict(item.get("detail")), **detail}
        conn.cursor().execute(
            """
            UPDATE agent_decision_outcomes
            SET evaluated_at = ?, score = ?, outcome = ?, detail = ?
            WHERE id = ? AND evaluated_at IS NULL
            """,
            (now, float(score), outcome, json.dumps(merged, default=str), row_id),
        )
        stats["graded"] += 1
    return stats


def _prune(conn, *, now: float, retention_days: int) -> int:
    cutoff = now - max(1, retention_days) * 86400.0
    try:
        cur = conn.cursor().execute(
            "DELETE FROM agent_decision_outcomes WHERE decided_at < ?",
            (cutoff,),
        )
        return max(0, int(getattr(cur, "rowcount", 0) or 0))
    except Exception as exc:
        logger.debug("decision_eval prune failed: %s", exc)
        return 0


def refresh_eval_summary(conn=None, *, now: float | None = None) -> int:
    """Rebuild the rolling per-agent accuracy rollup. Returns rows written."""
    ts = float(now if now is not None else time.time())

    def _work(c) -> int:
        groups = c.cursor().execute(
            """
            SELECT agent, decision_type,
                   COUNT(*) AS graded,
                   AVG(score) AS avg_score,
                   AVG(CASE WHEN score > 0 THEN 1.0 ELSE 0.0 END) AS accuracy
            FROM agent_decision_outcomes
            WHERE evaluated_at IS NOT NULL AND score IS NOT NULL
            GROUP BY agent, decision_type
            """
        ).fetchall()
        written = 0
        for row in groups:
            g = _row_dict(row)
            agent = str(g.get("agent") or "unknown")
            dtype = str(g.get("decision_type") or "unknown")
            last = c.cursor().execute(
                """
                SELECT score FROM agent_decision_outcomes
                WHERE agent = ? AND decision_type = ?
                  AND evaluated_at IS NOT NULL AND score IS NOT NULL
                ORDER BY evaluated_at DESC, id DESC
                LIMIT 1
                """,
                (agent, dtype),
            ).fetchone()
            last_score = None
            if last is not None:
                last_item = _row_dict(last)
                last_score = last_item.get("score")
                if last_score is None and not isinstance(last, dict):
                    try:
                        last_score = last[0]
                    except (TypeError, IndexError, KeyError):
                        last_score = None
            if is_postgres():
                c.cursor().execute(
                    """
                    INSERT INTO agent_eval_summary
                        (agent, decision_type, graded, avg_score, accuracy, last_score, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (agent, decision_type) DO UPDATE SET
                        graded = EXCLUDED.graded,
                        avg_score = EXCLUDED.avg_score,
                        accuracy = EXCLUDED.accuracy,
                        last_score = EXCLUDED.last_score,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        agent,
                        dtype,
                        int(g.get("graded") or 0),
                        g.get("avg_score"),
                        g.get("accuracy"),
                        last_score,
                        ts,
                    ),
                )
            else:
                c.cursor().execute(
                    """
                    INSERT OR REPLACE INTO agent_eval_summary
                        (agent, decision_type, graded, avg_score, accuracy, last_score, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        agent,
                        dtype,
                        int(g.get("graded") or 0),
                        g.get("avg_score"),
                        g.get("accuracy"),
                        last_score,
                        ts,
                    ),
                )
            written += 1
        return written

    if conn is not None:
        return _work(conn)
    try:
        with db_session() as c:
            return _work(c)
    except Exception as exc:
        logger.debug("decision_eval summary refresh failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_decision_eval(
    *,
    now: float | None = None,
    bar_provider: BarProvider | None = None,
) -> dict[str, int]:
    """One eval pass: register → grade → expire → prune → summarize.

    Synchronous (DB + archive reads); the runtime loop wraps it in
    ``asyncio.to_thread``. Never raises.
    """
    from app.database import ensure_agent_decision_eval_tables

    ts = float(now if now is not None else time.time())
    provider = bar_provider or _default_bar_provider
    stats = {"registered": 0, "graded": 0, "expired": 0, "skipped": 0, "pruned": 0, "summary_rows": 0}

    try:
        ensure_agent_decision_eval_tables()
    except Exception as exc:
        logger.debug("decision_eval schema ensure skipped: %s", exc)

    min_age = max(0.0, float(AGENT_EVAL_MIN_AGE_SEC))
    max_age = max(min_age, float(AGENT_EVAL_MAX_AGE_SEC))
    cutoff = ts - max_age

    try:
        with db_session() as conn:
            stats["registered"] += _register_vetoes(conn, now=ts, cutoff=cutoff)
            stats["registered"] += _register_events(conn, now=ts, cutoff=cutoff)
    except Exception as exc:
        logger.error("decision_eval registration failed: %s", exc)

    try:
        with db_session() as conn:
            grade_stats = _grade_pending(conn, provider, now=ts, min_age=min_age, max_age=max_age)
            stats.update(grade_stats)
    except Exception as exc:
        logger.error("decision_eval grading failed: %s", exc)

    try:
        with db_session() as conn:
            stats["pruned"] = _prune(conn, now=ts, retention_days=int(AGENT_EVAL_RETENTION_DAYS))
    except Exception as exc:
        logger.error("decision_eval prune failed: %s", exc)

    try:
        stats["summary_rows"] = refresh_eval_summary(now=ts)
    except Exception as exc:
        logger.debug("decision_eval summary failed: %s", exc)

    return stats


def get_decision_scores(
    agent: str | None = None,
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """Per-agent decision scores + recent graded outcomes for the API."""
    try:
        lim = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        lim = 50

    summary: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    pending = 0
    try:
        with db_session(commit=False) as conn:
            clauses: list[str] = []
            params: list[Any] = []
            if agent:
                clauses.append("agent = ?")
                params.append(str(agent))
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.cursor().execute(
                f"""
                SELECT agent, decision_type, graded, avg_score, accuracy, last_score, updated_at
                FROM agent_eval_summary
                {where}
                ORDER BY agent, decision_type
                """,
                tuple(params),
            ).fetchall()
            for row in rows:
                item = _row_dict(row)
                for key in ("avg_score", "accuracy", "last_score"):
                    if item.get(key) is not None:
                        try:
                            item[key] = round(float(item[key]), 4)
                        except (TypeError, ValueError):
                            pass
                summary.append(item)

            recent_where = (
                f"WHERE evaluated_at IS NOT NULL{' AND agent = ?' if agent else ''}"
            )
            recent_params = tuple(params) if agent else ()
            rows = conn.cursor().execute(
                f"""
                SELECT decision_key, decision_type, agent, bot_id, symbol,
                       decided_at, evaluated_at, score, outcome, detail
                FROM agent_decision_outcomes
                {recent_where}
                ORDER BY evaluated_at DESC, id DESC
                LIMIT ?
                """,
                (*recent_params, lim),
            ).fetchall()
            for row in rows:
                item = _row_dict(row)
                item["detail"] = _parse_json_dict(item.get("detail"))
                recent.append(item)

            count_row = conn.cursor().execute(
                f"SELECT COUNT(*) FROM agent_decision_outcomes WHERE evaluated_at IS NULL{' AND agent = ?' if agent else ''}",
                recent_params,
            ).fetchone()
            if count_row is not None:
                pending_item = _row_dict(count_row)
                pending = int(
                    next(iter(pending_item.values()), 0) if pending_item else 0
                )
    except Exception as exc:
        logger.debug("get_decision_scores failed: %s", exc)

    return {
        "summary": summary,
        "recent": recent,
        "pending": pending,
    }


def advisor_confidence_weight(
    agent: str,
    *,
    decision_type: str | None = None,
    min_graded: int = 5,
) -> float | None:
    """Rolling accuracy (0..1) an advisor may use to weight suggestions.

    Returns None when the scorer has fewer than ``min_graded`` graded
    decisions — callers should treat None as "no opinion, do not reweight".
    """
    if not agent:
        return None
    try:
        with db_session(commit=False) as conn:
            if decision_type:
                rows = conn.cursor().execute(
                    """
                    SELECT graded, accuracy FROM agent_eval_summary
                    WHERE agent = ? AND decision_type = ?
                    """,
                    (str(agent), str(decision_type)),
                ).fetchall()
            else:
                rows = conn.cursor().execute(
                    """
                    SELECT graded, accuracy FROM agent_eval_summary
                    WHERE agent = ?
                    """,
                    (str(agent),),
                ).fetchall()
    except Exception as exc:
        logger.debug("advisor_confidence_weight failed for %s: %s", agent, exc)
        return None

    total = 0
    weighted = 0.0
    for row in rows:
        item = _row_dict(row)
        try:
            n = int(item.get("graded") or 0)
            acc = float(item.get("accuracy"))
        except (TypeError, ValueError):
            continue
        total += n
        weighted += acc * n
    if total < max(1, int(min_graded)):
        return None
    return round(weighted / total, 4)
