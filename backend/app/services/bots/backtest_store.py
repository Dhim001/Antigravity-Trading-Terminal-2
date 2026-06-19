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


def _parse_run_row(row) -> dict[str, Any]:
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
    return item


def _summary_from_results(results: dict) -> dict[str, Any]:
    summary = results.get("summary") or {}
    if not summary.get("total_pnl") and "total_pnl" in results:
        summary = {
            "total_pnl": results.get("total_pnl"),
            "win_rate": results.get("win_rate"),
            "total_trades": results.get("trade_count"),
            "max_drawdown": results.get("max_drawdown"),
            "profit_factor": results.get("summary", {}).get("profit_factor"),
            "expectancy": results.get("summary", {}).get("expectancy"),
        }
    return {
        "total_pnl": summary.get("total_pnl"),
        "win_rate": summary.get("win_rate"),
        "total_trades": summary.get("total_trades") or results.get("trade_count"),
        "max_drawdown": summary.get("max_drawdown"),
        "profit_factor": summary.get("profit_factor"),
        "expectancy": summary.get("expectancy"),
        "return_pct": summary.get("return_pct"),
        "sharpe_ratio": summary.get("sharpe_ratio"),
        "time_in_market_pct": summary.get("time_in_market_pct"),
        "blocked_entries": summary.get("blocked_entries"),
        "max_consecutive_losses": summary.get("max_consecutive_losses"),
    }


def get_backtest_run(run_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, symbol, strategy, config, days, results, created_at
            FROM backtest_runs
            WHERE id = ?
            """,
            (run_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        item = _parse_run_row(row)
        item["summary"] = _summary_from_results(item["results"])
        return item
    finally:
        conn.close()


def get_backtest_trades(run_id: str) -> list[dict[str, Any]]:
    run = get_backtest_run(run_id)
    if not run:
        return []
    return list(run.get("results", {}).get("trades") or [])


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
            item = _parse_run_row(row)
            item["summary"] = _summary_from_results(item["results"])
            out.append(item)
        return out
    finally:
        conn.close()
