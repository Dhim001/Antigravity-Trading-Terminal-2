"""Generate OpenAPI 3.1 document from HTTP bindings and route registry."""

from __future__ import annotations

from app.api.http.bindings import HTTP_BINDINGS
from app.api.router import ensure_routes_loaded, list_routes
from app.config import HTTP_HOST, HTTP_PORT

ACTION_SUMMARY: dict[str, str] = {
    "place_order": "Place market or limit order",
    "cancel_order": "Cancel pending order",
    "update_position_sl_tp": "Update position stop-loss / take-profit",
    "get_account": "Account snapshot",
    "get_history": "Trade history",
    "subscribe_symbol": "Candle history for symbol",
    "bot_create": "Deploy algo bot",
    "bot_stop": "Stop bot",
    "bot_pause": "Pause bot",
    "bot_resume": "Resume bot",
    "bot_stop_all": "Stop all bots",
    "bot_get_detail": "Bot analytics detail",
    "bot_get_all": "List bots",
    "run_backtest": "Run strategy backtest",
    "run_backtest_sweep": "Run parameter sweep / walk-forward optimization",
    "cancel_backtest": "Cancel in-flight backtest job",
    "trade_explain": "Explain bot trade with insight context",
    "admin_get_stats": "System statistics",
    "admin_set_simulation": "Simulation controls",
    "admin_seed_balance": "Seed balance (SIM only)",
    "admin_reset_system": "Reset database (SIM only)",
    "admin_emergency_stop": "Emergency halt",
}


def build_openapi_spec() -> dict:
    ensure_routes_loaded()
    routes = list_routes()
    paths: dict = {
        "/health": {
            "get": {
                "summary": "Health check",
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/api/v1/routes": {
            "get": {
                "summary": "List registered WebSocket actions",
                "responses": {"200": {"description": "Route registry"}},
            }
        },
        "/api/v1/openapi.json": {
            "get": {
                "summary": "This OpenAPI document",
                "responses": {"200": {"description": "OpenAPI JSON"}},
            }
        },
    }

    for method, path, action, _param_map in HTTP_BINDINGS:
        meta = routes.get(action)
        entry = {
            "summary": ACTION_SUMMARY.get(action, action),
            "tags": meta.tags if meta else ["api"],
            "responses": {
                "200": {"description": "Success envelope with messages array"},
                "400": {"description": "Validation or handler error"},
            },
        }
        if method in ("POST", "PATCH"):
            entry["requestBody"] = {
                "required": method == "POST",
                "content": {"application/json": {"schema": {"type": "object"}}},
            }
        paths.setdefault(path, {})[method.lower()] = entry

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Trading Terminal API",
            "version": "1.0.0",
            "description": "REST facade over the WebSocket action router",
        },
        "servers": [{"url": f"http://{HTTP_HOST}:{HTTP_PORT}"}],
        "paths": paths,
    }
