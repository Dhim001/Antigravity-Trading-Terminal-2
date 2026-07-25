"""Alpaca equity data feed selection (SIP vs IEX) based on subscription entitlement."""

from __future__ import annotations

import logging
import os
from typing import Literal

import httpx

from app.config import ALPACA_API_KEY, ALPACA_SECRET_KEY

logger = logging.getLogger(__name__)

AlpacaEquityFeed = Literal["sip", "iex"]

_WS_BASE = "wss://stream.data.alpaca.markets/v2"
_DATA_REST = "https://data.alpaca.markets"
_DEFAULT_WS = f"{_WS_BASE}/sip"
_SIP_DENIED_CODE = 42210000
_SIP_DENIED_MSG = "subscription does not permit querying recent sip data"

_resolved_feed: AlpacaEquityFeed | None = None
_resolved_ws_url: str | None = None


def _alpaca_headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }


def _feed_mode() -> str:
    return os.environ.get("ALPACA_DATA_FEED", "auto").strip().lower()


def _explicit_ws_url() -> str:
    return os.environ.get("ALPACA_DATA_URL", "").strip()


def probe_sip_entitlement(*, timeout: float = 10.0) -> bool:
    """Return True when the account may query recent SIP equity data (Algo Trader Plus)."""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return False
    try:
        with httpx.Client(timeout=timeout, headers=_alpaca_headers()) as client:
            resp = client.get(
                f"{_DATA_REST}/v2/stocks/AAPL/trades/latest",
                params={"feed": "sip"},
            )
        if resp.status_code == 200:
            return True
        try:
            payload = resp.json()
        except Exception:
            payload = {}
        code = payload.get("code")
        message = str(payload.get("message", "")).lower()
        if code == _SIP_DENIED_CODE or _SIP_DENIED_MSG in message:
            return False
        logger.warning(
            "Alpaca SIP probe returned HTTP %s (code=%s); defaulting to IEX",
            resp.status_code,
            code,
        )
        return False
    except Exception as exc:
        logger.warning("Alpaca SIP probe failed (%s); defaulting to IEX", exc)
        return False


def is_sip_entitlement_error(status_code: int, body: str | dict | None) -> bool:
    """Detect Alpaca SIP subscription errors in REST or WebSocket auth responses."""
    if isinstance(body, dict):
        code = body.get("code")
        message = str(body.get("message", "")).lower()
        if code == _SIP_DENIED_CODE or _SIP_DENIED_MSG in message:
            return True
    text = str(body or "").lower()
    if _SIP_DENIED_MSG in text or "insufficient subscription" in text:
        return True
    return status_code in (403, 422) and "sip" in text


def resolve_equity_data_feed(*, force_refresh: bool = False) -> AlpacaEquityFeed:
    """Resolve sip vs iex once per process (cached unless force_refresh)."""
    global _resolved_feed
    if _resolved_feed and not force_refresh:
        return _resolved_feed

    mode = _feed_mode()
    if mode == "sip":
        _resolved_feed = "sip"
    elif mode == "iex":
        _resolved_feed = "iex"
    else:
        _resolved_feed = "sip" if probe_sip_entitlement() else "iex"

    logger.info(
        "Alpaca equity data feed resolved to %s (ALPACA_DATA_FEED=%s)",
        _resolved_feed,
        mode,
    )
    return _resolved_feed


def ws_url_for_feed(feed: AlpacaEquityFeed) -> str:
    return f"{_WS_BASE}/{feed}"


def get_alpaca_ws_url(*, force_refresh: bool = False) -> str:
    """WebSocket URL for equity stream — auto sip/iex unless explicitly overridden."""
    global _resolved_ws_url
    explicit = _explicit_ws_url()
    mode = _feed_mode()

    if explicit and mode != "auto":
        logger.warning(
            "ALPACA_DATA_URL and ALPACA_DATA_FEED=%s both set; feed mode wins",
            mode,
        )

    if mode in ("sip", "iex"):
        _resolved_ws_url = ws_url_for_feed(mode)  # type: ignore[arg-type]
        return _resolved_ws_url

    if explicit and explicit != _DEFAULT_WS:
        _resolved_ws_url = explicit
        logger.info("Alpaca WebSocket using explicit ALPACA_DATA_URL")
        return _resolved_ws_url

    if force_refresh:
        resolve_equity_data_feed(force_refresh=True)
    feed = resolve_equity_data_feed()
    _resolved_ws_url = ws_url_for_feed(feed)
    return _resolved_ws_url


def fallback_to_iex() -> tuple[AlpacaEquityFeed, str]:
    """Force IEX after a live SIP auth/subscription failure."""
    global _resolved_feed, _resolved_ws_url
    _resolved_feed = "iex"
    _resolved_ws_url = ws_url_for_feed("iex")
    logger.warning("Alpaca falling back to IEX equity feed (Basic plan / no SIP entitlement)")
    return _resolved_feed, _resolved_ws_url


# ── Crypto + options helpers ───────────────────────────────────────────────

_OCC_RE = __import__("re").compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
_DEFAULT_CRYPTO_WS = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"
_OPTIONS_WS_BASE = "wss://stream.data.alpaca.markets/v1beta1"


def env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def get_crypto_ws_url() -> str:
    return (
        os.environ.get("ALPACA_CRYPTO_WS_URL", "").strip()
        or _DEFAULT_CRYPTO_WS
    )


def get_options_ws_url() -> str:
    feed = os.environ.get("ALPACA_OPTION_FEED", "indicative").strip().lower()
    if feed not in ("indicative", "opra"):
        feed = "indicative"
    explicit = os.environ.get("ALPACA_OPTIONS_WS_URL", "").strip()
    if explicit:
        return explicit
    return f"{_OPTIONS_WS_BASE}/{feed}"


def is_option_symbol(symbol: str) -> bool:
    return bool(_OCC_RE.match((symbol or "").strip().upper()))


def terminal_to_alpaca_crypto(symbol: str) -> str:
    """Map terminal BTCUSDT → Alpaca BTC/USD (US crypto stream)."""
    s = (symbol or "").strip().upper().replace("-", "/").replace(" ", "")
    if not s:
        return ""
    if "/" in s:
        base, quote = s.split("/", 1)
        if quote in ("USDT", "USD"):
            return f"{base}/USD"
        return f"{base}/{quote}"
    if s.endswith("USDT"):
        return f"{s[:-4]}/USD"
    if s.endswith("USD"):
        return f"{s[:-3]}/USD"
    return f"{s}/USD"


def alpaca_crypto_to_terminal(symbol: str) -> str:
    """Map Alpaca BTC/USD → terminal BTCUSDT."""
    s = (symbol or "").strip().upper().replace(" ", "")
    if "/" in s:
        base, quote = s.split("/", 1)
        if quote in ("USD", "USDT"):
            return f"{base}USDT"
        return f"{base}{quote}"
    if s.endswith("USD") and not s.endswith("USDT"):
        return f"{s[:-3]}USDT"
    return s


def option_underlyings_from_env() -> list[str]:
    raw = os.environ.get("ALPACA_OPTION_UNDERLYINGS", "SPY,QQQ,AAPL")
    return [p.strip().upper() for p in str(raw).split(",") if p.strip()]


def explicit_option_symbols_from_env() -> list[str]:
    raw = os.environ.get("ALPACA_OPTION_SYMBOLS", "")
    out = []
    for p in str(raw).split(","):
        s = p.strip().upper()
        if s and is_option_symbol(s):
            out.append(s)
    return out


def resolve_option_watch_symbols(
    *,
    underlyings: list[str] | None = None,
    limit_per_underlying: int = 2,
    timeout: float = 12.0,
) -> list[str]:
    """Resolve a small liquid OCC watchlist via Alpaca options contracts REST."""
    explicit = explicit_option_symbols_from_env()
    if explicit:
        return explicit[: max(1, limit_per_underlying * 6)]

    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return []

    from app.config import ALPACA_BASE_URL

    roots = underlyings or option_underlyings_from_env()
    found: list[str] = []
    try:
        with httpx.Client(timeout=timeout, headers=_alpaca_headers()) as client:
            for root in roots:
                resp = client.get(
                    f"{ALPACA_BASE_URL.rstrip('/')}/v2/options/contracts",
                    params={
                        "underlying_symbols": root,
                        "status": "active",
                        "limit": max(4, limit_per_underlying * 4),
                    },
                )
                if resp.status_code != 200:
                    logger.debug(
                        "Alpaca options contracts %s → HTTP %s",
                        root,
                        resp.status_code,
                    )
                    continue
                rows = resp.json()
                if isinstance(rows, dict):
                    rows = rows.get("option_contracts") or rows.get("contracts") or []
                if not isinstance(rows, list):
                    continue
                # Prefer nearer expiries; keep mix of calls/puts.
                scored: list[tuple[str, str, str]] = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    sym = str(row.get("symbol") or "").upper()
                    if not is_option_symbol(sym):
                        continue
                    exp = str(row.get("expiration_date") or "")
                    style = str(row.get("type") or row.get("option_type") or "")
                    scored.append((exp, style, sym))
                scored.sort(key=lambda t: (t[0], t[1], t[2]))
                picked = 0
                for _, _, sym in scored:
                    if sym in found:
                        continue
                    found.append(sym)
                    picked += 1
                    if picked >= limit_per_underlying:
                        break
    except Exception as exc:
        logger.warning("Alpaca options watchlist resolve failed: %s", exc)
    return found
