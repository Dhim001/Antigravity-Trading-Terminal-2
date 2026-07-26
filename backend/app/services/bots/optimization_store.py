"""Persist parameter-sweep optimization sessions."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_connection

logger = logging.getLogger(__name__)


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


def get_best_config(run_id: str, source: str = "best") -> dict[str, Any] | None:
    """Return best / centroid / manual config from a saved optimization run."""
    run = get_optimization_run(run_id)
    if not run:
        return None
    src = str(source or "best").lower()
    if src == "centroid":
        req = run.get("request") if isinstance(run.get("request"), dict) else {}
        centroid = req.get("stable_config") or req.get("centroid_config")
        if isinstance(centroid, dict) and centroid:
            return dict(centroid)
        # Fall back to mean of numeric keys across top results when no centroid stored
        results = run.get("results") if isinstance(run.get("results"), list) else []
        configs = [
            r.get("config") for r in results[:5]
            if isinstance(r, dict) and isinstance(r.get("config"), dict)
        ]
        if configs:
            keys = set()
            for c in configs:
                keys.update(c.keys())
            out: dict[str, Any] = {}
            for key in keys:
                nums = []
                for c in configs:
                    v = c.get(key)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        nums.append(float(v))
                if nums:
                    mean = sum(nums) / len(nums)
                    out[key] = int(round(mean)) if all(isinstance(c.get(key), int) and not isinstance(c.get(key), bool) for c in configs if key in c) else round(mean, 6)
                else:
                    # majority categorical
                    vals = [c.get(key) for c in configs if key in c]
                    if vals:
                        out[key] = max(set(vals), key=vals.count)
            if out:
                return out
    best = run.get("best_config")
    return dict(best) if isinstance(best, dict) else {}


def _ensure_opt_bot_links_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS optimization_bot_links (
            run_id TEXT NOT NULL,
            bot_id TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            config_source TEXT,
            PRIMARY KEY (run_id, bot_id)
        )
        """
    )


def link_optimization_to_bot(
    run_id: str,
    bot_id: str,
    *,
    config_source: str = "best",
) -> bool:
    """Record that an optimization run was applied to a bot."""
    if not run_id or not bot_id:
        return False
    conn = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_opt_bot_links_table(cursor)
        # Portable upsert (SQLite + Postgres) — avoid dialect-specific EXCLUDED/excluded.
        cursor.execute(
            "DELETE FROM optimization_bot_links WHERE run_id = ? AND bot_id = ?",
            (str(run_id), str(bot_id)),
        )
        cursor.execute(
            """
            INSERT INTO optimization_bot_links (run_id, bot_id, applied_at, config_source)
            VALUES (?, ?, ?, ?)
            """,
            (str(run_id), str(bot_id), _now_iso(), str(config_source or "best")),
        )
        conn.commit()
        return True
    except Exception:
        logger.debug("link_optimization_to_bot failed", exc_info=True)
        return False
    finally:
        conn.close()


def get_latest_optimized_hyperparams(
    symbol: str,
    strategy: str,
    *,
    prefer_ml_sweep: bool = True,
) -> dict[str, Any] | None:
    """Return best_config from the newest successful run for symbol+strategy."""
    symbol_u = str(symbol or "").upper()
    strategy_u = str(strategy or "").upper()
    if not symbol_u or not strategy_u:
        return None
    runs = list_optimization_runs(limit=40, symbol=symbol_u)
    ml_match = None
    any_match = None
    for run in runs:
        if str(run.get("strategy") or "").upper() != strategy_u:
            continue
        best = run.get("best_config")
        if not isinstance(best, dict) or not best:
            continue
        req = run.get("request") if isinstance(run.get("request"), dict) else {}
        is_ml = (
            str(run.get("objective") or "") == "ml_val_score"
            or req.get("kind") == "ml_hyperparam_sweep"
        )
        if is_ml and ml_match is None:
            ml_match = dict(best)
        if any_match is None:
            any_match = dict(best)
        if ml_match and (not prefer_ml_sweep or ml_match):
            break
    return ml_match if (prefer_ml_sweep and ml_match) else (ml_match or any_match)


def get_param_importance(run_id: str) -> dict[str, float]:
    """Extract Optuna importance ranking stored on the run request/meta."""
    run = get_optimization_run(run_id)
    if not run:
        return {}
    req = run.get("request") if isinstance(run.get("request"), dict) else {}
    imp = req.get("importance_ranking") or req.get("hyperparameter_importance")
    if isinstance(imp, dict):
        out: dict[str, float] = {}
        for k, v in imp.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return out
    # Bayesian meta may live under results summary
    results = run.get("results")
    if isinstance(results, dict):
        bayes = results.get("bayesian") if isinstance(results.get("bayesian"), dict) else {}
        imp2 = bayes.get("hyperparameter_importance") or bayes.get("importance_ranking")
        if isinstance(imp2, dict):
            return {str(k): float(v) for k, v in imp2.items() if _is_number(v)}
    return {}


def _is_number(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False
