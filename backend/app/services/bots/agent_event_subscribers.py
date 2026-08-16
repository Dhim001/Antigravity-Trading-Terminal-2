"""Real AgentEventBus subscribers — wired once at bootstrap.

Handlers are defensive: any exception is caught and logged here (and again by
the bus's ``_safe_run``), never propagated to the publisher.

State kept in-process (paused cache, streak-escalation ignore set) is consumed
by RegimeRotationAgent and AlphaDecayMonitor; the durable ``agent_events``
table remains the cross-restart source of truth.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# bot_id -> pause timestamp (monotonic wall clock of when we saw BOT_PAUSED)
_paused_bots: dict[str, float] = {}

# bot_id -> epoch until which AlphaDecayMonitor should skip evaluation
_streak_ignore_until: dict[str, float] = {}

# How long a streak escalation suppresses alpha-decay evaluation when the
# event payload carries no explicit cool_until_ts.
STREAK_IGNORE_DEFAULT_SEC = 900.0


def mark_bot_paused(bot_id: str, ts: float | None = None) -> None:
    if bot_id:
        _paused_bots[bot_id] = float(ts if ts is not None else time.time())


def recently_paused_bot_ids(lookback_sec: float) -> set[str]:
    """Bot ids with a BOT_PAUSED event seen by this process within lookback."""
    cutoff = time.time() - lookback_sec
    stale = [bot_id for bot_id, ts in _paused_bots.items() if ts < cutoff]
    for bot_id in stale:
        _paused_bots.pop(bot_id, None)
    return set(_paused_bots)


def mark_streak_escalation(bot_id: str, until_ts: float | None = None) -> None:
    if not bot_id:
        return
    until = float(until_ts) if until_ts else time.time() + STREAK_IGNORE_DEFAULT_SEC
    if until <= time.time():
        return
    _streak_ignore_until[bot_id] = until


def is_streak_escalation_ignored(bot_id: str) -> bool:
    """True while AlphaDecayMonitor should skip this bot (post-streak cooldown)."""
    until = _streak_ignore_until.get(bot_id)
    if until is None:
        return False
    if until <= time.time():
        _streak_ignore_until.pop(bot_id, None)
        return False
    return True


async def _narrate(event_type: str, payload: dict[str, Any]) -> None:
    """Push into Copilot narration when available; otherwise just log."""
    try:
        from app.services.agent.copilot import agent_narrate_event

        await agent_narrate_event(event_type, payload)
    except Exception as exc:
        logger.debug("Copilot narration skipped for %s: %s", event_type, exc)


async def _on_bot_paused(event) -> None:
    bot_id = str(event.payload.get("bot_id") or "")
    mark_bot_paused(bot_id, ts=event.timestamp or None)
    logger.info(
        "AgentEvent BOT_PAUSED from %s: bot=%s reason=%s",
        event.source_agent,
        bot_id,
        event.payload.get("reason"),
    )
    await _narrate(
        "RiskSentinel",
        {
            "action": "bot_paused",
            "bot_id": bot_id,
            "reason": event.payload.get("reason"),
        },
    )


async def _on_streak_escalate(event) -> None:
    bot_id = str(event.payload.get("bot_id") or "")
    cool_until = event.payload.get("cool_until_ts")
    try:
        cool_until = float(cool_until) if cool_until is not None else None
    except (TypeError, ValueError):
        cool_until = None
    mark_streak_escalation(bot_id, cool_until)
    logger.warning(
        "AgentEvent STREAK_ESCALATE from %s: bot=%s streak=%s verdict=%s — "
        "alpha-decay evaluation suppressed until cool-down expires",
        event.source_agent,
        bot_id,
        event.payload.get("streak"),
        event.payload.get("verdict"),
    )
    await _narrate(
        "PreTradeIntel",
        {
            "action": "streak_escalate",
            "bot_id": bot_id,
            "symbol": event.payload.get("symbol"),
            "streak": event.payload.get("streak"),
            "verdict": event.payload.get("verdict"),
        },
    )


async def _on_posttrade_lesson(event) -> None:
    lesson = event.payload.get("lesson")
    lesson_text = lesson.get("lesson") if isinstance(lesson, dict) else None
    logger.info(
        "AgentEvent POSTTRADE_LESSON from %s: bot=%s symbol=%s lesson=%s",
        event.source_agent,
        event.payload.get("bot_id"),
        event.payload.get("symbol"),
        (lesson_text or "")[:200],
    )
    await _narrate(
        "PostTradeLearner",
        {
            "action": "posttrade_lesson",
            "bot_id": event.payload.get("bot_id"),
            "symbol": event.payload.get("symbol"),
            "lesson": lesson_text,
        },
    )


def register_agent_event_subscribers(agent_event_bus: Any) -> None:
    """Subscribe the production handlers to the shared bus (idempotent)."""
    if agent_event_bus is None:
        return
    if getattr(agent_event_bus, "_real_subscribers_registered", False):
        return
    agent_event_bus.subscribe("BOT_PAUSED", _on_bot_paused)
    agent_event_bus.subscribe("STREAK_ESCALATE", _on_streak_escalate)
    agent_event_bus.subscribe("POSTTRADE_LESSON", _on_posttrade_lesson)
    agent_event_bus._real_subscribers_registered = True
    logger.info("AgentEventBus subscribers registered (BOT_PAUSED, STREAK_ESCALATE, POSTTRADE_LESSON)")
