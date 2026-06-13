"""Persist simulated feed prices/candles across backend restarts (SIMULATED mode)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.db.connection import get_connection, is_postgres

logger = logging.getLogger(__name__)

MAX_PERSISTED_CANDLES = 500


def _ensure_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sim_market_state (
            symbol TEXT PRIMARY KEY,
            price REAL NOT NULL,
            candles_json TEXT NOT NULL,
            target_json TEXT,
            updated_at REAL NOT NULL
        )
    """)


def load_sim_market_state() -> dict[str, dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_table(cursor)
        cursor.execute(
            "SELECT symbol, price, candles_json, target_json FROM sim_market_state"
        )
        rows = cursor.fetchall()
    except Exception as exc:
        logger.warning("Could not load sim market state: %s", exc)
        conn.close()
        return {}
    conn.close()

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict):
            sym = row["symbol"]
            price = row["price"]
            candles_raw = row["candles_json"]
            target_raw = row.get("target_json")
        else:
            sym, price, candles_raw, target_raw = row[0], row[1], row[2], row[3]
        try:
            candles = json.loads(candles_raw) if candles_raw else []
            target = json.loads(target_raw) if target_raw else None
        except json.JSONDecodeError:
            continue
        if sym and candles:
            out[sym] = {"price": price, "candles": candles, "target": target}
    return out


def save_sim_market_state(feed) -> None:
    if not hasattr(feed, "_symbols") or not hasattr(feed, "candles"):
        return

    conn = get_connection()
    cursor = conn.cursor()
    now = time.time()
    try:
        _ensure_table(cursor)
        for symbol, info in feed._symbols.items():
            candles = feed.candles.get(symbol, [])
            if not candles:
                continue
            trimmed = candles[-MAX_PERSISTED_CANDLES:]
            target = feed._target_candles.get(symbol) if hasattr(feed, "_target_candles") else None
            candles_json = json.dumps(trimmed)
            target_json = json.dumps(target) if target else None
            price = info.get("price", trimmed[-1]["close"])

            if is_postgres():
                cursor.execute(
                    """
                    INSERT INTO sim_market_state (symbol, price, candles_json, target_json, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (symbol) DO UPDATE SET
                        price = EXCLUDED.price,
                        candles_json = EXCLUDED.candles_json,
                        target_json = EXCLUDED.target_json,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (symbol, price, candles_json, target_json, now),
                )
            else:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO sim_market_state
                    (symbol, price, candles_json, target_json, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (symbol, price, candles_json, target_json, now),
                )
        conn.commit()
    except Exception as exc:
        logger.warning("Could not persist sim market state: %s", exc)
    finally:
        conn.close()


def clear_sim_market_state() -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_table(cursor)
        cursor.execute("DELETE FROM sim_market_state")
        conn.commit()
    except Exception as exc:
        logger.warning("Could not clear sim market state: %s", exc)
    finally:
        conn.close()
