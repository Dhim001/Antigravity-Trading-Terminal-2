"""HITL desk action queue — durable proposals from silent autonomous actors.

When ``AUTO_AGENT_ACTIONS`` is false, actors (RiskSentinel, RegimeRotation,
AlphaDecay, ScannerDeploy, PostTradeLearner) park their non-emergency mutations
here instead of executing. A human approves/rejects via the HTTP API; approval
executes through the tool registry when a matching tool exists, otherwise via
the actor-supplied execute callback registered at propose time.

All public functions are best-effort: DB/event failures are logged, never
raised into the trading path.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from typing import Any, Awaitable, Callable

from app.config import AGENT_HITL_REJECT_COOLDOWN_SEC, AGENT_HITL_TTL_SEC

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"
_STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED)

# In-process execute callbacks for actions with no matching registry tool.
# Keyed by action_id; populated by propose_action(execute_fn=...).
_execute_callbacks: dict[int, Callable[[], Any]] = {}

# Wired by the HTTP layer / bootstrap so approval runs tools against live state.
_state_provider: Callable[[], Any] | None = None
_event_bus: Any | None = None


def set_state_provider(provider: Callable[[], Any] | None) -> None:
    global _state_provider
    _state_provider = provider


def set_event_bus(bus: Any | None) -> None:
    global _event_bus
    _event_bus = bus


def _ensure_tables() -> None:
    from app.database import ensure_agent_action_tables

    ensure_agent_action_tables()


def _row_to_action(row: Any) -> dict[str, Any]:
    def _get(key: str, idx: int) -> Any:
        if isinstance(row, dict):
            return row.get(key)
        try:
            return row[key]
        except (KeyError, TypeError, IndexError):
            return row[idx]

    raw_params = _get("params_json", 3)
    try:
        params = json.loads(raw_params) if raw_params else {}
    except (json.JSONDecodeError, ValueError):
        params = {}
    return {
        "id": _get("id", 0),
        "actor": _get("actor", 1),
        "action_type": _get("action_type", 2),
        "params": params if isinstance(params, dict) else {},
        "reason": _get("reason", 4),
        "status": _get("status", 5),
        "created_at": _get("created_at", 6),
        "resolved_at": _get("resolved_at", 7),
    }


def _action_key(actor: str, action_type: str, params: dict[str, Any] | None) -> str:
    """Identity for dedupe / reject-cooldown (same bot pause is one decision)."""
    p = params if isinstance(params, dict) else {}
    bot_id = str(p.get("bot_id") or "").strip()
    if bot_id:
        return f"{actor}\0{action_type}\0bot:{bot_id}"
    symbol = str(p.get("symbol") or "").strip()
    if symbol:
        return f"{actor}\0{action_type}\0sym:{symbol}"
    return f"{actor}\0{action_type}\0{json.dumps(p, sort_keys=True, default=str)}"


def _rows_for(status: str, actor: str, action_type: str) -> list[dict[str, Any]]:
    from app.db.connection import db_session

    with db_session(commit=False) as conn:
        rows = (
            conn.cursor()
            .execute(
                """
                SELECT id, actor, action_type, params_json, reason, status,
                       created_at, resolved_at
                FROM agent_pending_actions
                WHERE status = ? AND actor = ? AND action_type = ?
                ORDER BY id DESC
                """,
                (status, str(actor or ""), str(action_type or "")),
            )
            .fetchall()
        )
    return [_row_to_action(r) for r in rows]


def _matching(status: str, actor: str, action_type: str, params: dict[str, Any] | None) -> list[dict[str, Any]]:
    key = _action_key(actor, action_type, params)
    return [
        row
        for row in _rows_for(status, actor, action_type)
        if _action_key(row.get("actor") or "", row.get("action_type") or "", row.get("params")) == key
    ]


def _expire_ids(ids: list[int]) -> None:
    if not ids:
        return
    from app.db.connection import db_session

    now = time.time()
    marks = ",".join("?" for _ in ids)
    with db_session() as conn:
        conn.cursor().execute(
            f"UPDATE agent_pending_actions SET status = ?, resolved_at = ? "
            f"WHERE id IN ({marks}) AND status = ?",
            (STATUS_EXPIRED, now, *ids, STATUS_PENDING),
        )
    for aid in ids:
        _execute_callbacks.pop(int(aid), None)


def expire_duplicate_pending() -> int:
    """Keep the newest pending row per action key; expire older clones."""
    try:
        _ensure_tables()
        from app.db.connection import db_session

        with db_session(commit=False) as conn:
            rows = (
                conn.cursor()
                .execute(
                    """
                    SELECT id, actor, action_type, params_json, reason, status,
                           created_at, resolved_at
                    FROM agent_pending_actions WHERE status = ?
                    ORDER BY id DESC
                    """,
                    (STATUS_PENDING,),
                )
                .fetchall()
            )
    except Exception as exc:
        logger.debug("action_queue duplicate scan failed: %s", exc)
        return 0
    seen: set[str] = set()
    stale: list[int] = []
    for raw in rows:
        action = _row_to_action(raw)
        key = _action_key(action.get("actor") or "", action.get("action_type") or "", action.get("params"))
        try:
            aid = int(action["id"])
        except (TypeError, ValueError, KeyError):
            continue
        if key in seen:
            stale.append(aid)
        else:
            seen.add(key)
    if stale:
        try:
            _expire_ids(stale)
        except Exception as exc:
            logger.debug("action_queue duplicate expire failed: %s", exc)
            return 0
    return len(stale)


def _supersede_siblings(action: dict[str, Any]) -> int:
    """Expire other pending rows that are the same HITL decision."""
    try:
        keep_id = int(action.get("id"))
    except (TypeError, ValueError):
        return 0
    matches = _matching(
        STATUS_PENDING,
        action.get("actor") or "",
        action.get("action_type") or "",
        action.get("params"),
    )
    stale = []
    for row in matches:
        try:
            aid = int(row["id"])
        except (TypeError, ValueError, KeyError):
            continue
        if aid != keep_id:
            stale.append(aid)
    _expire_ids(stale)
    return len(stale)


def _publish_event(event_type: str, payload: dict[str, Any]) -> None:
    """Best-effort AgentEvent publish (works with or without a running loop)."""
    bus = _event_bus
    if bus is None:
        return
    try:
        from app.services.agent.agent_event_bus import AgentEvent

        event = AgentEvent(
            source_agent=str(payload.get("actor") or "action_queue"),
            event_type=event_type,
            payload=dict(payload),
            timestamp=time.time(),
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(bus.publish(event))
        else:
            # No loop (sync context, e.g. worker thread): persist directly so
            # the event still lands in the durable agent_events table.
            bus._persist_event(event)
    except Exception as exc:
        logger.debug("action_queue event publish skipped (%s): %s", event_type, exc)


def _narrate_proposal(action: dict[str, Any]) -> None:
    """Copilot narration: 'RiskSentinel wants to pause X — approve?'"""
    try:
        from app.services.agent.copilot import agent_narrate_event

        payload = {
            "action": "hitl_proposal",
            "actor": action.get("actor"),
            "action_type": action.get("action_type"),
            "action_id": action.get("id"),
            "symbol": (action.get("params") or {}).get("symbol"),
            "bot_id": (action.get("params") or {}).get("bot_id"),
            "reason": action.get("reason"),
            "params": action.get("params"),
        }
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            raise RuntimeError("no running loop")
        loop.create_task(agent_narrate_event("DeskSupervisor", payload))
    except RuntimeError:
        # No running loop — store the chat line synchronously so it is not lost.
        try:
            from app.services.agent import copilot_store

            action_id = action.get("id")
            actor = action.get("actor") or "Agent"
            atype = action.get("action_type") or "action"
            reason = action.get("reason") or ""
            sid = copilot_store.ensure_session_id("default")
            copilot_store.append_message(
                session_id=sid,
                role="assistant",
                content=(
                    f"**{actor}** wants to run `{atype}` — approve? "
                    f"(action #{action_id}) {reason}"
                ),
                intent="agent_event",
                payload={"action": "hitl_proposal", "action_id": action_id},
            )
        except Exception as exc:
            logger.debug("action_queue sync narration skipped: %s", exc)
    except Exception as exc:
        logger.debug("action_queue narration skipped: %s", exc)


def propose_action(
    actor: str,
    action_type: str,
    params: dict[str, Any] | None,
    reason: str,
    *,
    execute_fn: Callable[[], Any] | None = None,
    ttl_sec: float | None = None,
) -> dict[str, Any]:
    """Park a proposed action for human approval. Returns {pending, action_id}."""
    try:
        _ensure_tables()
    except Exception as exc:
        logger.error("action_queue schema ensure failed: %s", exc)
        return {"pending": False, "error": f"action queue unavailable: {exc}"}

    now = time.time()
    try:
        existing = _matching(STATUS_PENDING, actor, action_type, params or {})
    except Exception:
        existing = []
    if existing:
        try:
            action_id = int(existing[0]["id"])
        except (TypeError, ValueError, KeyError):
            action_id = None
        if action_id is not None:
            if execute_fn is not None:
                _execute_callbacks[action_id] = execute_fn
            return {"pending": True, "action_id": action_id, "duplicate": True}

    try:
        rejected = _matching(STATUS_REJECTED, actor, action_type, params or {})
    except Exception:
        rejected = []
    cooldown = float(AGENT_HITL_REJECT_COOLDOWN_SEC)
    if cooldown > 0 and rejected:
        latest = rejected[0]
        resolved_at = float(latest.get("resolved_at") or 0)
        if resolved_at and (now - resolved_at) < cooldown:
            try:
                skipped_id = int(latest["id"])
            except (TypeError, ValueError, KeyError):
                skipped_id = None
            return {
                "pending": False,
                "skipped": "recently_rejected",
                "action_id": skipped_id,
            }

    try:
        from app.db.connection import db_session

        with db_session() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO agent_pending_actions
                    (actor, action_type, params_json, reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(actor or "unknown"),
                    str(action_type or "unknown"),
                    json.dumps(params or {}, default=str),
                    str(reason or ""),
                    STATUS_PENDING,
                    now,
                ),
            )
            row_id = cur.lastrowid
    except Exception as exc:
        logger.error("action_queue propose failed (%s/%s): %s", actor, action_type, exc)
        return {"pending": False, "error": str(exc)}

    try:
        action_id = int(row_id)
    except (TypeError, ValueError):
        return {"pending": False, "error": "action insert returned no id"}

    if execute_fn is not None:
        _execute_callbacks[action_id] = execute_fn

    payload = {
        "action_id": action_id,
        "actor": actor,
        "action_type": action_type,
        "params": params or {},
        "reason": reason,
        "ttl_sec": float(ttl_sec if ttl_sec is not None else AGENT_HITL_TTL_SEC),
    }
    _publish_event("AGENT_ACTION_PROPOSED", payload)
    _narrate_proposal({"id": action_id, "actor": actor, "action_type": action_type,
                       "params": params or {}, "reason": reason})
    logger.info(
        "HITL action proposed: #%d %s/%s — %s", action_id, actor, action_type, reason
    )
    return {"pending": True, "action_id": action_id}


def get_action(action_id: int) -> dict[str, Any] | None:
    try:
        _ensure_tables()
        from app.db.connection import db_session

        with db_session(commit=False) as conn:
            row = (
                conn.cursor()
                .execute(
                    """
                    SELECT id, actor, action_type, params_json, reason, status,
                           created_at, resolved_at
                    FROM agent_pending_actions WHERE id = ?
                    """,
                    (int(action_id),),
                )
                .fetchone()
            )
    except Exception as exc:
        logger.debug("action_queue get failed for %s: %s", action_id, exc)
        return None
    return _row_to_action(row) if row else None


def list_actions(status: str | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
    try:
        _ensure_tables()
    except Exception:
        return []
    expire_old()
    expire_duplicate_pending()
    lim = max(1, min(int(limit or 50), 200))
    sql = (
        "SELECT id, actor, action_type, params_json, reason, status, created_at, resolved_at "
        "FROM agent_pending_actions"
    )
    args: list[Any] = []
    if status and str(status).lower() in _STATUSES:
        sql += " WHERE status = ?"
        args.append(str(status).lower())
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(lim)
    try:
        from app.db.connection import db_session

        with db_session(commit=False) as conn:
            rows = conn.cursor().execute(sql, args).fetchall()
        return [_row_to_action(r) for r in rows]
    except Exception as exc:
        logger.debug("action_queue list failed: %s", exc)
        return []


def _resolve(action_id: int, status: str) -> tuple[dict[str, Any] | None, bool]:
    """Mark an action resolved; returns (row, transitioned).

    ``transitioned`` is True only when this call flipped a pending row —
    approving/rejecting an already-resolved action returns (row, False).
    """
    from app.db.connection import db_session

    with db_session() as conn:
        cur = conn.cursor()
        row = cur.execute(
            """
            SELECT id, actor, action_type, params_json, reason, status, created_at, resolved_at
            FROM agent_pending_actions WHERE id = ?
            """,
            (int(action_id),),
        ).fetchone()
        if not row:
            return None, False
        action = _row_to_action(row)
        if action["status"] != STATUS_PENDING:
            return action, False
        now = time.time()
        cur.execute(
            "UPDATE agent_pending_actions SET status = ?, resolved_at = ? WHERE id = ?",
            (status, now, int(action_id)),
        )
    action["status"] = status
    action["resolved_at"] = now
    return action, True


async def _execute_via_registry(action: dict[str, Any]) -> dict[str, Any] | None:
    """Run through the tool registry when a matching tool exists; else None."""
    action_type = str(action.get("action_type") or "")
    try:
        from app.services.agent.tools.catalog import get_registry
        from app.services.agent.tools.registry import ToolContext
    except Exception as exc:
        logger.debug("action_queue registry import failed: %s", exc)
        return None

    registry = get_registry()
    if registry.get(action_type) is None:
        return None
    state = _state_provider() if _state_provider is not None else None
    outcome = await registry.execute(
        action_type,
        action.get("params") or {},
        confirmed=True,
        context=ToolContext(state=state),
    )
    if outcome.get("ok"):
        return {"ok": True, "result": outcome.get("result"), "via": "registry"}
    return {"ok": False, "error": outcome.get("error") or "tool execution failed"}


async def approve_action(action_id: int) -> dict[str, Any]:
    """Resolve as approved and execute (registry tool → else actor callback)."""
    try:
        action, transitioned = _resolve(action_id, STATUS_APPROVED)
    except Exception as exc:
        logger.error("action_queue approve failed for %s: %s", action_id, exc)
        return {"ok": False, "error": str(exc)}
    if action is None:
        return {"ok": False, "error": f"Action {action_id} not found"}
    if not transitioned:
        return {"ok": False, "error": f"Action {action_id} already {action['status']}"}

    try:
        _supersede_siblings(action)
    except Exception as exc:
        logger.debug("action_queue sibling expire on approve skipped: %s", exc)

    outcome: dict[str, Any] | None = await _execute_via_registry(action)
    if outcome is None:
        execute_fn = _execute_callbacks.pop(int(action_id), None)
        if execute_fn is None:
            outcome = {
                "ok": False,
                "error": (
                    f"No executor for action type '{action.get('action_type')}' "
                    "(no matching tool and no actor callback — process restart?)"
                ),
            }
        else:
            try:
                result = execute_fn()
                if inspect.isawaitable(result):
                    result = await result
                outcome = {"ok": True, "result": result, "via": "callback"}
            except Exception as exc:
                logger.exception("action_queue approved callback failed: #%s", action_id)
                outcome = {"ok": False, "error": str(exc)}

    _publish_event(
        "AGENT_ACTION_RESOLVED",
        {
            "action_id": action_id,
            "actor": action.get("actor"),
            "action_type": action.get("action_type"),
            "decision": STATUS_APPROVED,
            "ok": outcome.get("ok"),
            "error": outcome.get("error"),
        },
    )
    return {
        "ok": bool(outcome.get("ok")),
        "action": action,
        "result": outcome.get("result"),
        "error": outcome.get("error"),
    }


def reject_action(action_id: int) -> dict[str, Any]:
    try:
        action, transitioned = _resolve(action_id, STATUS_REJECTED)
    except Exception as exc:
        logger.error("action_queue reject failed for %s: %s", action_id, exc)
        return {"ok": False, "error": str(exc)}
    _execute_callbacks.pop(int(action_id), None)
    if action is None:
        return {"ok": False, "error": f"Action {action_id} not found"}
    if not transitioned:
        return {"ok": False, "error": f"Action {action_id} already {action['status']}"}
    try:
        _supersede_siblings(action)
    except Exception as exc:
        logger.debug("action_queue sibling expire on reject skipped: %s", exc)
    _publish_event(
        "AGENT_ACTION_RESOLVED",
        {
            "action_id": action_id,
            "actor": action.get("actor"),
            "action_type": action.get("action_type"),
            "decision": STATUS_REJECTED,
        },
    )
    return {"ok": True, "action": action}


def expire_old(ttl_sec: float | None = None) -> int:
    """Mark stale pending rows expired. Returns count expired."""
    ttl = float(ttl_sec if ttl_sec is not None else AGENT_HITL_TTL_SEC)
    cutoff = time.time() - ttl
    try:
        _ensure_tables()
        from app.db.connection import db_session

        with db_session() as conn:
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT id FROM agent_pending_actions WHERE status = ? AND created_at < ?",
                (STATUS_PENDING, cutoff),
            ).fetchall()
            ids = []
            for r in rows:
                try:
                    ids.append(int(r[0] if not isinstance(r, dict) else r.get("id")))
                except (TypeError, ValueError, KeyError):
                    continue
            if ids:
                marks = ",".join("?" for _ in ids)
                cur.execute(
                    f"UPDATE agent_pending_actions SET status = ?, resolved_at = ? "
                    f"WHERE id IN ({marks})",
                    (STATUS_EXPIRED, time.time(), *ids),
                )
    except Exception as exc:
        logger.debug("action_queue expire_old failed: %s", exc)
        return 0
    for aid in ids:
        _execute_callbacks.pop(aid, None)
        _publish_event(
            "AGENT_ACTION_RESOLVED",
            {"action_id": aid, "decision": STATUS_EXPIRED},
        )
    return len(ids)


def clear_callbacks() -> None:
    """Test helper — drop all registered execute callbacks."""
    _execute_callbacks.clear()
