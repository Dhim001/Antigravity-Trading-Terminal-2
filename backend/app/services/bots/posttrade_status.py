"""Aggregated Post-Trade Learning status (AI-FT-PTL-001 P0–P3).

Single read-only snapshot of every post-trade-learning subsystem so the UI
can show that the loops are alive: conformal gate calibration, regime
warnings, stacking online-update weights, isotonic calibration, RL replay
fill, post-trade label counts, and the copilot LoRA intent router.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _posttrade_label_count(bot_id: str) -> int:
    try:
        from app.db.connection import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM posttrade_labels WHERE bot_id = ?",
                (str(bot_id),),
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except Exception:
        return 0


def _bot_status(bot_id: str, bot: dict[str, Any]) -> dict[str, Any]:
    symbol = str(bot.get("symbol") or "").upper()
    out: dict[str, Any] = {
        "bot_id": bot_id,
        "symbol": symbol,
        "strategy": bot.get("strategy"),
        "status": bot.get("status"),
    }

    # Conformal gate (P1 #7)
    try:
        from app.services.bots.conformal_gate import load_conformal

        cal = load_conformal(bot_id)
        out["conformal"] = (
            {"q_hat": round(cal.q_hat, 4), "threshold": round(cal.threshold, 4)}
            if cal is not None
            else None
        )
    except Exception:
        out["conformal"] = None

    # Cross-strategy regime warning (P1 #8)
    try:
        from app.services.bots.agent_event_subscribers import regime_warning_active

        out["regime_warning"] = bool(symbol and regime_warning_active(symbol))
    except Exception:
        out["regime_warning"] = False

    # Stacking meta-learner online update (P2 #9)
    try:
        from app.services.bots.stacking_meta_learner import load_stacking_model

        sm = load_stacking_model(bot_id)
        out["stacking"] = (
            {
                "mode": sm.mode,
                "n_oos": sm.n_oos,
                "weights": [round(float(w), 4) for w in sm.weights],
                "base_names": list(sm.base_names),
            }
            if sm is not None
            else None
        )
    except Exception:
        out["stacking"] = None

    # Isotonic calibration flag (P2 #12)
    try:
        from app.services.bots.meta_label_model import get_meta_label_store

        meta = get_meta_label_store().get_metadata(bot_id) or {}
        out["isotonic_calibrated"] = bool(meta.get("isotonic_calibrated"))
        out["meta_label_trained_at"] = meta.get("trained_at")
    except Exception:
        out["isotonic_calibrated"] = False

    # RL replay buffer fill (P1 #4)
    try:
        from app.services.bots.rl_replay_store import count_transitions

        out["rl_replay_transitions"] = count_transitions(symbol) if symbol else 0
    except Exception:
        out["rl_replay_transitions"] = 0

    # Closed-loop feature feedback volume (P0 #3)
    out["posttrade_labels"] = _posttrade_label_count(bot_id)

    return out


def get_posttrade_learning_status(bot_manager: Any) -> dict[str, Any]:
    """Fleet-wide snapshot; safe to call from a handler (never raises)."""
    bots: list[dict[str, Any]] = []
    try:
        active = getattr(bot_manager, "active_bots", None) or {}
        for bot_id, bot in list(active.items()):
            try:
                bots.append(_bot_status(str(bot_id), bot if isinstance(bot, dict) else {}))
            except Exception:
                logger.debug("posttrade status failed for bot %s", bot_id, exc_info=True)
    except Exception:
        logger.debug("posttrade status bot enumeration failed", exc_info=True)

    # Copilot LoRA intent router (P3 #14)
    try:
        from app.services.agent.copilot_intent_lora import intent_router_status

        lora = intent_router_status()
    except Exception:
        lora = {"trained": False, "labels": [], "training_pairs": 0}

    return {
        "ok": True,
        "bots": bots,
        "copilot_intent_router": lora,
    }
