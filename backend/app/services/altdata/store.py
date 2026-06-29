"""Persist alternative data rows (economic + corporate events)."""

from __future__ import annotations

import json
import time
from typing import Any

from app.db.connection import get_connection, is_postgres


def upsert_economic_events(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    conn = get_connection()
    cursor = conn.cursor()
    now = time.time()
    try:
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
        conn.commit()
        return len(params)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_corporate_events(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    conn = get_connection()
    cursor = conn.cursor()
    now = time.time()
    try:
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
        conn.commit()
        return len(params)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def altdata_counts() -> dict[str, int]:
    conn = get_connection()
    cursor = conn.cursor()
    out = {"economic_events": 0, "corporate_events": 0}
    try:
        for table, key in (
            ("economic_events", "economic_events"),
            ("corporate_events", "corporate_events"),
        ):
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                row = cursor.fetchone()
                out[key] = int(row[0] if not isinstance(row, dict) else list(row.values())[0])
            except Exception:
                pass
    finally:
        conn.close()
    return out
