"""Persist alternative data rows (economic + corporate events)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from app.db.connection import db_session, is_postgres


def _parse_timestamp_to_epoch(ts: Any) -> float | None:
    """Normalize trade timestamps (unix, ISO string) to epoch seconds."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        val = float(ts)
        return val if val > 1e9 else None
    if isinstance(ts, str):
        text = ts.strip()
        if not text:
            return None
        if text.isdigit():
            return float(text)
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def _epoch_to_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def get_corporate_events_near(
    symbol: str,
    *,
    epoch: float,
    window_hours: float = 24.0,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Corporate events for symbol within ±window_hours of trade time."""
    sym = str(symbol or "").upper()
    if not sym:
        return []
    window_sec = max(3600.0, float(window_hours) * 3600.0)
    rows: list[dict[str, Any]] = []
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT symbol, event_type, event_date, title, source
                FROM corporate_events
                WHERE symbol = ?
                ORDER BY event_date DESC
                LIMIT 80
                """,
                (sym,),
            )
            raw = cursor.fetchall()
        except Exception:
            return []

    for row in raw:
        if isinstance(row, dict):
            item = dict(row)
        else:
            item = {
                "symbol": row[0],
                "event_type": row[1],
                "event_date": row[2],
                "title": row[3],
                "source": row[4],
            }
        event_epoch = _parse_timestamp_to_epoch(item.get("event_date"))
        if event_epoch is None:
            continue
        if abs(event_epoch - epoch) <= window_sec:
            rows.append(item)

    rows.sort(key=lambda r: abs((_parse_timestamp_to_epoch(r.get("event_date")) or epoch) - epoch))
    return rows[:limit]


def get_economic_events_near(
    *,
    epoch: float,
    window_hours: float = 24.0,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Macro events within ±window_hours of trade time."""
    window_sec = max(3600.0, float(window_hours) * 3600.0)
    rows: list[dict[str, Any]] = []
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT event_type, title, scheduled_at, impact, country, source
                FROM economic_events
                ORDER BY scheduled_at DESC
                LIMIT 120
                """
            )
            raw = cursor.fetchall()
        except Exception:
            return []

    for row in raw:
        if isinstance(row, dict):
            item = dict(row)
        else:
            item = {
                "event_type": row[0],
                "title": row[1],
                "scheduled_at": row[2],
                "impact": row[3],
                "country": row[4],
                "source": row[5],
            }
        event_epoch = _parse_timestamp_to_epoch(item.get("scheduled_at"))
        if event_epoch is None:
            continue
        if abs(event_epoch - epoch) <= window_sec:
            rows.append(item)

    rows.sort(key=lambda r: abs((_parse_timestamp_to_epoch(r.get("scheduled_at")) or epoch) - epoch))
    return rows[:limit]


def get_events_near_trade(
    symbol: str,
    *,
    timestamp: Any = None,
    bar_time: int | None = None,
    window_hours: float = 24.0,
) -> dict[str, Any]:
    """News/calendar context around a fill — corporate (symbol) + economic (macro)."""
    epoch = _parse_timestamp_to_epoch(timestamp)
    if epoch is None and bar_time is not None:
        try:
            epoch = float(bar_time)
        except (TypeError, ValueError):
            epoch = None
    if epoch is None:
        return {"corporate": [], "economic": [], "window_hours": window_hours}

    return {
        "window_hours": window_hours,
        "trade_time": _epoch_to_iso(epoch),
        "corporate": get_corporate_events_near(
            symbol, epoch=epoch, window_hours=window_hours, limit=8,
        ),
        "economic": get_economic_events_near(
            epoch=epoch, window_hours=window_hours, limit=6,
        ),
    }


def upsert_economic_events(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    now = time.time()
    with db_session() as conn:
        cursor = conn.cursor()
        if is_postgres():
            sql = """
                INSERT INTO economic_events (
                    event_id, event_type, title, scheduled_at, impact, country, source, raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (event_id) DO UPDATE SET
                  event_type = excluded.event_type,
                  title = excluded.title,
                  scheduled_at = excluded.scheduled_at,
                  impact = excluded.impact,
                  country = excluded.country,
                  source = excluded.source,
                  raw_json = excluded.raw_json,
                  updated_at = excluded.updated_at
            """
        else:
            sql = """
                INSERT OR REPLACE INTO economic_events (
                    event_id, event_type, title, scheduled_at, impact, country, source, raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        params = [
            (
                r["event_id"],
                r["event_type"],
                r["title"],
                r["scheduled_at"],
                r.get("impact"),
                r.get("country"),
                r["source"],
                json.dumps(r.get("raw") or r),
                now,
            )
            for r in rows
        ]
        cursor.executemany(sql, params)
        return len(params)


def upsert_corporate_events(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    now = time.time()
    with db_session() as conn:
        cursor = conn.cursor()
        if is_postgres():
            sql = """
                INSERT INTO corporate_events (
                    id, symbol, event_type, event_date, title, metadata_json, source, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                  symbol = excluded.symbol,
                  event_type = excluded.event_type,
                  event_date = excluded.event_date,
                  title = excluded.title,
                  metadata_json = excluded.metadata_json,
                  source = excluded.source,
                  updated_at = excluded.updated_at
            """
        else:
            sql = """
                INSERT OR REPLACE INTO corporate_events (
                    id, symbol, event_type, event_date, title, metadata_json, source, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
        params = [
            (
                r["id"],
                r["symbol"],
                r["event_type"],
                r["event_date"],
                r.get("title"),
                json.dumps(r.get("metadata") or {}),
                r["source"],
                now,
            )
            for r in rows
        ]
        cursor.executemany(sql, params)
        return len(params)


def upsert_sentiment_events(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    now = time.time()
    with db_session() as conn:
        cursor = conn.cursor()
        if is_postgres():
            sql = """
                INSERT INTO sentiment_events (
                    id, symbol, source, score, mention_count, headline, published_at, raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                  score = excluded.score,
                  mention_count = excluded.mention_count,
                  headline = excluded.headline,
                  published_at = excluded.published_at,
                  raw_json = excluded.raw_json,
                  updated_at = excluded.updated_at
            """
        else:
            sql = """
                INSERT OR REPLACE INTO sentiment_events (
                    id, symbol, source, score, mention_count, headline, published_at, raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        params = [
            (
                r["id"],
                str(r["symbol"]).upper(),
                r["source"],
                float(r["score"]),
                int(r.get("mention_count") or 1),
                r.get("headline"),
                r.get("published_at"),
                json.dumps(r.get("raw") or r),
                now,
            )
            for r in rows
        ]
        cursor.executemany(sql, params)
        return len(params)


def get_sentiment_events(
    symbol: str,
    *,
    lookback_hours: float = 24.0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sym = str(symbol or "").upper()
    if not sym:
        return []
    cutoff = time.time() - max(3600.0, float(lookback_hours) * 3600.0)
    rows: list[dict[str, Any]] = []
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, symbol, source, score, mention_count, headline, published_at, updated_at
                FROM sentiment_events
                WHERE symbol = ? AND updated_at >= ?
                ORDER BY published_at DESC, updated_at DESC
                LIMIT ?
                """,
                (sym, cutoff, limit),
            )
            for row in cursor.fetchall():
                if isinstance(row, dict):
                    rows.append(dict(row))
                else:
                    rows.append({
                        "id": row[0],
                        "symbol": row[1],
                        "source": row[2],
                        "score": row[3],
                        "mention_count": row[4],
                        "headline": row[5],
                        "published_at": row[6],
                        "updated_at": row[7],
                    })
        except Exception:
            pass
    return rows


def get_aggregate_sentiment(
    symbol: str,
    *,
    lookback_hours: float = 24.0,
) -> dict[str, Any]:
    """Weighted mean sentiment and mention stats for a symbol."""
    events = get_sentiment_events(symbol, lookback_hours=lookback_hours, limit=100)
    if not events:
        return {
            "symbol": str(symbol or "").upper(),
            "aggregate_score": 0.0,
            "mention_count": 0,
            "sources": [],
            "sample_headlines": [],
        }

    weighted_sum = 0.0
    weight_total = 0.0
    sources: set[str] = set()
    headlines: list[str] = []
    for ev in events:
        w = max(1, int(ev.get("mention_count") or 1))
        score = float(ev.get("score") or 0.0)
        weighted_sum += score * w
        weight_total += w
        sources.add(str(ev.get("source") or "unknown"))
        headline = ev.get("headline")
        if headline and len(headlines) < 3:
            headlines.append(str(headline)[:120])

    agg = weighted_sum / weight_total if weight_total > 0 else 0.0
    return {
        "symbol": str(symbol or "").upper(),
        "aggregate_score": round(agg, 4),
        "mention_count": len(events),
        "sources": sorted(sources),
        "sample_headlines": headlines,
    }


def insert_crypto_derivatives_snapshot(row: dict[str, Any]) -> None:
    sym = str(row.get("symbol") or "").upper()
    if not sym:
        return
    recorded = float(row.get("recorded_at") or time.time())
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO crypto_derivatives_history (
                symbol, recorded_at, funding_rate, open_interest, oi_change_24h_pct,
                mark_price, quadrant, score, source, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sym,
                recorded,
                row.get("funding_rate"),
                row.get("open_interest"),
                row.get("oi_change_24h_pct"),
                row.get("mark_price"),
                row.get("quadrant"),
                int(row.get("score") or 0),
                str(row.get("source") or "unknown"),
                json.dumps(row.get("metadata") or {}),
            ),
        )
        # Prune snapshots older than 30 days per symbol
        cutoff = recorded - 30 * 86400
        cursor.execute(
            "DELETE FROM crypto_derivatives_history WHERE symbol = ? AND recorded_at < ?",
            (sym, cutoff),
        )


def get_crypto_derivatives_at(
    symbol: str,
    at_ts: float | int | None,
) -> dict[str, Any] | None:
    sym = str(symbol or "").upper()
    if not sym:
        return None
    try:
        ref = float(at_ts) if at_ts is not None else time.time()
    except (TypeError, ValueError):
        ref = time.time()
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT funding_rate, open_interest, oi_change_24h_pct, mark_price,
                       quadrant, score, source, recorded_at
                FROM crypto_derivatives_history
                WHERE symbol = ? AND recorded_at <= ?
                ORDER BY recorded_at DESC
                LIMIT 1
                """,
                (sym, ref),
            )
            row = cursor.fetchone()
        except Exception:
            return None
    if not row:
        # Live fallback: latest snapshot regardless of time
        with db_session(commit=False) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    SELECT funding_rate, open_interest, oi_change_24h_pct, mark_price,
                           quadrant, score, source, recorded_at
                    FROM crypto_derivatives_history
                    WHERE symbol = ?
                    ORDER BY recorded_at DESC
                    LIMIT 1
                    """,
                    (sym,),
                )
                row = cursor.fetchone()
            except Exception:
                return None
    if not row:
        return None
    if isinstance(row, dict):
        return dict(row)
    return {
        "funding_rate": row[0],
        "open_interest": row[1],
        "oi_change_24h_pct": row[2],
        "mark_price": row[3],
        "quadrant": row[4],
        "score": row[5],
        "source": row[6],
        "recorded_at": row[7],
    }


def altdata_counts() -> dict[str, int]:
    out = {
        "economic_events": 0,
        "corporate_events": 0,
        "sentiment_events": 0,
        "crypto_derivatives_snapshots": 0,
    }
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        for table, key in (
            ("economic_events", "economic_events"),
            ("corporate_events", "corporate_events"),
            ("sentiment_events", "sentiment_events"),
            ("crypto_derivatives_history", "crypto_derivatives_snapshots"),
        ):
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                row = cursor.fetchone()
                out[key] = int(row[0] if not isinstance(row, dict) else list(row.values())[0])
            except Exception:
                pass
    return out
