"""Bar-close alert rule evaluation loop entry."""

from __future__ import annotations

import logging

from app.config import ALERT_RULES_ENABLED
from app.services.bots.bar_events import BarCloseTracker
from app.services.bots.candle_source import candles_for_timeframe, get_bot_candles
from app.services.bots.execution_mode import is_live_massive
from app.services.notifications import types as ntypes
from app.services.notifications.alert_rules import evaluator, store
from app.services.notifications.dispatcher import emit_notification
from app.services.notifications.events import NotificationEvent

logger = logging.getLogger(__name__)

_tracker = BarCloseTracker()
_MIN_ALERT_BARS = 30


async def evaluate_rules_for_bar(
    symbol: str,
    timeframe: str,
    ohlcv: list,
) -> int:
    """Evaluate enabled rules for symbol+timeframe on a confirmed bar close. Returns trigger count."""
    if not ALERT_RULES_ENABLED:
        return 0

    rules = [
        r for r in store.list_enabled_for_symbol(symbol)
        if (r.get("timeframe") or "1m") == timeframe
    ]
    if not rules:
        return 0

    metrics = evaluator.compute_bar_metrics(symbol, ohlcv)
    if not metrics:
        return 0

    triggered = 0
    for rule in rules:
        if store.is_in_cooldown(rule):
            continue
        if not evaluator.rule_matches(rule, metrics):
            continue

        title, body = evaluator.format_alert_message(rule, metrics)
        payload = {
            "rule_id": rule["id"],
            "rule_name": rule.get("name"),
            "condition_type": rule.get("condition_type"),
            "timeframe": timeframe,
            "bar_time": metrics.get("bar_time"),
            "metrics": metrics,
        }
        channel_ids = rule.get("notify_channels")
        if channel_ids is not None and not channel_ids:
            continue

        queued = await emit_notification(
            NotificationEvent(
                event_type=ntypes.ALERT_RULE,
                title=title,
                body=body,
                severity="warn",
                symbol=symbol.upper(),
                payload=payload,
            ),
            channel_ids=channel_ids,
        )
        if queued:
            store.mark_triggered(rule["id"])
            store.log_trigger(
                rule_id=rule["id"],
                symbol=symbol,
                timeframe=timeframe,
                message=body,
                payload=payload,
            )
            triggered += 1
    return triggered


async def maybe_evaluate_alert_rules(
    symbol: str,
    *,
    ohlcv_1m: list | None = None,
    feed=None,
) -> int:
    """Check all rule timeframes for symbol; evaluate on bar close."""
    if not ALERT_RULES_ENABLED:
        return 0

    rules = store.list_enabled_for_symbol(symbol)
    if not rules:
        return 0

    timeframes = sorted({(r.get("timeframe") or "1m") for r in rules})
    total = 0

    for timeframe in timeframes:
        # LIVE_MASSIVE HT bars are evaluated on the server from native HT closes only.
        if is_live_massive() and timeframe != "1m":
            continue

        ohlcv = None
        if feed is not None:
            ohlcv = get_bot_candles(symbol, feed, timeframe=timeframe)
            if (not ohlcv or len(ohlcv) < _MIN_ALERT_BARS) and ohlcv_1m:
                fallback = candles_for_timeframe(ohlcv_1m, timeframe)
                if fallback and (
                    len(fallback) >= _MIN_ALERT_BARS
                    or len(fallback) > len(ohlcv or [])
                ):
                    ohlcv = fallback
        elif ohlcv_1m:
            ohlcv = candles_for_timeframe(ohlcv_1m, timeframe)
        else:
            continue

        if not ohlcv or len(ohlcv) < _MIN_ALERT_BARS:
            continue
        if not _tracker.check(symbol, ohlcv, timeframe=timeframe):
            continue

        try:
            total += await evaluate_rules_for_bar(symbol, timeframe, ohlcv)
        except Exception as exc:
            logger.warning("Alert rule evaluation failed %s %s: %s", symbol, timeframe, exc)

    return total
