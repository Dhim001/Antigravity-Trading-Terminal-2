"""Notification channel WebSocket/HTTP handlers."""

from __future__ import annotations

import secrets
from typing import Any

from app.api.context import RequestContext
from app.api.protocol import Action
from app.api.responses import send_order_result
from app.api.router import route
from app.services.notifications import types as ntypes
from app.services.notifications.dispatcher import emit_notification
from app.services.notifications.events import NotificationEvent
from app.services.notifications import store as notify_store


def _parse_event_types(raw, *, channel_type: str, is_new: bool) -> list[str]:
    if raw == "*":
        return list(ntypes.ALL_EVENT_TYPES)
    if isinstance(raw, list) and raw:
        return [str(x) for x in raw if x]
    if channel_type == ntypes.CHANNEL_EMAIL and is_new:
        return [ntypes.DAILY_DIGEST]
    if channel_type == ntypes.CHANNEL_TELEGRAM and is_new:
        return list(ntypes.REALTIME_EVENT_TYPES)
    if channel_type == ntypes.CHANNEL_PUSH and is_new:
        return list(ntypes.REALTIME_EVENT_TYPES)
    return list(ntypes.REALTIME_EVENT_TYPES)


def _merge_config(
    channel_type: str,
    msg: dict,
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    config = dict((existing or {}).get("config") or {})

    if channel_type == ntypes.CHANNEL_WEBHOOK:
        url = (msg.get("url") or "").strip()
        if url:
            config["url"] = url
        if msg.get("preset"):
            config["preset"] = str(msg.get("preset")).strip().lower()
        hmac_secret = msg.get("hmac_secret")
        if hmac_secret:
            config["hmac_secret"] = str(hmac_secret).strip()

    elif channel_type == ntypes.CHANNEL_TELEGRAM:
        bot_token = (msg.get("bot_token") or "").strip()
        if bot_token:
            config["bot_token"] = bot_token
        chat_id = (msg.get("chat_id") or "").strip()
        if chat_id:
            config["chat_id"] = chat_id
        if msg.get("parse_mode") is not None:
            config["parse_mode"] = str(msg.get("parse_mode")).strip()

    elif channel_type == ntypes.CHANNEL_EMAIL:
        for key in ("smtp_host", "smtp_user", "from_address"):
            val = (msg.get(key) or "").strip()
            if val:
                config[key] = val
        if msg.get("smtp_port") is not None:
            config["smtp_port"] = int(msg.get("smtp_port"))
        smtp_password = msg.get("smtp_password")
        if smtp_password:
            config["smtp_password"] = str(smtp_password).strip()
        if msg.get("use_tls") is not None:
            config["use_tls"] = bool(msg.get("use_tls"))
        to_raw = msg.get("to_addresses")
        if to_raw is not None:
            if isinstance(to_raw, str):
                config["to_addresses"] = [a.strip() for a in to_raw.split(",") if a.strip()]
            else:
                config["to_addresses"] = [str(a).strip() for a in to_raw if str(a).strip()]

    return config


async def _deliver_test(channel: dict) -> None:
    ctype = channel.get("channel_type")
    config = channel.get("config") or {}
    event = NotificationEvent(
        event_type=ntypes.TEST,
        title="Trading Terminal test notification",
        body="If you see this, notification delivery is working.",
        severity="info",
        payload={"test": True},
    )
    if ctype == ntypes.CHANNEL_WEBHOOK:
        from app.services.notifications.adapters.webhook import deliver_webhook
        await deliver_webhook(event, config)
    elif ctype == ntypes.CHANNEL_TELEGRAM:
        from app.services.notifications.adapters.telegram import deliver_telegram
        await deliver_telegram(event, config)
    elif ctype == ntypes.CHANNEL_EMAIL:
        from app.services.notifications.adapters.email import deliver_email
        await deliver_email(event, config)
    elif ctype == ntypes.CHANNEL_PUSH:
        from app.services.notifications.adapters.push import deliver_push
        await deliver_push(event, channel)
    else:
        raise ValueError(f"Unsupported channel type: {ctype}")


@route(Action.NOTIFY_CHANNEL_LIST, tags=["notifications"])
async def notify_channel_list(ctx: RequestContext) -> None:
    channels = notify_store.list_channels()
    await send_order_result(ctx, {
        "status": "success",
        "message": f"{len(channels)} notification channel(s)",
        "notification_channels": channels,
    })


@route(Action.NOTIFY_CHANNEL_UPSERT, tags=["notifications"])
async def notify_channel_upsert(ctx: RequestContext) -> None:
    msg = ctx.message
    channel_id = msg.get("id")
    name = (msg.get("name") or "").strip()
    if not name:
        await send_order_result(ctx, {"status": "error", "message": "name is required"})
        return

    channel_type = (msg.get("channel_type") or ntypes.CHANNEL_WEBHOOK).strip()
    if channel_type not in ntypes.ALL_CHANNEL_TYPES:
        await send_order_result(ctx, {"status": "error", "message": f"Unknown channel type: {channel_type}"})
        return

    existing = notify_store.get_channel_decrypted(channel_id) if channel_id else None
    is_new = existing is None
    config = _merge_config(channel_type, msg, existing)
    subscribe_secret_out: str | None = None

    if channel_type == ntypes.CHANNEL_PUSH:
        if msg.get("rotate_subscribe_secret") or not (config.get("subscribe_secret") or "").strip():
            config["subscribe_secret"] = secrets.token_urlsafe(32)
            subscribe_secret_out = config["subscribe_secret"]

    try:
        notify_store.validate_channel_config(channel_type, config)
    except ValueError as exc:
        await send_order_result(ctx, {"status": "error", "message": str(exc)})
        return

    try:
        row = notify_store.upsert_channel(
            channel_id=channel_id,
            channel_type=channel_type,
            name=name,
            enabled=bool(msg.get("enabled", True)),
            event_types=_parse_event_types(
                msg.get("event_types"),
                channel_type=channel_type,
                is_new=is_new,
            ),
            config=config,
        )
        result: dict[str, Any] = {
            "status": "success",
            "message": "Notification channel saved",
            "notification_channel": row,
        }
        if subscribe_secret_out:
            result["subscribe_secret"] = subscribe_secret_out
        await send_order_result(ctx, result)
    except ValueError as exc:
        await send_order_result(ctx, {"status": "error", "message": str(exc)})
    except Exception as exc:
        await send_order_result(ctx, {"status": "error", "message": f"Save failed: {exc}"})


@route(Action.NOTIFY_CHANNEL_DELETE, tags=["notifications"])
async def notify_channel_delete(ctx: RequestContext) -> None:
    channel_id = (ctx.message.get("id") or "").strip()
    if not channel_id:
        await send_order_result(ctx, {"status": "error", "message": "id is required"})
        return
    ok = notify_store.delete_channel(channel_id)
    await send_order_result(ctx, {
        "status": "success" if ok else "error",
        "message": "Channel deleted" if ok else "Channel not found",
    })


@route(Action.NOTIFY_CHANNEL_TEST, tags=["notifications"])
async def notify_channel_test(ctx: RequestContext) -> None:
    channel_id = (ctx.message.get("id") or "").strip()
    if not channel_id:
        await send_order_result(ctx, {"status": "error", "message": "id is required"})
        return

    channel = notify_store.get_channel_decrypted(channel_id)
    if not channel:
        await send_order_result(ctx, {"status": "error", "message": "Channel not found"})
        return

    try:
        await _deliver_test(channel)
        await send_order_result(ctx, {
            "status": "success",
            "message": "Test notification sent",
        })
    except Exception as exc:
        await send_order_result(ctx, {"status": "error", "message": str(exc)})


@route(Action.NOTIFY_DIGEST_SEND_NOW, tags=["notifications"])
async def notify_digest_send_now(ctx: RequestContext) -> None:
    from app.services.notifications.digest import send_daily_digest

    try:
        queued = await send_daily_digest(ctx.oms)
        await send_order_result(ctx, {
            "status": "success",
            "message": f"Daily digest queued to {queued} channel(s)",
            "digest_queued": queued,
        })
    except Exception as exc:
        await send_order_result(ctx, {"status": "error", "message": str(exc)})


@route(Action.NOTIFY_PUSH_VAPID_PUBLIC, tags=["notifications"])
async def notify_push_vapid_public(ctx: RequestContext) -> None:
    from app.services.notifications.vapid import get_vapid_public_key, web_push_configured

    key = get_vapid_public_key()
    await send_order_result(ctx, {
        "status": "success" if key else "error",
        "message": "VAPID public key" if key else "Web Push not configured on server",
        "web_push_enabled": web_push_configured(),
        "vapid_public_key": key or "",
    })


@route(Action.NOTIFY_PUSH_SUBSCRIBE, tags=["notifications"])
async def notify_push_subscribe(ctx: RequestContext) -> None:
    from app.services.notifications import push_subscriptions as push_store
    from app.services.notifications.vapid import web_push_configured

    if not web_push_configured():
        await send_order_result(ctx, {"status": "error", "message": "Web Push not configured on server"})
        return

    channel_id = (ctx.message.get("channel_id") or ctx.message.get("id") or "").strip()
    sub = ctx.message.get("subscription") or {}
    endpoint = (sub.get("endpoint") or ctx.message.get("endpoint") or "").strip()
    keys = sub.get("keys") or {}
    p256dh = (keys.get("p256dh") or ctx.message.get("p256dh") or "").strip()
    auth = (keys.get("auth") or ctx.message.get("auth") or "").strip()

    if not channel_id:
        await send_order_result(ctx, {"status": "error", "message": "channel_id is required"})
        return

    channel = notify_store.get_channel_decrypted(channel_id)
    if not channel:
        await send_order_result(ctx, {"status": "error", "message": "Channel not found"})
        return
    if channel.get("channel_type") != ntypes.CHANNEL_PUSH:
        await send_order_result(ctx, {"status": "error", "message": "Channel is not a push channel"})
        return
    if not channel.get("enabled"):
        await send_order_result(ctx, {"status": "error", "message": "Push channel is disabled"})
        return

    expected_secret = ((channel.get("config") or {}).get("subscribe_secret") or "").strip()
    provided_secret = (ctx.message.get("subscribe_secret") or "").strip()
    if not expected_secret or provided_secret != expected_secret:
        await send_order_result(ctx, {
            "status": "error",
            "message": "Invalid or missing subscribe_secret for this push channel",
        })
        return

    try:
        row = push_store.upsert_subscription(
            channel_id=channel_id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=(ctx.message.get("user_agent") or "")[:500],
        )
        await send_order_result(ctx, {
            "status": "success",
            "message": "Push subscription saved",
            "push_subscription": row,
        })
    except ValueError as exc:
        await send_order_result(ctx, {"status": "error", "message": str(exc)})
    except Exception as exc:
        await send_order_result(ctx, {"status": "error", "message": f"Subscribe failed: {exc}"})


@route(Action.NOTIFY_PUSH_UNSUBSCRIBE, tags=["notifications"])
async def notify_push_unsubscribe(ctx: RequestContext) -> None:
    from app.services.notifications import push_subscriptions as push_store

    sub_id = (ctx.message.get("subscription_id") or ctx.message.get("id") or "").strip()
    endpoint = (ctx.message.get("endpoint") or "").strip()
    ok = push_store.delete_subscription(subscription_id=sub_id or None, endpoint=endpoint or None)
    await send_order_result(ctx, {
        "status": "success" if ok else "error",
        "message": "Unsubscribed" if ok else "Subscription not found",
    })


@route(Action.NOTIFY_PUSH_LIST, tags=["notifications"])
async def notify_push_list(ctx: RequestContext) -> None:
    from app.services.notifications import push_subscriptions as push_store

    channel_id = (ctx.message.get("channel_id") or "").strip() or None
    subs = push_store.list_subscriptions(channel_id=channel_id)
    await send_order_result(ctx, {
        "status": "success",
        "message": f"{len(subs)} subscription(s)",
        "push_subscriptions": subs,
    })
