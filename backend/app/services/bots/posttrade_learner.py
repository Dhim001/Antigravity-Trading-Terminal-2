"""Post-Trade Learning Agent — close → classify → lesson → optional config apply."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from app.config import (
    POSTTRADE_LEARNER_AUTO_APPLY,
    POSTTRADE_LEARNER_AUTO_RETRAIN,
    POSTTRADE_LEARNER_CONFIDENCE_BUMP,
    POSTTRADE_LEARNER_ENABLED,
    POSTTRADE_LEARNER_RETRAIN_EVERY_N,
    POSTTRADE_LEARNER_STOP_WIDEN_PCT,
    POSTTRADE_LEARNER_USE_LLM,
)
from app.database import get_connection
from app.services.altdata.store import get_aggregate_sentiment
from app.services.bots.analytics import _parse_insight_snapshot, get_bot_stats
from app.services.bots.strategy_advisor import validate_suggested_params
from app.services.journal.store import upsert_entry
from app.services.notifications import types as ntypes
from app.services.notifications.dispatcher import emit_notification
from app.services.notifications.events import NotificationEvent
from app.services.agent.reasoning import AgentReasoning, Observation
from app.services.agent.reasoning_store import save_agent_reasoning
from app.services.agent.agent_event_bus import AgentEvent

logger = logging.getLogger(__name__)

LESSON_SYSTEM_PROMPT = """You are a quantitative post-trade coach.
Given a closed trade's MAE/MFE, PnL, regime, and classification, write 2-4 sentences of
actionable lesson text. Be specific about stops, filters, or regime. No fluff.
Rules (strict):
- MAE = Maximum Adverse Excursion (% against the position). MFE = Maximum Favorable Excursion (% with the position). Never redefine these.
- First sentence MUST state whether the trade was a WIN or LOSS and the PnL sign/magnitude from context.
- outcome_class is authoritative — never call a loss a win or a win a loss.
- If suggested_patch is non-empty, recommend ONLY those exact keys/values (do not invent opposite direction changes).
- If suggested_patch is empty, do not invent config numbers.
- Do not invent numbers not present in the data. Start directly with the lesson."""


def pnl_from_exit(
    exit_side: str,
    quantity: float,
    exit_price: float,
    entry_price: float,
) -> float:
    """Exit-side PnL: SELL closes a long, BUY covers a short."""
    qty = float(quantity)
    ex = float(exit_price)
    en = float(entry_price)
    if str(exit_side).upper() == "SELL":
        return (ex - en) * qty
    return (en - ex) * qty


@dataclass
class TradeLesson:
    outcome_class: str = "unknown"
    mae_pct: float | None = None
    mfe_pct: float | None = None
    pnl: float | None = None
    lesson: str = ""
    config_patch: dict[str, Any] = field(default_factory=dict)
    applied: bool = False
    retrained: bool = False
    journal_id: str | None = None
    reasoning: AgentReasoning | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.reasoning:
            d["reasoning"] = self.reasoning.to_dict()
        return d


def compute_mae_mfe(
    *,
    entry_price: float,
    is_long: bool,
    high_watermark: float | None,
    low_watermark: float | None,
    exit_price: float | None = None,
) -> tuple[float | None, float | None]:
    """Return (mae_pct, mfe_pct) as positive percentages of entry."""
    if entry_price is None or entry_price <= 0:
        return None, None
    hi = high_watermark
    lo = low_watermark
    if hi is None and exit_price is not None:
        hi = exit_price
    if lo is None and exit_price is not None:
        lo = exit_price
    if hi is None or lo is None:
        return None, None

    try:
        hi_f = float(hi)
        lo_f = float(lo)
    except (TypeError, ValueError):
        return None, None

    if is_long:
        mfe = max(0.0, (hi_f - entry_price) / entry_price * 100.0)
        mae = max(0.0, (entry_price - lo_f) / entry_price * 100.0)
    else:
        mfe = max(0.0, (entry_price - lo_f) / entry_price * 100.0)
        mae = max(0.0, (hi_f - entry_price) / entry_price * 100.0)
    return round(mae, 4), round(mfe, 4)


def fetch_entry_context(bot_id: str, symbol: str) -> dict[str, Any]:
    """Latest opening fill for this bot/symbol (insight + price)."""
    if not bot_id or not symbol:
        return {}
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT side, price, quantity, timestamp, signal_bar_time, insight_snapshot
            FROM bot_trades
            WHERE bot_id = ? AND symbol = ? AND is_exit = 0
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (bot_id, str(symbol).upper()),
        )
        row = cursor.fetchone()
    except Exception as exc:
        logger.debug("posttrade entry context skipped: %s", exc)
        return {}
    finally:
        conn.close()

    if not row:
        return {}
    if isinstance(row, dict):
        item = dict(row)
    else:
        item = {
            "side": row[0],
            "price": row[1],
            "quantity": row[2],
            "timestamp": row[3],
            "signal_bar_time": row[4],
            "insight_snapshot": row[5],
        }
    item["insight_snapshot"] = _parse_insight_snapshot(item.get("insight_snapshot"))
    return item


GIVEBACK_CAPTURE_MAX = 0.30


def classify_outcome(
    *,
    pnl: float | None,
    mae_pct: float | None,
    mfe_pct: float | None,
    trigger_type: str | None,
    insight: dict[str, Any] | None,
    stop_loss_percent: float | None = None,
    realized_pct: float | None = None,
) -> tuple[str, dict[str, Any]]:
    """Heuristic outcome class for a closed trade."""
    reason: dict[str, Any] = {
        "pnl": pnl,
        "mae_pct": mae_pct,
        "mfe_pct": mfe_pct,
        "trigger_type": trigger_type,
        "realized_pct": realized_pct,
    }
    snap = insight if isinstance(insight, dict) else {}
    regime = str(
        snap.get("regime")
        or (snap.get("sub_reports") or {}).get("regime")
        or snap.get("market_regime")
        or ""
    ).lower()
    reason["regime"] = regime or None

    won = pnl is not None and float(pnl) > 0
    lost = pnl is not None and float(pnl) < 0
    mae = float(mae_pct) if mae_pct is not None else None
    mfe = float(mfe_pct) if mfe_pct is not None else None
    trig = str(trigger_type or "").upper()

    # Regime mismatch: loss while ranging / elevated vol without blocks
    if lost and any(tok in regime for tok in ("rang", "chop", "elevated", "high_vol")):
        reason["note"] = "Loss in hostile regime"
        return "regime_mismatch", reason

    # Stop too tight: SL exit with little favorable excursion vs stop width
    if lost and trig == "SL" and mae is not None:
        sl_w = float(stop_loss_percent) if stop_loss_percent else None
        if mfe is not None and mfe < max(0.15, (mae * 0.35)):
            reason["note"] = "SL hit with minimal favorable excursion"
            return "stop_too_tight", reason
        if sl_w is not None and mae >= sl_w * 0.85 and (mfe is None or mfe < sl_w * 0.5):
            reason["note"] = "MAE consumed nearly full stop with weak MFE"
            return "stop_too_tight", reason

    # Good entry, bad exit: had substantial MFE but finished red
    if lost and mae is not None and mfe is not None and mfe >= max(0.4, mae * 1.5):
        reason["note"] = "Trade went favorably then reversed into a loss"
        return "good_entry_bad_exit", reason

    if won and mfe is not None and mfe > 0 and realized_pct is not None:
        try:
            capture = float(realized_pct) / float(mfe)
        except (TypeError, ValueError, ZeroDivisionError):
            capture = None
        if capture is not None:
            reason["capture_frac"] = round(capture, 4)
            if capture < GIVEBACK_CAPTURE_MAX:
                reason["note"] = (
                    f"Captured {capture:.0%} of MFE — profit give-back, do not tighten the initial stop"
                )
                return "giveback_win", reason

    if won and mfe is not None and mae is not None and mfe >= mae and mfe > 0:
        return "clean_win", reason
    if won:
        # Missing/zero excursion marks → do not over-claim "clean".
        if mfe is None or mae is None or (mfe <= 0 and mae <= 0):
            reason["note"] = "Win with incomplete excursion marks"
            return "win", reason
        return "messy_win", reason
    if lost:
        if mfe is None or mae is None or (mfe <= 0 and mae <= 0):
            reason["note"] = "Loss with incomplete excursion marks"
            return "loss", reason
        return "clean_loss", reason
    return "flat", reason


def _default_min_confidence(strategy: str) -> float:
    s = (strategy or "").upper()
    if s == "TCN_MULTI_HORIZON":
        return 0.002
    if s == "RL_PPO_AGENT":
        from app.services.bots.rl_risk import DEFAULT_MIN_CONFIDENCE

        return DEFAULT_MIN_CONFIDENCE
    return 0.55


def _bump_min_confidence(strategy: str, conf: float) -> float:
    """Strategy-aware min_confidence nudge (prob vs return-magnitude scales)."""
    s = (strategy or "").upper()
    bump = float(POSTTRADE_LEARNER_CONFIDENCE_BUMP)
    if s == "TCN_MULTI_HORIZON":
        return round(min(0.05, max(1e-4, conf * 1.2 + 0.0005)), 6)
    if s == "RL_PPO_AGENT":
        return round(min(0.7, conf + min(bump, 0.02)), 4)
    return round(min(0.95, conf + bump), 4)


def build_config_patch(
    outcome_class: str,
    bot_config: dict[str, Any] | None,
    *,
    strategy: str = "",
) -> dict[str, Any]:
    """Map outcome class → safe config patch (validated via strategy_advisor bounds)."""
    cfg = dict(bot_config or {})
    raw: dict[str, Any] = {}

    from app.services.bots.rl_risk import is_rl_strategy, uses_percent_stops

    rl_atr = is_rl_strategy(strategy) and not uses_percent_stops(cfg)

    if outcome_class == "stop_too_tight":
        if rl_atr:
            cur = float(cfg.get("atr_stop_mult") or cfg.get("chandelier_multiplier") or 1.5)
            raw["atr_stop_mult"] = round(min(5.0, cur + 0.25), 4)
            raw["chandelier_multiplier"] = raw["atr_stop_mult"]
        else:
            cur = float(cfg.get("stop_loss_percent") or cfg.get("trailing_stop_percent") or 1.5)
            raw["stop_loss_percent"] = round(cur + POSTTRADE_LEARNER_STOP_WIDEN_PCT, 4)
            if cfg.get("trailing_stop_percent") is not None:
                trail = float(cfg.get("trailing_stop_percent") or cur)
                raw["trailing_stop_percent"] = round(trail + POSTTRADE_LEARNER_STOP_WIDEN_PCT, 4)

    elif outcome_class == "good_entry_bad_exit":
        if rl_atr:
            cur = float(cfg.get("take_profit_r") or 1.5)
            raw["take_profit_r"] = round(min(5.0, cur * 1.1), 4)
        else:
            # Capture more of the move: prefer trailing if absent; else nudge TP up slightly.
            if not cfg.get("trailing_stop_percent"):
                sl = float(cfg.get("stop_loss_percent") or 1.5)
                raw["trailing_stop_percent"] = round(max(0.5, sl * 0.75), 4)
            tp = cfg.get("take_profit_percent")
            if tp is not None:
                raw["take_profit_percent"] = round(float(tp) * 1.1, 4)

    elif outcome_class == "regime_mismatch":
        raw["block_ranging_markets"] = True
        conf = float(cfg.get("min_confidence") or _default_min_confidence(strategy))
        raw["min_confidence"] = _bump_min_confidence(strategy, conf)

    elif outcome_class in ("clean_loss", "loss"):
        conf = float(cfg.get("min_confidence") or _default_min_confidence(strategy))
        raw["min_confidence"] = _bump_min_confidence(strategy, conf)

    if not raw:
        return {}

    clean, _warnings = validate_suggested_params(strategy or "", raw, base_config=cfg)
    return clean


def template_lesson(
    outcome_class: str,
    *,
    symbol: str,
    pnl: float | None,
    mae_pct: float | None,
    mfe_pct: float | None,
    patch: dict[str, Any],
    reason: dict[str, Any],
) -> str:
    pnl_s = f"{pnl:+.2f}" if pnl is not None else "n/a"
    mae_s = f"{mae_pct:.2f}%" if mae_pct is not None else "n/a"
    mfe_s = f"{mfe_pct:.2f}%" if mfe_pct is not None else "n/a"
    if pnl is not None and float(pnl) > 0:
        result = "WIN"
    elif pnl is not None and float(pnl) < 0:
        result = "LOSS"
    else:
        result = "FLAT"
    note = reason.get("note") or outcome_class.replace("_", " ")
    patch_bit = ""
    if patch:
        bits = ", ".join(f"{k}={v}" for k, v in patch.items())
        patch_bit = f" Suggested adjust: {bits}."
    return (
        f"{symbol} {result} (PnL {pnl_s}). {note}. "
        f"MAE {mae_s}, MFE {mfe_s} (class={outcome_class}).{patch_bit}"
    )


def _lesson_contradicts_outcome(lesson_text: str, outcome_class: str, pnl: float | None) -> bool:
    """Reject LLM copy that flips win/loss relative to the classified outcome."""
    text = (lesson_text or "").lower()
    if not text:
        return True
    lost = (pnl is not None and float(pnl) < 0) or "loss" in outcome_class
    won = (pnl is not None and float(pnl) > 0) or "win" in outcome_class
    if lost and re.search(r"\b(clean\s+)?win\b", text) and not re.search(r"\b(loss|lost|losing)\b", text):
        return True
    if won and re.search(r"\b(clean\s+)?loss\b", text) and not re.search(r"\b(win|won|winning|profit)\b", text):
        return True
    return False


async def _llm_lesson(context: dict[str, Any]) -> str | None:
    if not POSTTRADE_LEARNER_USE_LLM:
        return None
    try:
        from app.services.agent.llm.router import _chat
        from app.services.agent.llm.payloads import dumps_payload

        result = await _chat(
            system=LESSON_SYSTEM_PROMPT,
            user=f"TRADE CONTEXT:\n{dumps_payload(context)}",
            task="narrator",
            max_tokens=280,
            temperature=0.35,
        )
        text = (result.text or "").strip()
        return text or None
    except Exception as exc:
        logger.debug("posttrade LLM lesson skipped: %s", exc)
        return None


def _count_exits(bot_id: str) -> int:
    stats = get_bot_stats(bot_id) or {}
    try:
        return int(stats.get("exit_count") or 0)
    except (TypeError, ValueError):
        return 0


async def learn_from_closed_trade(
    bot_manager: Any,
    bot_id: str,
    *,
    symbol: str,
    exit_side: str,
    exit_price: float,
    entry_price: float | None,
    quantity: float,
    pnl: float | None,
    trigger_type: str | None = None,
    high_watermark: float | None = None,
    low_watermark: float | None = None,
    entry_insight: dict[str, Any] | None = None,
    order_id: str | None = None,
) -> TradeLesson:
    """Run the post-trade learning loop for one closed bot trade."""
    if not POSTTRADE_LEARNER_ENABLED:
        return TradeLesson(outcome_class="disabled", lesson="Post-trade learner disabled")

    bot = None
    if bot_manager is not None and hasattr(bot_manager, "_get_bot_dict"):
        bot = bot_manager._get_bot_dict(bot_id)
    if not bot and bot_manager is not None:
        bot = (getattr(bot_manager, "active_bots", {}) or {}).get(bot_id)
    bot = bot or {"id": bot_id, "symbol": symbol, "config": {}, "strategy": ""}

    cfg = bot.get("config") or {}
    if isinstance(cfg, str):
        import json

        try:
            cfg = json.loads(cfg) if cfg else {}
        except json.JSONDecodeError:
            cfg = {}

    entry_ctx = fetch_entry_context(bot_id, symbol)
    insight = entry_insight or entry_ctx.get("insight_snapshot")
    if not isinstance(insight, dict):
        insight = {}

    ep = float(entry_price) if entry_price is not None else None
    if ep is None and entry_ctx.get("price") is not None:
        try:
            ep = float(entry_ctx["price"])
        except (TypeError, ValueError):
            ep = None
    if ep is None:
        ep = float(exit_price)

    # Prefer exit-side geometry over a stale/wrong passed pnl (sign flips).
    try:
        recomputed = pnl_from_exit(exit_side, quantity, float(exit_price), ep)
        if pnl is None:
            pnl = recomputed
        elif abs(float(pnl) - recomputed) > max(1e-6, abs(recomputed) * 1e-6 + 1e-4):
            logger.warning(
                "posttrade pnl mismatch bot=%s %s passed=%s recomputed=%s — using recomputed",
                bot_id,
                symbol,
                pnl,
                recomputed,
            )
            pnl = recomputed
    except (TypeError, ValueError):
        pass

    # Infer long/short from exit side first (authoritative for this close),
    # then entry fill side as fallback.
    exit_u = str(exit_side).upper()
    if exit_u == "SELL":
        is_long = True
    elif exit_u == "BUY":
        is_long = False
    else:
        entry_side = str(entry_ctx.get("side") or "").upper()
        is_long = entry_side == "BUY" if entry_side in ("BUY", "SELL") else True

    mae_pct, mfe_pct = compute_mae_mfe(
        entry_price=ep,
        is_long=is_long,
        high_watermark=high_watermark,
        low_watermark=low_watermark,
        exit_price=float(exit_price),
    )

    sl_pct = cfg.get("trailing_stop_percent") or cfg.get("stop_loss_percent")
    try:
        sl_pct_f = float(sl_pct) if sl_pct is not None else None
    except (TypeError, ValueError):
        sl_pct_f = None

    realized_pct = None
    if ep is not None and ep > 0:
        try:
            if is_long:
                realized_pct = (float(exit_price) - ep) / ep * 100.0
            else:
                realized_pct = (ep - float(exit_price)) / ep * 100.0
        except (TypeError, ValueError):
            realized_pct = None

    outcome_class, reason = classify_outcome(
        pnl=pnl,
        mae_pct=mae_pct,
        mfe_pct=mfe_pct,
        trigger_type=trigger_type,
        insight=insight,
        stop_loss_percent=sl_pct_f,
        realized_pct=realized_pct,
    )

    patch = build_config_patch(
        outcome_class,
        cfg,
        strategy=str(bot.get("strategy") or ""),
    )

    try:
        sentiment = get_aggregate_sentiment(symbol, lookback_hours=12.0)
    except Exception:
        sentiment = {}

    result_label = (
        "WIN" if pnl is not None and float(pnl) > 0
        else ("LOSS" if pnl is not None and float(pnl) < 0 else "FLAT")
    )
    context = {
        "symbol": symbol,
        "bot_id": bot_id,
        "outcome_class": outcome_class,
        "result": result_label,
        "pnl": pnl,
        "mae_pct": mae_pct,
        "mfe_pct": mfe_pct,
        "trigger_type": trigger_type,
        "entry_price": ep,
        "exit_price": exit_price,
        "quantity": quantity,
        "exit_side": exit_side,
        "position_side": "LONG" if is_long else "SHORT",
        "regime": reason.get("regime"),
        "confidence": insight.get("confidence"),
        "score": insight.get("score"),
        "signal": insight.get("signal"),
        "sentiment": {
            "aggregate_score": sentiment.get("aggregate_score"),
            "mention_count": sentiment.get("mention_count"),
        },
        "suggested_patch": patch,
        "note": reason.get("note"),
    }

    lesson_text = await _llm_lesson(context)
    if (not lesson_text) or _lesson_contradicts_outcome(lesson_text, outcome_class, pnl):
        if lesson_text:
            logger.info(
                "posttrade LLM lesson rejected for %s (%s) — using template",
                bot_id,
                outcome_class,
            )
        lesson_text = template_lesson(
            outcome_class,
            symbol=symbol,
            pnl=pnl,
            mae_pct=mae_pct,
            mfe_pct=mfe_pct,
            patch=patch,
            reason=reason,
        )

    applied = False
    if patch and POSTTRADE_LEARNER_AUTO_APPLY and bot_manager is not None:
        async def _apply_posttrade_patch():
            await bot_manager.update_bot_config(bot_id, patch)
            await bot_manager.log_bot_event(
                bot_id,
                "INFO",
                f"Post-trade learner applied config: {patch}",
            )

        try:
            from app.services.agent import desk_supervisor

            apply_out = await desk_supervisor.propose_or_execute(
                "PostTradeLearner",
                "update_bot_config",
                {"bot_id": bot_id, "config_patch": dict(patch)},
                f"Post-trade lesson ({outcome_class}): apply config patch {patch}",
                _apply_posttrade_patch,
            )
        except Exception as sup_exc:
            logger.warning(
                "PostTradeLearner supervisor path failed (%s) — applying directly", sup_exc
            )
            apply_out = {"executed": True, "ok": True}
            try:
                await _apply_posttrade_patch()
            except Exception as exc:
                logger.warning("posttrade auto-apply failed for %s: %s", bot_id, exc)
                apply_out = {"executed": True, "ok": False}

        applied = bool(apply_out.get("executed") and apply_out.get("ok"))
        if apply_out.get("pending"):
            applied = False

    journal_id = None
    try:
        entry = upsert_entry({
            "bot_id": bot_id,
            "symbol": str(symbol).upper(),
            "order_id": order_id,
            "tags": ["posttrade-learner", "agent", outcome_class],
            "note": lesson_text,
            "lesson": (
                f"class={outcome_class}; mae={mae_pct}; mfe={mfe_pct}; "
                f"patch={patch}; applied={applied}"
            ),
        })
        journal_id = entry.get("id") if isinstance(entry, dict) else None
    except Exception as exc:
        logger.debug("posttrade journal write skipped: %s", exc)

    # Calibration buckets refresh on next gate use after invalidate (already done on exit).
    try:
        from app.services.bots.calibration import get_calibration_store

        get_calibration_store().invalidate(bot_id)
    except Exception:
        pass

    retrained = False
    if POSTTRADE_LEARNER_AUTO_RETRAIN:
        exits = _count_exits(bot_id)
        every = max(1, int(POSTTRADE_LEARNER_RETRAIN_EVERY_N))
        if exits > 0 and exits % every == 0:
            try:
                from app.services.bots.ml_retrain_scheduler import get_retrain_scheduler

                # Route through centralized coordinator for cooldown/dedup
                req = get_retrain_scheduler().request_retrain(
                    strategy="META_LABEL",
                    symbol=str(bot.get("symbol", bot_id)),
                    reason=(
                        f"periodic meta-label retrain after {exits} exits "
                        f"({bot.get('strategy') or 'bot'})"
                    ),
                    source="posttrade_learner",
                    timeframe=bot.get("timeframe") or (bot.get("config") or {}).get("timeframe"),
                )
                if req.get("queued"):
                    from app.services.bots.meta_label_model import train_meta_label_model

                    res = train_meta_label_model(bot_id)
                    retrained = bool(res.get("ok"))
                    if retrained:
                        get_retrain_scheduler().record_retrain(
                            "META_LABEL",
                            str(bot.get("symbol", bot_id)),
                        )
                        if bot_manager is not None:
                            await bot_manager.log_bot_event(
                                bot_id,
                                "INFO",
                                f"Post-trade learner retrained meta-label model after {exits} exits.",
                            )
                else:
                    logger.debug(
                        "posttrade retrain skipped (%s): %s",
                        req.get("reason"), bot_id,
                    )
            except Exception as exc:
                logger.debug("posttrade retrain skipped: %s", exc)

    # Adaptive conformal gate recalibration (AI-FT-PTL-001 §4.5): refresh the
    # gate's q_hat from recent prediction/outcome pairs every N closed trades.
    try:
        from app.config import CONFORMAL_RECALIB_EVERY_N, CONFORMAL_RECALIB_ENABLED

        if CONFORMAL_RECALIB_ENABLED:
            exits_n = _count_exits(bot_id)
            every_n = max(1, int(CONFORMAL_RECALIB_EVERY_N))
            if exits_n > 0 and exits_n % every_n == 0:
                from app.services.bots.conformal_gate import recalibrate_conformal_gate

                recal = recalibrate_conformal_gate(bot_id)
                if recal.get("updated") and bot_manager is not None:
                    await bot_manager.log_bot_event(
                        bot_id,
                        "INFO",
                        f"Conformal gate recalibrated after {exits_n} exits: "
                        f"threshold={recal.get('threshold')}",
                    )
    except Exception:
        logger.debug("conformal recalibration skipped for %s", bot_id, exc_info=True)

    uncertainties = []
    if sentiment is None or not sentiment:
        uncertainties.append("Missing or sparse recent sentiment data.")
    if entry_insight is None and insight is None:
        uncertainties.append("Missing entry insight context.")

    obs1 = Observation(
        source="trade_performance",
        signal="pnl",
        confidence=1.0,
        detail=f"PnL: {pnl}, MAE: {mae_pct}%, MFE: {mfe_pct}%",
        data={"pnl": pnl, "mae": mae_pct, "mfe": mfe_pct}
    )
    obs2 = Observation(
        source="trade_context",
        signal="outcome",
        confidence=0.9,
        detail=f"Outcome class: {outcome_class}",
        data={"outcome_class": outcome_class, "regime": reason.get("regime")}
    )
    obs3 = Observation(
        source="learning",
        signal="config_patch",
        confidence=0.85,
        detail=f"Generated config patch: {patch}" if patch else "No config patch suggested.",
        data={"patch": patch}
    )

    reasoning_obj = AgentReasoning(
        observations=[obs1, obs2, obs3],
        synthesis=lesson_text,
        decision="LEARN_AND_ADJUST",
        confidence=0.85 if patch else 0.5,
        uncertainty_sources=uncertainties,
        recommendation_strength="strong" if patch and applied else ("moderate" if patch else "weak"),
    )

    result = TradeLesson(
        outcome_class=outcome_class,
        mae_pct=mae_pct,
        mfe_pct=mfe_pct,
        pnl=float(pnl) if pnl is not None else None,
        lesson=lesson_text,
        config_patch=patch,
        applied=applied,
        retrained=retrained,
        journal_id=journal_id,
        reasoning=reasoning_obj,
    )

    save_agent_reasoning(bot_id, "POSTTRADE_LEARNER", reasoning_obj)

    # Closed-loop feature feedback (AI-FT-PTL-001 §4.2): persist a structured
    # label row so the next retrain's triple-barrier labeller can use it.
    try:
        from app.services.bots.ml_posttrade_labels import record_posttrade_label

        _bar_time = entry_ctx.get("signal_bar_time")
        try:
            _bar_time = int(_bar_time) if _bar_time is not None else None
        except (TypeError, ValueError):
            _bar_time = None
        _is_bps = None
        try:
            from app.services.bots.execution_tca import mean_is_bps_for_symbol

            _is_bps = mean_is_bps_for_symbol(symbol, lookback_days=1)
        except Exception:
            _is_bps = None
        record_posttrade_label(
            bot_id=bot_id,
            symbol=symbol,
            bar_time=_bar_time,
            outcome_class=outcome_class,
            mae=mae_pct,
            mfe=mfe_pct,
            execution_shortfall_bps=_is_bps,
            regime=reason.get("regime"),
        )
    except Exception:
        logger.debug("posttrade label write skipped for %s", symbol, exc_info=True)

    # RL replay buffer (AI-FT-PTL-001 §3.2): complete the pending live
    # transition with a normalized reward + the outcome class for shaping.
    try:
        from app.services.bots.rl_replay_store import record_live_close
        from app.services.bots.rl_risk import live_close_log_reward

        _reward = live_close_log_reward(pnl, ep, quantity)
        record_live_close(
            bot_id,
            symbol,
            reward=_reward,
            outcome_class=outcome_class,
        )
    except Exception:
        logger.debug("rl replay close hook skipped for %s", bot_id, exc_info=True)

    # Stacking meta-learner online update (AI-FT-PTL-001 §4.6): label the
    # entry-time base-prob vector with the realised outcome, then refresh the
    # combination weights every N trades.
    try:
        from app.services.bots.stacking_meta_learner import (
            maybe_update_stacking,
            record_stacking_outcome,
        )

        record_stacking_outcome(bot_id, win=bool(pnl is not None and pnl > 0))
        maybe_update_stacking(bot_id)
    except Exception:
        logger.debug("stacking online update skipped for %s", bot_id, exc_info=True)

    # Publish to Agent Event Bus
    agent_event_bus = getattr(bot_manager, "agent_event_bus", None)
    if agent_event_bus:
        try:
            import asyncio
            asyncio.create_task(
                agent_event_bus.publish(
                    AgentEvent(
                        source_agent="POSTTRADE_LEARNER",
                        event_type="POSTTRADE_LESSON",
                        payload={"bot_id": bot_id, "symbol": symbol, "lesson": result.to_dict()},
                        timestamp=time.time(),
                        reasoning=reasoning_obj,
                    )
                )
            )
        except Exception as exc:
            logger.debug("posttrade agent event bus publish failed: %s", exc)

    try:
        severity = "info" if (pnl or 0) >= 0 else "warn"
        await emit_notification(
            NotificationEvent(
                event_type=ntypes.POSTTRADE_LEARNER,
                title=f"Post-trade lesson {symbol} ({outcome_class})",
                body=lesson_text[:400],
                severity=severity,
                payload={
                    "bot_id": bot_id,
                    "symbol": symbol,
                    "lesson": result.to_dict(),
                },
            )
        )
    except Exception as exc:
        logger.debug("posttrade notify skipped: %s", exc)

    if bot_manager is not None and not applied:
        try:
            await bot_manager.log_bot_event(
                bot_id,
                "INFO",
                f"Post-trade lesson [{outcome_class}]: {lesson_text[:240]}",
            )
        except Exception:
            pass

    return result
