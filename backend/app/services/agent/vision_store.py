"""Persist on-demand chart vision reports — SQLite source of truth for RAG."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.db.connection import get_connection, is_postgres
from app.services.agent.models import VisionReport
from app.services.market.timeframes import normalize_timeframe, timeframe_to_secs

logger = logging.getLogger(__name__)

# Bar-window tolerance when matching vision to a trade signal bar.
_DEFAULT_LOOKUP_TFS = ("4h", "1h")


def vision_report_id(symbol: str, timeframe: str, bar_time: int) -> str:
    tf = normalize_timeframe(timeframe) if timeframe not in ("1h", "4h") else timeframe.lower()
    if tf not in ("1h", "4h"):
        tf = "4h"
    return f"{symbol.upper()}:{tf}:{int(bar_time)}"


def build_rag_text(report: VisionReport | dict[str, Any]) -> str:
    """
    Canonical text blob for future embedding / vector search.
    Kept denormalized in `vision_reports.rag_text` at write time.
    """
    if isinstance(report, VisionReport):
        data = report.to_dict()
    else:
        data = report
    sym = data.get("symbol", "")
    tf = data.get("timeframe", "")
    bar_time = data.get("bar_time")
    parts: list[str] = []
    if sym:
        parts.append(f"Symbol: {sym}")
    if tf:
        parts.append(f"Timeframe: {tf}")
    if bar_time is not None:
        parts.append(f"Bar time: {bar_time}")
    structure = (data.get("structure") or "").strip()
    if structure:
        parts.append(f"Structure: {structure}")
    patterns = data.get("patterns") or []
    if patterns:
        parts.append("Patterns: " + ", ".join(str(p) for p in patterns[:12]))
    notes = (data.get("notes") or "").strip()
    if notes:
        parts.append(f"Notes: {notes}")
    return "\n".join(parts)


def slim_vision_payload(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Shape attached to trade-explain bundles."""
    if not data or not data.get("structure"):
        return None
    return {
        "report_id": data.get("report_id"),
        "timeframe": data.get("timeframe"),
        "bar_time": data.get("bar_time"),
        "structure": data.get("structure"),
        "patterns": (data.get("patterns") or [])[:5],
        "notes": data.get("notes"),
        "model": data.get("model"),
        "rag_text": data.get("rag_text"),
    }


def persist_vision_report(report: VisionReport) -> None:
    """Upsert full vision JSON + denormalized rag_text."""
    payload = report.to_dict()
    rag_text = build_rag_text(report)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if is_postgres():
            cursor.execute(
                """
                INSERT INTO vision_reports
                    (report_id, symbol, timeframe, bar_time, payload, rag_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (report_id) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    rag_text = EXCLUDED.rag_text,
                    created_at = CURRENT_TIMESTAMP
                """,
                (
                    report.report_id,
                    report.symbol.upper(),
                    report.timeframe.lower(),
                    int(report.bar_time),
                    json.dumps(payload),
                    rag_text,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT OR REPLACE INTO vision_reports
                    (report_id, symbol, timeframe, bar_time, payload, rag_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    report.report_id,
                    report.symbol.upper(),
                    report.timeframe.lower(),
                    int(report.bar_time),
                    json.dumps(payload),
                    rag_text,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def get_vision_report(report_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT payload, rag_text FROM vision_reports WHERE report_id = ?",
            (report_id,),
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    raw = row[0] if not isinstance(row, dict) else row.get("payload")
    if not raw:
        return None
    data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    rag = row[1] if not isinstance(row, dict) else row.get("rag_text")
    if rag and not data.get("rag_text"):
        data["rag_text"] = rag
    return data


def get_vision_exact(symbol: str, timeframe: str, bar_time: int) -> dict[str, Any] | None:
    rid = vision_report_id(symbol, timeframe, bar_time)
    return get_vision_report(rid)


def lookup_vision_near_bar(
    symbol: str,
    bar_time: int | None,
    *,
    timeframes: tuple[str, ...] = _DEFAULT_LOOKUP_TFS,
) -> dict[str, Any] | None:
    """Exact bar match first, then nearest bar within one HT period per TF."""
    if bar_time is None:
        return None
    sym = symbol.upper()
    for tf in timeframes:
        exact = get_vision_exact(sym, tf, bar_time)
        if exact:
            return slim_vision_payload(exact)
    try:
        period_by_tf = {tf: timeframe_to_secs(tf) for tf in timeframes}
    except ValueError:
        period_by_tf = {"4h": 14400, "1h": 3600}

    conn = get_connection()
    cursor = conn.cursor()
    best: dict[str, Any] | None = None
    best_delta = max(period_by_tf.values()) + 1
    try:
        for tf in timeframes:
            period = period_by_tf.get(tf, 3600)
            lo = int(bar_time) - period
            hi = int(bar_time) + period
            cursor.execute(
                """
                SELECT payload, rag_text, bar_time, timeframe
                FROM vision_reports
                WHERE symbol = ? AND timeframe = ? AND bar_time BETWEEN ? AND ?
                ORDER BY ABS(bar_time - ?) ASC
                LIMIT 1
                """,
                (sym, tf.lower(), lo, hi, int(bar_time)),
            )
            row = cursor.fetchone()
            if not row:
                continue
            raw = row[0] if not isinstance(row, dict) else row.get("payload")
            if not raw:
                continue
            data = json.loads(raw) if isinstance(raw, str) else dict(raw)
            rag = row[1] if not isinstance(row, dict) else row.get("rag_text")
            if rag:
                data["rag_text"] = rag
            bt = row[2] if not isinstance(row, dict) else row.get("bar_time")
            delta = abs(int(bt) - int(bar_time))
            if delta <= period and delta < best_delta:
                best = data
                best_delta = delta
    finally:
        conn.close()
    return slim_vision_payload(best)


def list_vision_reports(symbol: str, *, timeframe: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    sym = symbol.upper()
    try:
        if timeframe:
            cursor.execute(
                """
                SELECT payload FROM vision_reports
                WHERE symbol = ? AND timeframe = ?
                ORDER BY bar_time DESC
                LIMIT ?
                """,
                (sym, timeframe.lower(), limit),
            )
        else:
            cursor.execute(
                """
                SELECT payload FROM vision_reports
                WHERE symbol = ?
                ORDER BY bar_time DESC
                LIMIT ?
                """,
                (sym, limit),
            )
        rows = cursor.fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        raw = row[0] if not isinstance(row, dict) else row.get("payload")
        if not raw:
            continue
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
        out.append(data)
    return out


def search_vision_semantic(
    symbol: str,
    query_text: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """
    Vector RAG hook (not implemented).

    Phase 2 plan:
    1. On persist_vision_report, embed `rag_text` via configured provider.
    2. Store vector in `vision_reports.embedding` (pgvector) or side index (sqlite-vec).
    3. At explain time, embed `query_text` (trade + insight context) and ANN-search
       filtered by symbol (and optionally timeframe window).

    Until then, callers should use lookup_vision_near_bar or keyword fallback below.
    """
    _ = query_text
    sym = symbol.upper()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT payload, rag_text FROM vision_reports
            WHERE symbol = ?
            ORDER BY bar_time DESC
            LIMIT ?
            """,
            (sym, max(limit * 4, 20)),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    # Keyword fallback: score rag_text overlap (bridges to vector RAG without embeddings).
    query_tokens = {t.lower() for t in query_text.split() if len(t) > 3}
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        raw = row[0] if not isinstance(row, dict) else row.get("payload")
        rag = row[1] if not isinstance(row, dict) else row.get("rag_text")
        if not raw:
            continue
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if rag:
            data["rag_text"] = rag
        text = (rag or build_rag_text(data)).lower()
        score = sum(1 for tok in query_tokens if tok in text)
        if score > 0:
            scored.append((score, data))
    scored.sort(key=lambda item: -item[0])
    return [item[1] for item in scored[:limit]]
