"""Terminal symbol <-> Massive (Polygon) ticker/pair mapping."""

from __future__ import annotations

# Massive crypto WS/REST uses USD pairs (e.g. BTC-USD, X:BTCUSD), not Binance USDT.


def is_crypto_terminal_symbol(symbol: str) -> bool:
    return "USDT" in symbol.upper()


def terminal_to_massive_rest_ticker(symbol: str, info: dict | None = None) -> str:
    """REST v2 aggs ticker: AAPL or X:BTCUSD."""
    sym = symbol.upper()
    if is_crypto_terminal_symbol(sym):
        asset = (info or {}).get("asset") or sym.replace("USDT", "")
        return f"X:{asset}USD"
    return sym


def terminal_to_massive_ws_pair(symbol: str, info: dict | None = None) -> str:
    """WebSocket pair/ticker: AAPL or BTC-USD."""
    sym = symbol.upper()
    if is_crypto_terminal_symbol(sym):
        asset = (info or {}).get("asset") or sym.replace("USDT", "")
        return f"{asset}-USD"
    return sym


def build_pair_to_terminal(crypto_symbols: dict[str, dict]) -> dict[str, str]:
    """Map Massive crypto `pair` (e.g. BTC-USD) -> terminal symbol (BTCUSDT)."""
    out: dict[str, str] = {}
    for sym, info in crypto_symbols.items():
        pair = terminal_to_massive_ws_pair(sym, info)
        out[pair] = sym
        out[pair.upper()] = sym
    return out
