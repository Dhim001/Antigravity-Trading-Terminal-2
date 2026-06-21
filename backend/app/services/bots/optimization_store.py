"""Persist parameter-sweep optimization sessions."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_connection


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def save_optimization_run(
    *,
    symbol: str,
    strategy: str,
    objective: str,
    request: dict,
    results: list[dict],
    best_config: dict | None,
    walk_forward: dict | None = None,
) -> str:
    run_id = str(uuid.uuid4())
    conn = get_connection()
    cursor = conn.cursor()
    wf_json = json.dumps(walk_forward) if walk_forward else None
    try:
        cursor.execute(
            """
            INSERT INTO optimization_runs
                (id, symbol, strategy, objective, request_json, results_json, best_config, walk_forward_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                symbol,
                strategy,
                objective,
                json.dumps(request or {}),
                json.dumps(results or []),
                json.dumps(best_config or {}),
                wf_json,
                _now_iso(),
            ),
        )
    except Exception:
        req = dict(request or {})
        if walk_forward:
            req["walk_forward_result"] = walk_forward
        cursor.execute(
            """
            INSERT INTO optimization_runs
                (id, symbol, strategy, objective, request_json, results_json, best_config, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                symbol,
                strategy,
                objective,
                json.dumps(req),
                json.dumps(results or []),
                json.dumps(best_config or {}),
                _now_iso(),
            ),
        )
    try:
        conn.commit()
    finally:
        conn.close()
    return run_id


def _parse_row(row) -> dict[str, Any]:
    item = dict(row) if isinstance(row, dict) else {
        "id": row[0],
        "created_at": row[1],
        "symbol": row[2],
        "strategy": row[3],
        "objective": row[4],
        "request_json": row[5],
        "results_json": row[6],
        "best_config": row[7],
        "walk_forward_json": row[8] if len(row) > 8 else None,
    }
    for key in ("request_json", "results_json", "best_config", "walk_forward_json"):
        raw = item.get(key)
        if isinstance(raw, str):
            parsed_key = key.replace("_json", "")
            try:
                item[parsed_key] = json.loads(raw or ("{}" if parsed_key in ("request", "best_config", "walk_forward") else "[]"))
            except json.JSONDecodeError:
                item[parsed_key] = {} if parsed_key in ("request", "best_config", "walk_forward") else []
        elif raw is None and key == "walk_forward_json":
            item["walk_forward"] = None
    if "request_json" in item and "request" not in item:
        try:
            item["request"] = json.loads(item.pop("request_json") or "{}")
        except json.JSONDecodeError:
            item["request"] = {}
    if "results_json" in item and "results" not in item:
        try:
            item["results"] = json.loads(item.pop("results_json") or "[]")
        except json.JSONDecodeError:
            item["results"] = []
    if "walk_forward_json" in item and "walk_forward" not in item:
        raw = item.pop("walk_forward_json")
        if isinstance(raw, str):
            try:
                item["walk_forward"] = json.loads(raw or "null")
            except json.JSONDecodeError:
                item["walk_forward"] = None
        else:
            item["walk_forward"] = raw
    req = item.get("request") or {}
    if not item.get("walk_forward") and isinstance(req, dict) and req.get("walk_forward_result"):
        item["walk_forward"] = req.get("walk_forward_result")
    if isinstance(item.get("best_config"), str):
        try:
            item["best_config"] = json.loads(item["best_config"] or "{}")
        except json.JSONDecodeError:
            item["best_config"] = {}
    return item


def get_optimization_run(run_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, created_at, symbol, strategy, objective, request_json, results_json, best_config, walk_forward_json
            FROM optimization_runs
            WHERE id = ?
            """,
            (run_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _parse_row(row)
    finally:
        conn.close()


def list_optimization_runs(*, limit: int = 20, symbol: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if symbol:
            cursor.execute(
                """
                SELECT id, created_at, symbol, strategy, objective, request_json, results_json, best_config, walk_forward_json
                FROM optimization_runs
                WHERE symbol = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (symbol, limit),
            )
        else:
            cursor.execute(
                """
                SELECT id, created_at, symbol, strategy, objective, request_json, results_json, best_config, walk_forward_json
                FROM optimization_runs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [_parse_row(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def prune_optimization_runs(retention_days: int) -> int:
    """Delete optimization runs older than retention_days. Returns rows deleted."""
    if retention_days <= 0:
        return 0
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(retention_days))).isoformat().replace("+00:00", "Z")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM optimization_runs WHERE created_at < ?",
            (cutoff,),
        )
        deleted = cursor.rowcount or 0
        conn.commit()
        return deleted
    finally:
        conn.close()
