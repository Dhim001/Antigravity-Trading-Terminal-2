"""Macro economic calendar — Finnhub economic releases (FOMC, CPI, NFP, etc.)."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import FINNHUB_API_KEY, FINNHUB_API_URL, MACRO_CALENDAR_ENABLED
from app.services.altdata.store import upsert_economic_events

logger = logging.getLogger(__name__)

_SOURCE = "FINNHUB_MACRO"

# Finnhub impact: "high", "medium", "low" or numeric 1-3
_HIGH_IMPACT = frozenset({"high", "3", "3.0"})
_MACRO_KEYWORDS = (
    "cpi", "consumer price", "fomc", "fed", "interest rate", "nonfarm", "nfp",
    "payroll", "gdp", "ppi", "pce", "jobless", "unemployment", "retail sales",
)


def _is_high_impact(event: dict[str, Any]) -> bool:
    impact = str(event.get("impact") or "").lower().strip()
    if impact in _HIGH_IMPACT or impact == "high":
        return True
    title = str(event.get("title") or event.get("event") or "").lower()
    return any(kw in title for kw in _MACRO_KEYWORDS)


def _event_id(item: dict[str, Any]) -> str:
    title = str(item.get("event") or item.get("title") or "macro")
    sched = str(item.get("time") or item.get("scheduled_at") or "")
    country = str(item.get("country") or "US")
    digest = hashlib.sha1(f"{country}:{sched}:{title}".encode()).hexdigest()[:12]
    return f"macro:{country}:{digest}"


def _parse_scheduled_at(item: dict[str, Any]) -> str:
    raw = item.get("time") or item.get("date")
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return datetime.now(timezone.utc).isoformat()


def fetch_finnhub_economic_calendar(
    *,
    days_ahead: int = 14,
    days_back: int = 1,
) -> list[dict[str, Any]]:
    if not FINNHUB_API_KEY:
        return []
    import httpx

    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=max(0, days_back))).strftime("%Y-%m-%d")
    end = (now + timedelta(days=max(1, days_ahead))).strftime("%Y-%m-%d")
    url = f"{FINNHUB_API_URL}/calendar/economic"
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(
                url,
                params={"from": start, "to": end, "token": FINNHUB_API_KEY},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Finnhub economic calendar failed: %s", exc)
        return []

    items = data.get("economicCalendar") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []

    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        country = str(item.get("country") or "").upper()
        if country and country not in ("US", "USA", ""):
            continue
        title = str(item.get("event") or item.get("title") or "Macro release")
        impact_raw = item.get("impact")
        impact = str(impact_raw) if impact_raw is not None else "medium"
        high = _is_high_impact({"title": title, "impact": impact})
        rows.append({
            "event_id": _event_id({**item, "title": title}),
            "event_type": "macro_release",
            "title": title,
            "scheduled_at": _parse_scheduled_at(item),
            "impact": "high" if high else impact,
            "country": country or "US",
            "source": _SOURCE,
            "raw": item,
        })
    return rows


def refresh_macro_calendar() -> dict[str, Any]:
    if not MACRO_CALENDAR_ENABLED:
        return {"enabled": False, "reason": "MACRO_CALENDAR_ENABLED=false"}
    if not FINNHUB_API_KEY:
        return {"enabled": False, "reason": "FINNHUB_API_KEY not set"}

    rows = fetch_finnhub_economic_calendar()
    written = upsert_economic_events(rows)
    high_count = sum(1 for r in rows if str(r.get("impact") or "").lower() == "high")
    return {
        "enabled": True,
        "fetched": len(rows),
        "written": written,
        "high_impact": high_count,
    }
