"""RL replay buffer — persistent (state, action, reward, next_state) store.

Implements AI-FT-PTL-001 §3.2 (P1 #4). Live RL bots record transitions as they
trade; on a retrain trigger, the PPO trainer samples mini-batches from this
buffer (plus latest candle episodes) and applies a KL-constrained update so the
policy adapts to live markets without catastrophic forgetting.

Storage is the ``rl_replay`` SQLite table, capped at
``RL_REPLAY_MAX_TRANSITIONS`` rows per symbol (oldest evicted — ring buffer).
All public functions are defensive and never raise into the trade path.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np

from app.config import (
    RL_REPLAY_ENABLED,
    RL_REPLAY_MAX_TRANSITIONS,
    TCA_REWARD_FEEDBACK_ENABLED,
    TCA_REWARD_LOOKBACK_DAYS,
)
from app.database import get_connection

logger = logging.getLogger(__name__)

# Reward shaping from post-trade lessons (AI-FT-PTL-001 §3.2, P1 #5).
# Same Δlog-equity units as TradingEnv / live_close_log_reward (not R-multiples).
OUTCOME_REWARD_MODIFIERS: dict[str, float] = {
    "clean_win": 0.002,
    "regime_mismatch": -0.003,
    "stop_too_tight": -0.001,
    "good_entry_bad_exit": -0.0015,
    "giveback_win": -0.001,
}

# Latest (obs, action) per live bot — written by the RL strategy each bar,
# consumed by record_live_close when the position closes.
_pending_actions: dict[str, dict[str, Any]] = {}


def _flatten_position_obs(obs: Any) -> np.ndarray:
    """Copy an observation with position features zeroed (flat next-state)."""
    arr = np.asarray(obs, dtype=np.float32).copy()
    if arr.size >= 3:
        arr[-3:] = 0.0
    return arr


def note_pending_action(
    bot_id: str,
    symbol: str,
    obs: Any,
    action: int,
) -> None:
    """Stash the latest live (obs, action); complete the previous bar's step."""
    if not RL_REPLAY_ENABLED or not bot_id:
        return
    try:
        obs_arr = np.asarray(obs, dtype=np.float32).copy()
        prev = _pending_actions.get(bot_id)
        if prev and prev.get("obs") is not None:
            record_transition(
                bot_id=bot_id,
                symbol=prev.get("symbol") or symbol,
                obs=prev["obs"],
                action=int(prev["action"]),
                reward=0.0,
                next_obs=obs_arr,
                done=False,
            )
        _pending_actions[bot_id] = {
            "symbol": str(symbol).upper(),
            "obs": obs_arr,
            "action": int(action),
        }
    except Exception:
        logger.debug("note_pending_action failed for %s", bot_id, exc_info=True)


def record_live_close(
    bot_id: str,
    symbol: str,
    *,
    reward: float,
    outcome_class: str | None = None,
    next_obs: Any | None = None,
) -> None:
    """Complete the pending transition for a closed live trade.

    Applies outcome-class reward shaping (P1 #5) and subtracts the aggregate
    implementation shortfall from the gross reward (P1 #6). Never raises.
    """
    if not RL_REPLAY_ENABLED:
        return
    pending = _pending_actions.pop(bot_id, None)
    if not pending:
        return
    try:
        shaped = float(reward)
        modifier = OUTCOME_REWARD_MODIFIERS.get(str(outcome_class or ""), 0.0)
        shaped += modifier

        if TCA_REWARD_FEEDBACK_ENABLED:
            from app.services.bots.execution_tca import mean_is_bps_for_symbol

            is_bps = mean_is_bps_for_symbol(symbol, lookback_days=TCA_REWARD_LOOKBACK_DAYS)
            if is_bps:
                shaped -= float(is_bps) / 10_000.0

        close_next = next_obs if next_obs is not None else _flatten_position_obs(pending["obs"])
        record_transition(
            bot_id=bot_id,
            symbol=pending["symbol"],
            obs=pending["obs"],
            action=pending["action"],
            reward=shaped,
            next_obs=close_next,
            done=True,
            outcome_class=outcome_class,
        )
    except Exception:
        logger.debug("record_live_close failed for %s", bot_id, exc_info=True)


def _utcnow_naive() -> str:
    """Naive-UTC timestamp string matching CURRENT_TIMESTAMP's format."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def record_transition(
    *,
    bot_id: str,
    symbol: str,
    obs: Any,
    action: int,
    reward: float,
    next_obs: Any | None = None,
    done: bool = False,
    outcome_class: str | None = None,
) -> None:
    """Persist one transition. Never raises."""
    if not RL_REPLAY_ENABLED:
        return
    try:
        obs_json = json.dumps(np.asarray(obs, dtype=np.float32).tolist())
        next_json = (
            json.dumps(np.asarray(next_obs, dtype=np.float32).tolist())
            if next_obs is not None
            else None
        )
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO rl_replay
            (bot_id, symbol, obs, action, reward, next_obs, done, outcome_class, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bot_id,
                str(symbol).upper(),
                obs_json,
                int(action),
                float(reward),
                next_json,
                1 if done else 0,
                outcome_class,
                _utcnow_naive(),
            ),
        )
        # Ring buffer: evict oldest rows beyond the per-symbol cap.
        cursor.execute(
            """
            DELETE FROM rl_replay
            WHERE symbol = ? AND id NOT IN (
                SELECT id FROM rl_replay WHERE symbol = ?
                ORDER BY id DESC LIMIT ?
            )
            """,
            (str(symbol).upper(), str(symbol).upper(), max(1, RL_REPLAY_MAX_TRANSITIONS)),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.debug("record_transition failed for %s", symbol, exc_info=True)


def count_transitions(symbol: str) -> int:
    """Number of stored transitions for a symbol (0 on error)."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM rl_replay WHERE symbol = ?",
            (str(symbol).upper(),),
        )
        row = cursor.fetchone()
        conn.close()
        n = row[0] if row else 0
        return int(n or 0)
    except Exception:
        logger.debug("count_transitions failed for %s", symbol, exc_info=True)
        return 0


def load_transitions(symbol: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Load transitions oldest→newest for training. Returns [] on error."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        sql = (
            "SELECT obs, action, reward, next_obs, done, outcome_class "
            "FROM rl_replay WHERE symbol = ? ORDER BY id DESC"
        )
        params: tuple = (str(symbol).upper(),)
        if limit:
            sql += " LIMIT ?"
            params = (str(symbol).upper(), int(limit))
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        conn.close()
    except Exception:
        logger.debug("load_transitions failed for %s", symbol, exc_info=True)
        return []

    out: list[dict[str, Any]] = []
    for row in reversed(rows or []):  # restore oldest→newest
        item = dict(row) if isinstance(row, dict) else {
            "obs": row[0], "action": row[1], "reward": row[2],
            "next_obs": row[3], "done": row[4], "outcome_class": row[5],
        }
        try:
            item["obs"] = np.asarray(json.loads(item["obs"]), dtype=np.float32)
            item["next_obs"] = (
                np.asarray(json.loads(item["next_obs"]), dtype=np.float32)
                if item.get("next_obs")
                else None
            )
            item["action"] = int(item["action"])
            item["reward"] = float(item["reward"])
            item["done"] = bool(item["done"])
        except Exception:
            continue
        out.append(item)
    return out
