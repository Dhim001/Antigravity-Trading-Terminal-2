"""Persist backtest runs for comparison and audit."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_connection


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def save_backtest_run(
    symbol: str,
    strategy: str,
    config: dict,
    days: int,
    results: dict,
) -> str:
    run_id = str(uuid.uuid4())
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO backtest_runs (id, symbol, strategy, config, days, results, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                symbol,
                strategy,
                json.dumps(config or {}),
                int(days),
                json.dumps(results),
                _now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def list_backtest_runs(*, limit: int = 50, symbol: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if symbol:
            cursor.execute(
                """
                SELECT id, symbol, strategy, config, days, results, created_at
                FROM backtest_runs
                WHERE symbol = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (symbol, limit),
            )
        else:
            cursor.execute(
                """
                SELECT id, symbol, strategy, config, days, results, created_at
                FROM backtest_runs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = cursor.fetchall()
        out = []
        for row in rows:
            item = dict(row) if isinstance(row, dict) else {
                "id": row[0], "symbol": row[1], "strategy": row[2],
                "config": row[3], "days": row[4], "results": row[5], "created_at": row[6],
            }
            try:
                item["config"] = json.loads(item.get("config") or "{}")
            except json.JSONDecodeError:
                item["config"] = {}
            try:
                item["results"] = json.loads(item.get("results") or "{}")
            except json.JSONDecodeError:
                item["results"] = {}
            summary = item["results"].get("summary") or {}
            item["summary"] = {
                "total_pnl": summary.get("total_pnl"),
                "win_rate": summary.get("win_rate"),
                "total_trades": summary.get("total_trades"),
            }
            out.append(item)
        return out
    finally:
        conn.close()
