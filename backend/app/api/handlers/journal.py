"""Trade journal WebSocket handlers."""

from __future__ import annotations

from app.api.context import RequestContext
from app.api.outbound import error
from app.api.protocol import Action, MessageType
from app.api.responses import send_to
from app.api.router import route
from app.services.journal import store as journal_store


@route(Action.JOURNAL_LIST, tags=["journal"])
async def journal_list(ctx: RequestContext) -> None:
    entries = journal_store.list_entries(
        query=ctx.message.get("query"),
        tag=ctx.message.get("tag"),
        symbol=ctx.message.get("symbol"),
        limit=int(ctx.message.get("limit") or 100),
    )
    await send_to(ctx, {"type": MessageType.JOURNAL_ENTRIES, "data": {"entries": entries}})


@route(Action.JOURNAL_UPSERT, tags=["journal"])
async def journal_upsert(ctx: RequestContext) -> None:
    payload = ctx.message.get("entry")
    if not isinstance(payload, dict):
        await send_to(ctx, error("entry must be an object"))
        return
    entry = journal_store.upsert_entry(payload)
    await send_to(ctx, {"type": MessageType.JOURNAL_ENTRY, "data": entry})


@route(Action.JOURNAL_DELETE, tags=["journal"])
async def journal_delete(ctx: RequestContext) -> None:
    entry_id = (ctx.message.get("id") or "").strip()
    if not entry_id:
        await send_to(ctx, error("id is required"))
        return
    if not journal_store.delete_entry(entry_id):
        await send_to(ctx, error("journal entry not found"))
        return
    await send_to(ctx, {"type": MessageType.JOURNAL_DELETED, "data": {"id": entry_id}})
