"""Post-Trade Learning Agent — close → classify → lesson → optional config apply."""

from __future__ import annotations

import logging
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
from app.services.agent.agent_event_bus import AgentEvent
import time

logger = logging.getLogger(__name__)

LESSON_SYSTEM_PROMPT = """You are a quantitative post-trade coach.
Given a closed trade's MAE/MFE, PnL, regime, and classification, write 2-4 sentences of
actionable lesson text. Be specific about stops, filters, or regime. No fluff.
Do not invent numbers not present in the data. Start directly with the lesson."""


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


def classify_outcome(
    *,
    pnl: float | None,
    mae_pct: float | None,
    mfe_pct: float | None,
    trigger_type: str | None,
    insight: dict[str, Any] | None,
    stop_loss_percent: float | None = None,
) -> tuple[str, dict[str, Any]]:
    """Heuristic outcome class for a closed trade."""
    reason: dict[str, Any] = {
        "pnl": pnl,
        "mae_pct": mae_pct,
        "mfe_pct": mfe_pct,
        "trigger_type": trigger_type,
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

    if won and mfe is not None and mae is not None and mfe >= mae:
        return "clean_win", reason
    if won:
        return "messy_win", reason
    if lost:
        return "clean_loss", reason
    return "flat", reason


def _default_min_confidence(strategy: str) -> float:
    s = (strategy or "").upper()
    if s == "TCN_MULTI_HORIZON":
        return 0.002
    if s == "RL_PPO_AGENT":
        return 0.28
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

    if outcome_class == "stop_too_tight":
        cur = float(cfg.get("stop_loss_percent") or cfg.get("trailing_stop_percent") or 1.5)
        raw["stop_loss_percent"] = round(cur + POSTTRADE_LEARNER_STOP_WIDEN_PCT, 4)
        if cfg.get("trailing_stop_percent") is not None:
            trail = float(cfg.get("trailing_stop_percent") or cur)
            raw["trailing_stop_percent"] = round(trail + POSTTRADE_LEARNER_STOP_WIDEN_PCT, 4)

    elif outcome_class == "good_entry_bad_exit":
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

    elif outcome_class == "clean_loss":
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
    note = reason.get("note") or outcome_class.replace("_", " ")
    patch_bit = ""
    if patch:
        bits = ", ".join(f"{k}={v}" for k, v in patch.items())
        patch_bit = f" Suggested adjust: {bits}."
    return (
        f"{symbol}: {note}. PnL {pnl_s}, MAE {mae_s}, MFE {mfe_s} "
        f"(class={outcome_class}).{patch_bit}"
    )


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

    # Infer long/short from entry side or exit side (exit SELL ⇒ was long).
    entry_side = str(entry_ctx.get("side") or "").upper()
    if entry_side in ("BUY", "SELL"):
        is_long = entry_side == "BUY"
    else:
        is_long = str(exit_side).upper() == "SELL"

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

    outcome_class, reason = classify_outcome(
        pnl=pnl,
        mae_pct=mae_pct,
        mfe_pct=mfe_pct,
        trigger_type=trigger_type,
        insight=insight,
        stop_loss_percent=sl_pct_f,
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

    context = {
        "symbol": symbol,
        "bot_id": bot_id,
        "outcome_class": outcome_class,
        "pnl": pnl,
        "mae_pct": mae_pct,
        "mfe_pct": mfe_pct,
        "trigger_type": trigger_type,
        "entry_price": ep,
        "exit_price": exit_price,
        "quantity": quantity,
        "exit_side": exit_side,
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
    if not lesson_text:
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
        try:
            await bot_manager.update_bot_config(bot_id, patch)
            applied = True
            await bot_manager.log_bot_event(
                bot_id,
                "INFO",
                f"Post-trade learner applied config: {patch}",
            )
        except Exception as exc:
            logger.warning("posttrade auto-apply failed for %s: %s", bot_id, exc)

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
