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
        written = len(params)

    # Keep table bounded after writes (Movers UI can refresh every 60s).
    try:
        prune_sentiment_events_throttled()
    except Exception:
        pass
    return written


_last_sentiment_prune_ts = 0.0
_SENTIMENT_PRUNE_EVERY_SEC = 300.0


def prune_sentiment_events_throttled(
    *,
    min_interval_sec: float = _SENTIMENT_PRUNE_EVERY_SEC,
) -> dict[str, int | float | bool]:
    """Prune at most once per interval so 60s Movers polls don't hammer DELETE."""
    global _last_sentiment_prune_ts
    now = time.time()
    if now - _last_sentiment_prune_ts < max(30.0, float(min_interval_sec)):
        return {"skipped": True, "remaining": -1}
    _last_sentiment_prune_ts = now
    return prune_sentiment_events()


def prune_sentiment_events(
    *,
    max_age_hours: float | None = None,
    max_rows: int | None = None,
) -> dict[str, int]:
    """Drop stale / excess sentiment_events so storage cannot grow unbounded."""
    from app.config import SENTIMENT_LOOKBACK_HOURS, SENTIMENT_MAX_AGE_HOURS, SENTIMENT_MAX_EVENTS

    age_h = float(
        max_age_hours
        if max_age_hours is not None
        else max(float(SENTIMENT_MAX_AGE_HOURS), float(SENTIMENT_LOOKBACK_HOURS) * 2)
    )
    cap = int(max_rows if max_rows is not None else SENTIMENT_MAX_EVENTS)
    cap = max(100, min(cap, 50_000))
    cutoff = time.time() - max(3600.0, age_h * 3600.0)

    deleted_age = 0
    deleted_cap = 0
    remaining = 0
    with db_session() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM sentiment_events WHERE updated_at < ?", (cutoff,))
            deleted_age = int(cursor.rowcount or 0)
        except Exception:
            deleted_age = 0

        try:
            cursor.execute("SELECT COUNT(*) FROM sentiment_events")
            row = cursor.fetchone()
            remaining = int((row[0] if not isinstance(row, dict) else next(iter(row.values()))) or 0)
        except Exception:
            remaining = 0

        if remaining > cap:
            overflow = remaining - cap
            try:
                # Keep newest rows by updated_at; delete the oldest overflow.
                if is_postgres():
                    cursor.execute(
                        """
                        DELETE FROM sentiment_events
                        WHERE id IN (
                          SELECT id FROM sentiment_events
                          ORDER BY updated_at ASC, id ASC
                          LIMIT ?
                        )
                        """,
                        (overflow,),
                    )
                else:
                    cursor.execute(
                        """
                        DELETE FROM sentiment_events
                        WHERE id IN (
                          SELECT id FROM sentiment_events
                          ORDER BY updated_at ASC, id ASC
                          LIMIT ?
                        )
                        """,
                        (overflow,),
                    )
                deleted_cap = int(cursor.rowcount or 0)
                remaining = max(0, remaining - deleted_cap)
            except Exception:
                deleted_cap = 0

    return {
        "deleted_age": deleted_age,
        "deleted_cap": deleted_cap,
        "remaining": remaining,
        "max_age_hours": age_h,
        "max_rows": cap,
    }


def get_sentiment_events(
    symbol: str,
    *,
    lookback_hours: float = 24.0,
    limit: int = 50,
    epoch: float | None = None,
) -> list[dict[str, Any]]:
    """Fetch sentiment events for ``symbol``.

    When ``epoch`` is set, only events with ``updated_at`` / ``published_at``
    at or before that time and within ``lookback_hours`` are returned (no look-ahead).
    """
    sym = str(symbol or "").upper()
    if not sym:
        return []
    as_of_epoch = float(epoch) if epoch is not None else None
    as_of = as_of_epoch if as_of_epoch is not None else time.time()
    lookback_sec = max(3600.0, float(lookback_hours) * 3600.0)
    cutoff = as_of - lookback_sec
    rows: list[dict[str, Any]] = []
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        try:
            # Live path (no epoch): keep historical updated_at window semantics.
            # As-of / training path: over-fetch then filter on published_at in
            # Python (column is TEXT ISO; cannot compare to epoch in SQL).
            fetch_limit = limit if as_of_epoch is None else max(limit * 4, 200)
            cursor.execute(
                """
                SELECT id, symbol, source, score, mention_count, headline, published_at, updated_at
                FROM sentiment_events
                WHERE symbol = ? AND updated_at >= ?
                ORDER BY published_at DESC, updated_at DESC
                LIMIT ?
                """,
                (sym, cutoff, fetch_limit),
            )
            for row in cursor.fetchall():
                if isinstance(row, dict):
                    item = dict(row)
                else:
                    item = {
                        "id": row[0],
                        "symbol": row[1],
                        "source": row[2],
                        "score": row[3],
                        "mention_count": row[4],
                        "headline": row[5],
                        "published_at": row[6],
                        "updated_at": row[7],
                    }
                if as_of_epoch is not None:
                    event_epoch = _parse_timestamp_to_epoch(item.get("published_at"))
                    if event_epoch is None:
                        try:
                            event_epoch = float(item.get("updated_at"))
                        except (TypeError, ValueError):
                            continue
                    if event_epoch < cutoff or event_epoch > as_of_epoch:
                        continue
                rows.append(item)
                if len(rows) >= limit:
                    break
        except Exception:
            pass
    return rows


def get_aggregate_sentiment(
    symbol: str,
    *,
    lookback_hours: float = 24.0,
    epoch: float | None = None,
) -> dict[str, Any]:
    """Weighted mean sentiment and mention stats for a symbol."""
    events = get_sentiment_events(
        symbol, lookback_hours=lookback_hours, limit=100, epoch=epoch,
    )
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


def get_sentiment_summary(
    symbol: str,
    *,
    epoch: float | None = None,
    lookback_hours: float = 24.0,
) -> dict[str, Any]:
    """Time-aligned sentiment summary for ML features (neutral zeros when empty)."""
    return get_aggregate_sentiment(
        symbol, lookback_hours=lookback_hours, epoch=epoch,
    )


def hours_to_next_macro_event(epoch: float, *, lookahead_hours: float = 72.0) -> float | None:
    """Hours until the next high-impact macro event after ``epoch``, or None."""
    try:
        as_of = float(epoch)
    except (TypeError, ValueError):
        return None
    horizon = as_of + max(3600.0, float(lookahead_hours) * 3600.0)
    best: float | None = None
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT scheduled_at, impact, title
                FROM economic_events
                WHERE event_type = 'macro_release'
                ORDER BY scheduled_at ASC
                LIMIT 200
                """
            )
            raw = cursor.fetchall()
        except Exception:
            return None
    for row in raw:
        if isinstance(row, dict):
            scheduled = row.get("scheduled_at")
            impact = row.get("impact")
            title = row.get("title")
        else:
            scheduled, impact, title = row[0], row[1], row[2]
        event_epoch = _parse_timestamp_to_epoch(scheduled)
        if event_epoch is None or event_epoch < as_of or event_epoch > horizon:
            continue
        impact_l = str(impact or "").lower()
        title_l = str(title or "").lower()
        high = (
            impact_l in ("3", "high", "h")
            or "fomc" in title_l
            or "cpi" in title_l
            or "nfp" in title_l
            or "nonfarm" in title_l
        )
        if not high:
            continue
        hours = (event_epoch - as_of) / 3600.0
        if best is None or hours < best:
            best = hours
    return best


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
