"""HTTP path bindings for WebSocket actions (dual registration)."""

from app.api.protocol import Action

# (method, path, action, path_param_map)
HTTP_BINDINGS: list[tuple[str, str, str, dict[str, str] | None]] = [
    ("GET", "/api/v1/account", Action.GET_ACCOUNT, None),
    ("GET", "/api/v1/history", Action.GET_HISTORY, None),
    ("GET", "/api/v1/market/{symbol}/candles", Action.SUBSCRIBE_SYMBOL, {"symbol": "symbol"}),
    ("GET", "/api/v1/market/{symbol}/history", Action.GET_MARKET_HISTORY, {"symbol": "symbol"}),
    ("POST", "/api/v1/orders", Action.PLACE_ORDER, None),
    ("DELETE", "/api/v1/orders/{order_id}", Action.CANCEL_ORDER, {"order_id": "order_id"}),
    ("PATCH", "/api/v1/positions/{symbol}/sl-tp", Action.UPDATE_POSITION_SL_TP, {"symbol": "symbol"}),
    ("GET", "/api/v1/bots", Action.BOT_GET_ALL, None),
    ("GET", "/api/v1/bots/all", Action.BOT_LIST_ALL, None),
    ("POST", "/api/v1/bots", Action.BOT_CREATE, None),
    ("GET", "/api/v1/bots/{bot_id}", Action.BOT_GET_DETAIL, {"bot_id": "bot_id"}),
    ("POST", "/api/v1/bots/{bot_id}/stop", Action.BOT_STOP, {"bot_id": "bot_id"}),
    ("POST", "/api/v1/bots/{bot_id}/pause", Action.BOT_PAUSE, {"bot_id": "bot_id"}),
    ("POST", "/api/v1/bots/{bot_id}/resume", Action.BOT_RESUME, {"bot_id": "bot_id"}),
    ("POST", "/api/v1/bots/stop-all", Action.BOT_STOP_ALL, None),
    ("POST", "/api/v1/backtest", Action.RUN_BACKTEST, None),
    ("GET", "/api/v1/admin/stats", Action.ADMIN_GET_STATS, None),
    ("POST", "/api/v1/admin/archive/backfill", Action.ADMIN_ARCHIVE_BACKFILL, None),
    ("POST", "/api/v1/admin/archive/export", Action.ADMIN_ARCHIVE_EXPORT, None),
    ("GET", "/api/v1/admin/reconciliation", Action.ADMIN_GET_RECONCILIATION, None),
    ("POST", "/api/v1/admin/reconciliation/reconcile", Action.ADMIN_RECONCILE, None),
    ("POST", "/api/v1/admin/reconciliation/resolve", Action.ADMIN_RESOLVE_AMBIGUOUS, None),
    ("GET", "/api/v1/market/{symbol}/ticks", Action.GET_MARKET_TICKS, {"symbol": "symbol"}),
    ("POST", "/api/v1/admin/simulation", Action.ADMIN_SET_SIMULATION, None),
    ("POST", "/api/v1/admin/seed-balance", Action.ADMIN_SEED_BALANCE, None),
    ("POST", "/api/v1/admin/reset", Action.ADMIN_RESET_SYSTEM, None),
    ("POST", "/api/v1/admin/emergency-stop", Action.ADMIN_EMERGENCY_STOP, None),
]
