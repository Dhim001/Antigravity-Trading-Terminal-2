from app.api.context import RequestContext
from app.api.protocol import Action
from app.api.responses import send_account_update, send_trade_history
from app.api.router import route


@route(Action.GET_ACCOUNT, tags=["account"])
async def get_account(ctx: RequestContext) -> None:
    await send_account_update(ctx)


@route(Action.GET_HISTORY, tags=["account"])
async def get_history(ctx: RequestContext) -> None:
    await send_trade_history(ctx)
