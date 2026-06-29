"""Alert rule CRUD + history handlers."""

from __future__ import annotations

from app.api.context import RequestContext
from app.api.protocol import Action
from app.api.responses import send_order_result
from app.api.router import route
from app.services.notifications.alert_rules import store as alert_store
from app.services.notifications.alert_rules import types as atypes


def _parse_channels(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


@route(Action.ALERT_RULE_LIST, tags=["notifications", "alerts"])
async def alert_rule_list(ctx: RequestContext) -> None:
    symbol = (ctx.message.get("symbol") or "").strip().upper() or None
    rules = alert_store.list_rules(symbol=symbol)
    await send_order_result(ctx, {
        "status": "success",
        "message": f"{len(rules)} alert rule(s)",
        "alert_rules": rules,
    })


@route(Action.ALERT_RULE_UPSERT, tags=["notifications", "alerts"])
async def alert_rule_upsert(ctx: RequestContext) -> None:
    msg = ctx.message
    name = (msg.get("name") or "").strip()
    symbol = (msg.get("symbol") or "").strip()
    if not name:
        await send_order_result(ctx, {"status": "error", "message": "name is required"})
        return
    if not symbol:
        await send_order_result(ctx, {"status": "error", "message": "symbol is required"})
        return

    condition_type = (msg.get("condition_type") or atypes.PRICE_ABOVE).strip()
    threshold_raw = msg.get("threshold")
    threshold = float(threshold_raw) if threshold_raw not in (None, "") else None

    try:
        row = alert_store.upsert_rule(
            rule_id=msg.get("id"),
            name=name,
            enabled=bool(msg.get("enabled", True)),
            symbol=symbol,
            timeframe=(msg.get("timeframe") or "1m").strip(),
            condition_type=condition_type,
            threshold=threshold,
            signal=(msg.get("signal") or "").strip().upper() or None,
            cooldown_sec=int(msg.get("cooldown_sec") or 300),
            notify_channels=_parse_channels(msg.get("notify_channels")),
        )
        await send_order_result(ctx, {
            "status": "success",
            "message": "Alert rule saved",
            "alert_rule": row,
        })
    except ValueError as exc:
        await send_order_result(ctx, {"status": "error", "message": str(exc)})
    except Exception as exc:
        await send_order_result(ctx, {"status": "error", "message": f"Save failed: {exc}"})


@route(Action.ALERT_RULE_DELETE, tags=["notifications", "alerts"])
async def alert_rule_delete(ctx: RequestContext) -> None:
    rule_id = (ctx.message.get("id") or "").strip()
    if not rule_id:
        await send_order_result(ctx, {"status": "error", "message": "id is required"})
        return
    ok = alert_store.delete_rule(rule_id)
    await send_order_result(ctx, {
        "status": "success" if ok else "error",
        "message": "Rule deleted" if ok else "Rule not found",
    })


@route(Action.ALERT_RULE_HISTORY, tags=["notifications", "alerts"])
async def alert_rule_history(ctx: RequestContext) -> None:
    rule_id = (ctx.message.get("rule_id") or ctx.message.get("id") or "").strip() or None
    limit = int(ctx.message.get("limit") or 50)
    history = alert_store.list_trigger_history(rule_id=rule_id, limit=limit)
    await send_order_result(ctx, {
        "status": "success",
        "message": f"{len(history)} trigger(s)",
        "alert_rule_history": history,
    })
