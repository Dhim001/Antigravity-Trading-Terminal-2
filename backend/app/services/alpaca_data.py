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
