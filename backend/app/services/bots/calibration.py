"""Trade outcome calibration — pair entries/exits, bucket win rates, threshold hints."""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from app.database import get_connection
from app.services.bots.analytics import _parse_insight_snapshot
from app.services.bots.strategies_chart_agent import classify_filter_reject

FILTER_REJECT_BUCKETS = ("min_score", "trend", "vol", "htf", "confidence", "calibration", "other")


def wilson_lower_bound(wins: int, n: int, z: float = 1.96) -> float:
    """Wilson score interval lower bound (95% default)."""
    if n <= 0:
        return 0.0
    wins = max(0, min(wins, n))
    phat = wins / n
    denom = 1.0 + z * z / n
    centre = phat + z * z / (2.0 * n)
    margin = z * math.sqrt((phat * (1.0 - phat) + z * z / (4.0 * n)) / n)
    return round(max(0.0, (centre - margin) / denom), 4)


def score_bucket(score: int | float | None) -> str:
    try:
        val = abs(int(score or 0))
    except (TypeError, ValueError):
        return "0"
    if val >= 4:
        return "4+"
    if val >= 3:
        return "3"
    if val >= 2:
        return "2"
    return "0-1"


def confidence_bucket(confidence: float | None) -> str:
    try:
        c = float(confidence or 0)
    except (TypeError, ValueError):
        return "unknown"
    if c >= 0.75:
        return "0.75+"
    if c >= 0.65:
        return "0.65-0.75"
    if c >= 0.55:
        return "0.55-0.65"
    return "<0.55"


@dataclass
class ClosedTrade:
    bot_id: str
    symbol: str
    timeframe: str
    side: str
    score: int | None
    confidence: float | None
    atr_regime: str | None
    trend_score: int | None
    momentum_score: int | None
    pnl: float
    win: bool
    entry_id: int | None = None
    exit_id: int | None = None
    entry_ts: str | None = None
    exit_ts: str | None = None

    def bucket_key(self) -> tuple:
        return (
            self.symbol,
            self.timeframe,
            self.side,
            self.atr_regime or "unknown",
            score_bucket(self.score),
            confidence_bucket(self.confidence),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sub_score(snapshot: dict | None, key: str) -> int | None:
    if not snapshot:
        return None
    sub = snapshot.get("sub_reports") or {}
    block = sub.get(key) or {}
    try:
        return int(block.get("score")) if block.get("score") is not None else None
    except (TypeError, ValueError):
        return None


def _context_from_entry(entry: dict, timeframe: str) -> dict[str, Any]:
    snap = entry.get("insight_snapshot")
    if not isinstance(snap, dict):
        snap = _parse_insight_snapshot(entry.get("insight_snapshot"))
    sub = (snap or {}).get("sub_reports") or {}
    risk = sub.get("risk") or {}
    return {
        "timeframe": timeframe,
        "side": entry.get("side") or "BUY",
        "score": snap.get("score") if snap else None,
        "confidence": snap.get("confidence") if snap else None,
        "atr_regime": risk.get("atr_regime"),
        "trend_score": _sub_score(snap, "trend"),
        "momentum_score": _sub_score(snap, "momentum"),
    }


def pair_closed_trades(
    trades: list[dict],
    *,
    bot_timeframes: dict[str, str] | None = None,
) -> list[ClosedTrade]:
    """Match long-only entry rows to the next exit with realized pnl."""
    tf_map = bot_timeframes or {}
    sorted_trades = sorted(trades, key=lambda t: (t.get("timestamp") or "", t.get("id") or 0))
    pending: dict[tuple[str, str], dict] = {}
    closed: list[ClosedTrade] = []

    for row in sorted_trades:
        bot_id = str(row.get("bot_id") or "")
        symbol = str(row.get("symbol") or "").upper()
        key = (bot_id, symbol)
        is_exit = bool(row.get("is_exit"))

        if not is_exit:
            if str(row.get("side", "")).upper() == "BUY":
                pending[key] = row
            continue

        entry = pending.pop(key, None)
        pnl = row.get("pnl")
        if pnl is None or entry is None:
            continue
        try:
            pnl_f = float(pnl)
        except (TypeError, ValueError):
            continue

        ctx = _context_from_entry(entry, tf_map.get(bot_id, "1m"))
        closed.append(
            ClosedTrade(
                bot_id=bot_id,
                symbol=symbol,
                timeframe=ctx["timeframe"],
                side=ctx["side"],
                score=ctx["score"],
                confidence=ctx["confidence"],
                atr_regime=ctx["atr_regime"],
                trend_score=ctx["trend_score"],
                momentum_score=ctx["momentum_score"],
                pnl=round(pnl_f, 4),
                win=pnl_f > 0,
                entry_id=entry.get("id"),
                exit_id=row.get("id"),
                entry_ts=entry.get("timestamp"),
                exit_ts=row.get("timestamp"),
            )
        )
    return closed


def _bucket_stats(trades: list[ClosedTrade], key_fn) -> list[dict[str, Any]]:
    groups: dict[Any, list[ClosedTrade]] = defaultdict(list)
    for t in trades:
        groups[key_fn(t)].append(t)

    rows: list[dict[str, Any]] = []
    for key, items in groups.items():
        n = len(items)
        wins = sum(1 for t in items if t.win)
        total_pnl = sum(t.pnl for t in items)
        win_pnls = [t.pnl for t in items if t.win]
        loss_pnls = [t.pnl for t in items if not t.win]
        avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0.0
        avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0.0
        win_rate = wins / n if n else 0.0
        loss_rate = 1.0 - win_rate
        expectancy = win_rate * avg_win + loss_rate * avg_loss

        if isinstance(key, tuple):
            symbol, timeframe, side, atr_regime, score_b, conf_b = key
            label = {
                "symbol": symbol,
                "timeframe": timeframe,
                "side": side,
                "atr_regime": atr_regime,
                "score_bucket": score_b,
                "confidence_bucket": conf_b,
            }
        else:
            label = {"symbol": key}

        rows.append({
            **label,
            "sample_size": n,
            "win_count": wins,
            "win_rate": round(win_rate, 4),
            "wilson_lower": wilson_lower_bound(wins, n),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "expectancy": round(expectancy, 4),
            "total_pnl": round(total_pnl, 2),
        })
    rows.sort(key=lambda r: (-r["expectancy"], -r["sample_size"]))
    return rows


def suggest_thresholds(
    buckets: list[dict[str, Any]],
    *,
    min_samples: int = 5,
    target_wilson: float = 0.45,
) -> list[dict[str, Any]]:
    """Advisory hints — never mutates bot config."""
    suggestions: list[dict[str, Any]] = []
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for b in buckets:
        sym = b.get("symbol")
        if sym:
            by_symbol[sym].append(b)

    for symbol, sym_buckets in sorted(by_symbol.items()):
        low_conf = [
            b for b in sym_buckets
            if b.get("confidence_bucket") in ("<0.55", "0.55-0.65")
            and b.get("sample_size", 0) >= min_samples
            and b.get("wilson_lower", 0) < target_wilson
        ]
        if low_conf:
            total_n = sum(b["sample_size"] for b in low_conf)
            suggestions.append({
                "symbol": symbol,
                "kind": "min_confidence",
                "message": (
                    f"{symbol}: {total_n} trades in low-confidence buckets have "
                    f"Wilson lower bound below {int(target_wilson * 100)}% — consider min_confidence ≥ 0.65"
                ),
                "suggested_min_confidence": 0.65,
                "evidence_sample_size": total_n,
            })

        low_score = [
            b for b in sym_buckets
            if b.get("score_bucket") in ("2", "0-1")
            and b.get("sample_size", 0) >= min_samples
            and b.get("wilson_lower", 0) < target_wilson
        ]
        if low_score:
            total_n = sum(b["sample_size"] for b in low_score)
            suggestions.append({
                "symbol": symbol,
                "kind": "min_score",
                "message": (
                    f"{symbol}: {total_n} trades with |score| ≤ 2 underperform — consider min_score ≥ 3"
                ),
                "suggested_min_score": 3,
                "evidence_sample_size": total_n,
            })

        elevated = [
            b for b in sym_buckets
            if b.get("atr_regime") == "elevated"
            and b.get("sample_size", 0) >= min_samples
            and b.get("wilson_lower", 0) < target_wilson
        ]
        if elevated:
            total_n = sum(b["sample_size"] for b in elevated)
            suggestions.append({
                "symbol": symbol,
                "kind": "block_elevated_vol",
                "message": (
                    f"{symbol}: {total_n} trades in elevated ATR regime underperform — "
                    "consider block_elevated_vol: true"
                ),
                "suggested_block_elevated_vol": True,
                "evidence_sample_size": total_n,
            })

    return suggestions


def fetch_trades_for_calibration(
    *,
    bot_id: str | None = None,
    symbol: str | None = None,
    limit: int = 2000,
) -> list[dict]:
    limit = max(1, min(int(limit), 10000))
    conn = get_connection()
    cursor = conn.cursor()
    clauses: list[str] = []
    params: list[Any] = []
    if bot_id:
        clauses.append("bot_id = ?")
        params.append(bot_id)
    if symbol:
        clauses.append("UPPER(symbol) = ?")
        params.append(symbol.upper())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    cursor.execute(
        f"""
        SELECT id, bot_id, symbol, side, quantity, price, pnl, signal_id,
               signal_bar_time, is_exit, timestamp, insight_snapshot
        FROM bot_trades
        {where}
        ORDER BY timestamp ASC
        LIMIT ?
        """,
        (*params, limit),
    )
    rows = []
    for row in cursor.fetchall():
        item = dict(row)
        item["insight_snapshot"] = _parse_insight_snapshot(item.get("insight_snapshot"))
        rows.append(item)
    conn.close()
    return rows


def fetch_bot_timeframes(bot_ids: list[str] | None = None) -> dict[str, str]:
    conn = get_connection()
    cursor = conn.cursor()
    if bot_ids:
        placeholders = ",".join("?" * len(bot_ids))
        cursor.execute(
            f"SELECT id, timeframe FROM bots WHERE id IN ({placeholders})",
            bot_ids,
        )
    else:
        cursor.execute("SELECT id, timeframe FROM bots")
    out = {str(r[0]): str(r[1] or "1m") for r in cursor.fetchall()}
    conn.close()
    return out


def get_calibration(
    *,
    bot_id: str | None = None,
    symbol: str | None = None,
    min_samples: int = 3,
    limit: int = 2000,
) -> dict[str, Any]:
    trades = fetch_trades_for_calibration(bot_id=bot_id, symbol=symbol, limit=limit)
    bot_ids = list({t["bot_id"] for t in trades if t.get("bot_id")})
    tf_map = fetch_bot_timeframes(bot_ids)
    closed = pair_closed_trades(trades, bot_timeframes=tf_map)

    with_context = [t for t in closed if t.score is not None or t.confidence is not None]
    all_buckets = _bucket_stats(with_context or closed, lambda t: t.bucket_key())
    symbol_buckets = _bucket_stats(closed, lambda t: t.symbol)
    filtered = [b for b in all_buckets if b.get("sample_size", 0) >= min_samples]

    wins = sum(1 for t in closed if t.win)
    n = len(closed)
    overall = {
        "closed_trades": n,
        "wins": wins,
        "win_rate": round(wins / n, 4) if n else 0.0,
        "wilson_lower": wilson_lower_bound(wins, n),
        "total_pnl": round(sum(t.pnl for t in closed), 2),
        "with_insight_context": len(with_context),
    }

    symbol_thresholds: dict[str, dict[str, Any]] = {}
    for sym_row in symbol_buckets:
        sym = sym_row["symbol"]
        if sym_row.get("sample_size", 0) < min_samples:
            continue
        sym_closed = [t for t in closed if t.symbol == sym]
        sym_wins = sum(1 for t in sym_closed if t.win)
        sym_n = len(sym_closed)
        symbol_thresholds[sym] = {
            "sample_size": sym_n,
            "win_rate": round(sym_wins / sym_n, 4) if sym_n else 0.0,
            "wilson_lower": wilson_lower_bound(sym_wins, sym_n),
            "total_pnl": round(sum(t.pnl for t in sym_closed), 2),
            "suggestions": suggest_thresholds(
                [b for b in all_buckets if b.get("symbol") == sym],
                min_samples=min_samples,
            ),
        }

    return {
        "overall": overall,
        "buckets": filtered,
        "symbol_summary": symbol_buckets,
        "symbol_thresholds": symbol_thresholds,
        "suggestions": suggest_thresholds(filtered, min_samples=min_samples),
        "recent_closed": [t.to_dict() for t in closed[-20:]],
    }


def _empty_reject_counts() -> dict[str, int]:
    return {k: 0 for k in FILTER_REJECT_BUCKETS}


def aggregate_live_filter_rejects(
    *,
    bot_id: str | None = None,
    symbol: str | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    """Aggregate CHART_AGENT skip events from bot_logs."""
    limit = max(1, min(int(limit), 20000))
    conn = get_connection()
    cursor = conn.cursor()
    clauses = ["meta IS NOT NULL"]
    params: list[Any] = []
    if bot_id:
        clauses.append("bot_id = ?")
        params.append(bot_id)
    cursor.execute(
        f"""
        SELECT bot_id, message, timestamp, meta
        FROM bot_logs
        WHERE {' AND '.join(clauses)}
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    rows = cursor.fetchall()
    conn.close()

    by_bucket = _empty_reject_counts()
    by_symbol: dict[str, dict[str, int]] = defaultdict(_empty_reject_counts)
    recent: list[dict[str, Any]] = []

    for row in rows:
        item = dict(row)
        raw_meta = item.get("meta")
        try:
            meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(meta, dict):
            continue
        if meta.get("event_type") != "chart_agent_skip":
            continue
        sym = str(meta.get("symbol") or "").upper()
        if symbol and sym != symbol.upper():
            continue
        reason = meta.get("reject_reason") or item.get("message") or ""
        bucket = classify_filter_reject(str(reason)) or "other"
        by_bucket[bucket] = by_bucket.get(bucket, 0) + 1
        if sym:
            by_symbol[sym][bucket] = by_symbol[sym].get(bucket, 0) + 1
        if len(recent) < 30:
            recent.append({
                "bot_id": item.get("bot_id"),
                "symbol": sym,
                "timestamp": item.get("timestamp"),
                "bucket": bucket,
                "reason": str(reason)[:200],
            })

    total = sum(by_bucket.values())
    return {
        "total": total,
        "by_bucket": by_bucket,
        "by_symbol": dict(by_symbol),
        "recent": recent,
    }


def aggregate_backtest_filter_rejects(
    *,
    symbol: str | None = None,
    strategy: str | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """Sum filter_rejects from recent backtest + optimization runs."""
    limit = max(1, min(int(limit), 100))
    conn = get_connection()
    cursor = conn.cursor()

    by_bucket = _empty_reject_counts()
    runs_used = 0

    def _merge_summary(summary: dict | None) -> None:
        nonlocal runs_used
        if not summary:
            return
        rejects = summary.get("filter_rejects")
        if not rejects:
            return
        runs_used += 1
        for key, count in rejects.items():
            bucket = key if key in by_bucket else "other"
            by_bucket[bucket] = by_bucket.get(bucket, 0) + int(count or 0)

    if symbol:
        cursor.execute(
            """
            SELECT results FROM backtest_runs
            WHERE UPPER(symbol) = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (symbol.upper(), limit),
        )
    else:
        cursor.execute(
            "SELECT results FROM backtest_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    for (raw,) in cursor.fetchall():
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            continue
        if strategy and str(data.get("meta", {}).get("strategy", "")).upper() != strategy.upper():
            continue
        _merge_summary(data.get("summary"))

    if symbol:
        cursor.execute(
            """
            SELECT results_json FROM optimization_runs
            WHERE UPPER(symbol) = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (symbol.upper(), limit),
        )
    else:
        cursor.execute(
            "SELECT results_json FROM optimization_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    for (raw,) in cursor.fetchall():
        try:
            rows = json.loads(raw or "[]")
        except json.JSONDecodeError:
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if strategy and str(row.get("strategy", "")).upper() != strategy.upper():
                continue
            summary = row.get("summary") if isinstance(row.get("summary"), dict) else row
            if isinstance(summary, dict):
                _merge_summary(summary)

    conn.close()
    return {
        "total": sum(by_bucket.values()),
        "by_bucket": by_bucket,
        "runs_aggregated": runs_used,
    }


def get_filter_reject_dashboard(
    *,
    bot_id: str | None = None,
    symbol: str | None = None,
    strategy: str | None = None,
) -> dict[str, Any]:
    live = aggregate_live_filter_rejects(bot_id=bot_id, symbol=symbol)
    backtest = aggregate_backtest_filter_rejects(symbol=symbol, strategy=strategy)
    return {
        "live": live,
        "backtest": backtest,
    }


def setup_bucket_key(
    *,
    symbol: str,
    timeframe: str,
    side: str,
    insight: dict,
) -> tuple:
    """Bucket key for a prospective entry (matches ClosedTrade.bucket_key)."""
    sub = insight.get("sub_reports") or {}
    atr_regime = (sub.get("risk") or {}).get("atr_regime") or "unknown"
    return (
        str(symbol or "").upper(),
        str(timeframe or "1m"),
        str(side or "BUY").upper(),
        atr_regime,
        score_bucket(insight.get("score")),
        confidence_bucket(insight.get("confidence")),
    )


@dataclass
class _CalibrationCacheEntry:
    expires_at: float
    index: dict[tuple, dict[str, Any]]


class CalibrationStore:
    """In-memory per-bot bucket stats for live meta-label gating."""

    def __init__(self, ttl_sec: float = 300.0):
        self.ttl_sec = max(30.0, float(ttl_sec))
        self._entries: dict[str, _CalibrationCacheEntry] = {}

    def invalidate(self, bot_id: str | None = None) -> None:
        if bot_id:
            self._entries.pop(str(bot_id), None)
        else:
            self._entries.clear()

    def lookup(self, bot_id: str, key: tuple) -> dict[str, Any] | None:
        if not bot_id:
            return None
        entry = self._get_entry(bot_id)
        return entry.index.get(key)

    def _get_entry(self, bot_id: str) -> _CalibrationCacheEntry:
        now = time.monotonic()
        cached = self._entries.get(bot_id)
        if cached and cached.expires_at > now:
            return cached
        index = self._build_index(bot_id)
        entry = _CalibrationCacheEntry(expires_at=now + self.ttl_sec, index=index)
        self._entries[bot_id] = entry
        return entry

    def _build_index(self, bot_id: str) -> dict[tuple, dict[str, Any]]:
        trades = fetch_trades_for_calibration(bot_id=bot_id)
        tf_map = fetch_bot_timeframes([bot_id])
        closed = pair_closed_trades(trades, bot_timeframes=tf_map)
        raw: dict[tuple, dict[str, float | int]] = defaultdict(
            lambda: {"wins": 0, "n": 0, "total_pnl": 0.0}
        )
        for trade in closed:
            if trade.score is None and trade.confidence is None:
                continue
            key = trade.bucket_key()
            raw[key]["n"] += 1
            if trade.win:
                raw[key]["wins"] += 1
            raw[key]["total_pnl"] += trade.pnl

        index: dict[tuple, dict[str, Any]] = {}
        for key, stats in raw.items():
            n = int(stats["n"])
            wins = int(stats["wins"])
            index[key] = {
                "sample_size": n,
                "win_count": wins,
                "win_rate": round(wins / n, 4) if n else 0.0,
                "wilson_lower": wilson_lower_bound(wins, n),
                "total_pnl": round(float(stats["total_pnl"]), 2),
            }
        return index


_store: CalibrationStore | None = None


def get_calibration_store() -> CalibrationStore:
    global _store
    if _store is None:
        from app.config import CALIBRATION_CACHE_TTL_SEC

        _store = CalibrationStore(ttl_sec=CALIBRATION_CACHE_TTL_SEC)
    return _store


def check_meta_label_gate(
    insight: dict,
    cfg: dict,
    *,
    symbol: str,
    timeframe: str,
    signal: str,
    bot_id: str | None = None,
) -> str | None:
    """Block entries when the setup bucket underperforms in closed-trade history."""
    if not cfg.get("calibration_gate_enabled"):
        return None
    if not bot_id:
        return None

    try:
        min_samples = int(cfg.get("calibration_min_samples", 5))
    except (TypeError, ValueError):
        min_samples = 5
    try:
        min_wilson = float(cfg.get("calibration_min_wilson", 0.45))
    except (TypeError, ValueError):
        min_wilson = 0.45

    key = setup_bucket_key(
        symbol=symbol,
        timeframe=timeframe,
        side=signal,
        insight=insight,
    )
    stats = get_calibration_store().lookup(bot_id, key)
    if not stats:
        return None
    n = int(stats.get("sample_size") or 0)
    if n < max(1, min_samples):
        return None
    wilson = float(stats.get("wilson_lower") or 0.0)
    if wilson < min_wilson:
        return (
            f"calibration gate: setup Wilson lower {wilson:.2%} "
            f"below {min_wilson:.2%} (n={n})"
        )
    return None


def build_config_patch_from_suggestions(
    suggestions: list[dict[str, Any]],
    *,
    symbol: str | None = None,
    kinds: set[str] | None = None,
    enable_gate: bool = True,
) -> dict[str, Any]:
    """Merge advisory suggestions into a bot config patch."""
    patch: dict[str, Any] = {}
    applied: list[dict[str, Any]] = []

    for suggestion in suggestions:
        sym = str(suggestion.get("symbol") or "").upper()
        if symbol and sym != symbol.upper():
            continue
        kind = suggestion.get("kind")
        if kinds and kind not in kinds:
            continue

        if kind == "min_confidence":
            val = suggestion.get("suggested_min_confidence")
            if val is not None:
                cur = patch.get("min_confidence")
                patch["min_confidence"] = max(float(cur) if cur is not None else 0.0, float(val))
                applied.append(suggestion)
        elif kind == "min_score":
            val = suggestion.get("suggested_min_score")
            if val is not None:
                cur = patch.get("min_score")
                patch["min_score"] = max(int(cur) if cur is not None else 0, int(val))
                applied.append(suggestion)
        elif kind == "block_elevated_vol":
            patch["block_elevated_vol"] = True
            applied.append(suggestion)

    if enable_gate and patch:
        patch.setdefault("calibration_gate_enabled", True)

    return {"patch": patch, "applied": applied}


def compute_calibration_apply_patch(
    bot_id: str,
    *,
    symbol: str | None = None,
    kinds: list[str] | None = None,
    apply_all: bool = False,
    min_samples: int = 3,
) -> dict[str, Any]:
    """Build config patch from calibration suggestions for a bot."""
    if not bot_id:
        raise ValueError("bot_id is required")

    data = get_calibration(bot_id=bot_id, symbol=symbol, min_samples=min_samples)
    suggestions: list[dict[str, Any]] = list(data.get("suggestions") or [])

    if symbol:
        sym_key = symbol.upper()
        sym_row = (data.get("symbol_thresholds") or {}).get(sym_key)
        if sym_row and sym_row.get("suggestions"):
            suggestions = list(sym_row["suggestions"])

    kind_set = None if apply_all or not kinds else set(kinds)
    result = build_config_patch_from_suggestions(
        suggestions,
        symbol=symbol,
        kinds=kind_set,
        enable_gate=True,
    )
    if not result["patch"]:
        return {
            "patch": {},
            "applied": [],
            "message": "No applicable calibration suggestions for this bot/symbol.",
        }
    return {
        "patch": result["patch"],
        "applied": result["applied"],
        "message": f"Ready to apply {len(result['applied'])} suggestion(s).",
    }
