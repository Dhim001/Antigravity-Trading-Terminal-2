"""Durable persistence for AgentReasoning chains — per-bot decision history.

Agents log their chains into bot_logs.meta today; this store keeps a structured,
queryable copy so the UI can render the latest decision per bot. Writes are
best-effort by design: a persistence failure must never break the trading path.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.db.connection import db_session

logger = logging.getLogger(__name__)

# Keep the newest N chains per bot; older rows are pruned on insert.
RETAIN_PER_BOT = 200
MAX_LIST_LIMIT = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_chain_dict(reasoning: Any) -> dict[str, Any]:
    """Accept an AgentReasoning, its to_dict() payload, or any mapping."""
    if reasoning is None:
        return {}
    if hasattr(reasoning, "to_dict"):
        try:
            payload = reasoning.to_dict()
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}
    if isinstance(reasoning, dict):
        return reasoning
    return {}


def _parse_json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def save_agent_reasoning(
    bot_id: str,
    agent: str,
    reasoning: Any,
    *,
    vetoes: list[str] | None = None,
    size_multiplier: float | None = None,
    ts: float | None = None,
    retain: int = RETAIN_PER_BOT,
) -> bool:
    """Persist one agent reasoning chain. Best-effort: never raises."""
    try:
        if not bot_id:
            return False
        chain = _as_chain_dict(reasoning)
        verdict = chain.get("decision")
        notes = chain.get("synthesis")
        observations = chain.get("observations") or []
        if vetoes is None:
            vetoes = chain.get("vetoes") or []
        row_ts = float(ts) if ts is not None else time.time()
        size_mult = float(size_multiplier) if size_multiplier is not None else None

        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO agent_reasoning
                    (bot_id, agent, verdict, notes, observations, vetoes,
                     size_multiplier, ts, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(bot_id),
                    str(agent or "unknown"),
                    str(verdict) if verdict is not None else None,
                    str(notes) if notes is not None else None,
                    json.dumps(observations, default=str),
                    json.dumps(list(vetoes or []), default=str),
                    size_mult,
                    row_ts,
                    _now_iso(),
                ),
            )
            cursor.execute(
                """
                DELETE FROM agent_reasoning
                WHERE bot_id = ? AND id NOT IN (
                    SELECT id FROM agent_reasoning
                    WHERE bot_id = ?
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                )
                """,
                (str(bot_id), str(bot_id), max(1, int(retain))),
            )
        return True
    except Exception as exc:
        logger.debug("save_agent_reasoning failed for bot %s (%s): %s", bot_id, agent, exc)
        return False


def _row_to_chain(row: dict) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "bot_id": row.get("bot_id"),
        "agent": row.get("agent"),
        "verdict": row.get("verdict"),
        "notes": row.get("notes"),
        "observations": _parse_json_list(row.get("observations")),
        "vetoes": _parse_json_list(row.get("vetoes")),
        "size_multiplier": row.get("size_multiplier"),
        "ts": row.get("ts"),
        "created_at": row.get("created_at"),
    }


def list_agent_reasoning(bot_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Newest-first persisted reasoning chains for a bot."""
    if not bot_id:
        return []
    try:
        lim = max(1, min(int(limit), MAX_LIST_LIMIT))
    except (TypeError, ValueError):
        lim = 20
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, bot_id, agent, verdict, notes, observations, vetoes,
                   size_multiplier, ts, created_at
            FROM agent_reasoning
            WHERE bot_id = ?
            ORDER BY ts DESC, id DESC
            LIMIT ?
            """,
            (str(bot_id), lim),
        )
        rows = [dict(r) for r in cursor.fetchall()]
    return [_row_to_chain(r) for r in rows]
