"""Unified alt-data entry gates and upcoming-event context."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import (
    CALENDAR_GATES_ENABLED,
    CORP_BLACKOUT_EX_DIV_DAYS,
    CORP_BLACKOUT_SPLIT_DAYS,
    CORP_EVENT_GATES_ENABLED,
    MACRO_BLACKOUT_MINUTES,
    MACRO_GATES_ENABLED,
)
from app.db.connection import db_session
from app.services.altdata.calendar import calendar_gate, is_crypto_symbol
from app.services.altdata.store import _parse_timestamp_to_epoch


@dataclass(frozen=True)
class EventPolicy:
    calendar_gate: bool = True
    corp_split_blackout_days: int = 1
    corp_ex_div_blackout_days: int = 0
    crypto_exempt: bool = True
    macro_gate: bool = True
    macro_blackout_minutes: int = 30


def parse_event_policy(bot_config: dict | None) -> EventPolicy:
    cfg = bot_config if isinstance(bot_config, dict) else {}
    ep = cfg.get("event_policy") if isinstance(cfg.get("event_policy"), dict) else {}
    return EventPolicy(
        calendar_gate=bool(ep.get("calendar_gate", CALENDAR_GATES_ENABLED)),
        corp_split_blackout_days=int(
            ep.get("corp_split_blackout_days", CORP_BLACKOUT_SPLIT_DAYS)
        ),
        corp_ex_div_blackout_days=int(
            ep.get("corp_ex_div_blackout_days", CORP_BLACKOUT_EX_DIV_DAYS)
        ),
        crypto_exempt=bool(ep.get("crypto_exempt", True)),
        macro_gate=bool(ep.get("macro_gate", MACRO_GATES_ENABLED)),
        macro_blackout_minutes=int(
            ep.get("macro_blackout_minutes", MACRO_BLACKOUT_MINUTES)
        ),
    )


def _day_span_blackout(event_epoch: float, ts: float, days: int) -> bool:
    """True when ts falls within ±days calendar days of the event date."""
    if days <= 0:
        return False
    try:
        event_date = datetime.fromtimestamp(float(event_epoch), tz=timezone.utc).date()
        ts_date = datetime.fromtimestamp(float(ts), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return False
    return abs((ts_date - event_date).days) <= max(1, days)


def _corporate_blackout(
    symbol: str,
    ts: float,
    policy: EventPolicy,
) -> tuple[bool, str | None]:
    if not CORP_EVENT_GATES_ENABLED:
        return False, None
    if policy.crypto_exempt and is_crypto_symbol(symbol):
        return False, None

    sym = str(symbol or "").upper()
    if not sym:
        return False, None

    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT event_type, event_date, title
                FROM corporate_events
                WHERE symbol = ?
                ORDER BY event_date DESC
                LIMIT 80
                """,
                (sym,),
            )
            rows = cursor.fetchall()
        except Exception:
            return False, None

    for row in rows:
        if isinstance(row, dict):
            etype = row.get("event_type")
            edate = row.get("event_date")
            title = row.get("title")
        else:
            etype, edate, title = row[0], row[1], row[2]
        event_epoch = _parse_timestamp_to_epoch(edate)
        if event_epoch is None:
            continue
        if etype == "split" and policy.corp_split_blackout_days > 0:
            if _day_span_blackout(event_epoch, ts, policy.corp_split_blackout_days):
                return True, f"Split window blackout: {title or 'stock split'}"
        if etype == "dividend" and policy.corp_ex_div_blackout_days > 0:
            if _day_span_blackout(event_epoch, ts, policy.corp_ex_div_blackout_days):
                return True, f"Ex-dividend window blackout: {title or 'dividend'}"

    return False, None


def _is_high_impact_macro(impact: str | None, title: str | None) -> bool:
    imp = str(impact or "").lower().strip()
    if imp in ("high", "3"):
        return True
    title_l = str(title or "").lower()
    keywords = (
        "cpi", "consumer price", "fomc", "fed funds", "nonfarm", "nfp",
        "payroll", "gdp", "ppi", "pce", "jobless claims",
    )
    return any(kw in title_l for kw in keywords)


def _macro_blackout(ts: float, policy: EventPolicy) -> tuple[bool, str | None]:
    """Block entries ±N minutes around high-impact US macro releases."""
    if not MACRO_GATES_ENABLED or not policy.macro_gate:
        return False, None
    window_sec = max(5, policy.macro_blackout_minutes) * 60.0
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT title, scheduled_at, impact
                FROM economic_events
                WHERE event_type = 'macro_release'
                ORDER BY scheduled_at DESC
                LIMIT 80
                """
            )
            rows = cursor.fetchall()
        except Exception:
            return False, None

    for row in rows:
        if isinstance(row, dict):
            title = row.get("title")
            sched = row.get("scheduled_at")
            impact = row.get("impact")
        else:
            title, sched, impact = row[0], row[1], row[2]
        if not _is_high_impact_macro(impact, title):
            continue
        event_epoch = _parse_timestamp_to_epoch(sched)
        if event_epoch is None:
            continue
        if abs(event_epoch - ts) <= window_sec:
            return True, f"Macro release window: {title or 'economic event'}"
    return False, None


def check_entry_gates(
    symbol: str,
    ts: float | int | None,
    bot_config: dict | None = None,
    *,
    is_exit: bool = False,
) -> tuple[bool, str | None, str | None]:
    """
    Validate entry against calendar + corporate policy.

    Returns (allowed, reason, gate_kind).
    gate_kind is 'calendar' | 'corporate' | 'macro' | None.
    """
    if is_exit:
        return True, None, None
    if ts is None:
        return True, None, None
    try:
        epoch = float(ts)
    except (TypeError, ValueError):
        return True, None, None

    policy = parse_event_policy(bot_config)

    if policy.calendar_gate:
        blocked, reason = calendar_gate(symbol, epoch)
        if blocked:
            return False, reason, "calendar"

    blocked, reason = _corporate_blackout(symbol, epoch, policy)
    if blocked:
        return False, reason, "corporate"

    blocked, reason = _macro_blackout(epoch, policy)
    if blocked:
        return False, reason, "macro"

    return True, None, None


def get_upcoming_corporate_events(symbol: str, *, days: int = 7) -> list[dict[str, Any]]:
    sym = str(symbol or "").upper()
    if not sym:
        return []
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=max(1, days))
    out: list[dict[str, Any]] = []
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT event_type, event_date, title, source
                FROM corporate_events
                WHERE symbol = ?
                ORDER BY event_date ASC
                LIMIT 40
                """,
                (sym,),
            )
            rows = cursor.fetchall()
        except Exception:
            return []
    for row in rows:
        if isinstance(row, dict):
            item = dict(row)
        else:
            item = {
                "event_type": row[0],
                "event_date": row[1],
                "title": row[2],
                "source": row[3],
            }
        epoch = _parse_timestamp_to_epoch(item.get("event_date"))
        if epoch is None:
            continue
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        if dt < now - timedelta(days=1) or dt > horizon:
            continue
        out.append(item)
    return out


def get_upcoming_holidays(*, days: int = 14) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=max(1, days))
    out: list[dict[str, Any]] = []
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT title, scheduled_at, impact
                FROM economic_events
                WHERE event_type = 'market_holiday'
                ORDER BY scheduled_at ASC
                LIMIT 60
                """
            )
            rows = cursor.fetchall()
        except Exception:
            return []
    for row in rows:
        if isinstance(row, dict):
            item = dict(row)
        else:
            item = {"title": row[0], "scheduled_at": row[1], "impact": row[2]}
        epoch = _parse_timestamp_to_epoch(item.get("scheduled_at"))
        if epoch is None:
            continue
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        if dt < now - timedelta(days=1) or dt > horizon:
            continue
        out.append(item)
    return out


def get_upcoming_macro(*, days: int = 7) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=max(1, days))
    out: list[dict[str, Any]] = []
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT title, scheduled_at, impact, country
                FROM economic_events
                WHERE event_type = 'macro_release'
                ORDER BY scheduled_at ASC
                LIMIT 40
                """
            )
            rows = cursor.fetchall()
        except Exception:
            return []
    for row in rows:
        if isinstance(row, dict):
            item = dict(row)
        else:
            item = {
                "title": row[0],
                "scheduled_at": row[1],
                "impact": row[2],
                "country": row[3],
            }
        if not _is_high_impact_macro(item.get("impact"), item.get("title")):
            continue
        epoch = _parse_timestamp_to_epoch(item.get("scheduled_at"))
        if epoch is None:
            continue
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        if dt < now - timedelta(hours=6) or dt > horizon:
            continue
        out.append(item)
    return out


def get_upcoming_events(symbol: str, *, days: int = 7) -> dict[str, Any]:
    out: dict[str, Any] = {
        "corporate": get_upcoming_corporate_events(symbol, days=days),
        "holidays": get_upcoming_holidays(days=days + 7),
        "macro": get_upcoming_macro(days=days),
    }
    if is_crypto_symbol(symbol):
        try:
            from app.services.altdata.store import get_crypto_derivatives_at

            snap = get_crypto_derivatives_at(symbol, None)
            if snap:
                out["derivatives"] = snap
        except Exception:
            pass
    return out


def event_risk_score(symbol: str, ts: float | int | None) -> tuple[int, list[str]]:
    """Light event-risk domain for CHART_AGENT (-1 near split, 0 otherwise)."""
    if ts is None or is_crypto_symbol(symbol):
        return 0, []
    try:
        epoch = float(ts)
    except (TypeError, ValueError):
        return 0, []

    policy = EventPolicy(corp_split_blackout_days=1, corp_ex_div_blackout_days=0)
    blocked, reason = _corporate_blackout(symbol, epoch, policy)
    if blocked and reason and "split" in reason.lower():
        return -1, [reason]
    return 0, []


def backtest_event_manifest(symbol: str, from_ts: int, to_ts: int) -> dict[str, Any]:
    from app.config import BACKTEST_PRICE_ADJUST, MACRO_GATES_ENABLED
    from app.services.altdata.adjustments import count_splits_in_range

    splits = count_splits_in_range(symbol, from_ts, to_ts)
    return {
        "price_adjust": BACKTEST_PRICE_ADJUST,
        "splits_in_range": splits,
        "calendar_gates": CALENDAR_GATES_ENABLED,
        "corp_event_gates": CORP_EVENT_GATES_ENABLED,
        "macro_gates": MACRO_GATES_ENABLED,
    }
