"""Desk Supervisor — thin coordinator between silent actors and the HITL queue.

Actors call ``propose_or_execute(...)`` instead of mutating directly. When
``AUTO_AGENT_ACTIONS`` is true (or the action is a declared emergency like the
kill switch), ``execute_fn`` runs immediately — preserving current behavior.
Otherwise the action is parked in ``action_queue`` for human approval and the
actor gets back ``{pending: True, action_id}``.

Import-safe by design: actors wrap the import in try/except and fall back to
direct execution when this module (or the queue) is unavailable.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Awaitable, Callable

from app.config import AUTO_AGENT_ACTIONS

logger = logging.getLogger(__name__)

ExecuteFn = Callable[[], Any]


def auto_actions_enabled() -> bool:
    """Read the flag at call time so tests / runtime toggles apply."""
    try:
        import app.config as cfg

        return bool(getattr(cfg, "AUTO_AGENT_ACTIONS", AUTO_AGENT_ACTIONS))
    except Exception:
        return bool(AUTO_AGENT_ACTIONS)


async def propose_or_execute(
    actor: str,
    action_type: str,
    params: dict[str, Any] | None,
    reason: str,
    execute_fn: ExecuteFn,
    *,
    emergency: bool = False,
) -> dict[str, Any]:
    """Run ``execute_fn`` now, or park the action for human approval.

    Returns either ``{pending: True, action_id}`` (queued) or
    ``{executed: True, ok, result|error}`` (immediate path).
    """
    if emergency or auto_actions_enabled():
        try:
            result = execute_fn()
            if inspect.isawaitable(result):
                result = await result
            return {"executed": True, "ok": True, "result": result}
        except Exception as exc:
            logger.error(
                "DeskSupervisor immediate execution failed (%s/%s): %s",
                actor, action_type, exc,
            )
            return {"executed": True, "ok": False, "error": str(exc)}

    try:
        from app.services.agent import action_queue

        outcome = action_queue.propose_action(
            actor, action_type, params or {}, reason, execute_fn=execute_fn
        )
    except Exception as exc:
        # Queue unavailable — safest fallback is to execute as before rather
        # than silently dropping a risk action.
        logger.warning(
            "DeskSupervisor queue unavailable (%s) — executing %s/%s directly",
            exc, actor, action_type,
        )
        try:
            result = execute_fn()
            if inspect.isawaitable(result):
                result = await result
            return {"executed": True, "ok": True, "result": result, "fallback": True}
        except Exception as exc2:
            return {"executed": True, "ok": False, "error": str(exc2), "fallback": True}

    if outcome.get("pending"):
        return {
            "pending": True,
            "action_id": outcome.get("action_id"),
            "duplicate": bool(outcome.get("duplicate")),
        }

    # Human already dismissed this decision — do NOT fall through to execute.
    if outcome.get("skipped"):
        return {
            "pending": False,
            "skipped": outcome.get("skipped"),
            "action_id": outcome.get("action_id"),
        }

    # Propose failed cleanly (e.g. DB down) — same direct-execution fallback.
    logger.warning(
        "DeskSupervisor propose failed for %s/%s (%s) — executing directly",
        actor, action_type, outcome.get("error"),
    )
    try:
        result = execute_fn()
        if inspect.isawaitable(result):
            result = await result
        return {"executed": True, "ok": True, "result": result, "fallback": True}
    except Exception as exc:
        return {"executed": True, "ok": False, "error": str(exc), "fallback": True}


class DeskSupervisor:
    """Optional OO facade for actors that prefer an instance."""

    async def propose_or_execute(
        self,
        actor: str,
        action_type: str,
        params: dict[str, Any] | None,
        reason: str,
        execute_fn: ExecuteFn,
        *,
        emergency: bool = False,
    ) -> dict[str, Any]:
        return await propose_or_execute(
            actor, action_type, params, reason, execute_fn, emergency=emergency
        )


_SUPERVISOR: DeskSupervisor | None = None


def get_supervisor() -> DeskSupervisor:
    global _SUPERVISOR
    if _SUPERVISOR is None:
        _SUPERVISOR = DeskSupervisor()
    return _SUPERVISOR
