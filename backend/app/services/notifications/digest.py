"""Daily P&L digest builder and sender."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.config import NOTIFICATION_DIGEST_TZ
from app.database import get_connection
from app.services.notifications import types as ntypes
from app.services.notifications.dispatcher import emit_notification
from app.services.notifications.events import NotificationEvent

logger = logging.getLogger(__name__)


def _bot_status_counts() -> dict[str, int]:
    conn = get_connection()
    cursor = conn.cursor()
    counts: dict[str, int] = {}
    try:
        cursor.execute("SELECT status, COUNT(*) FROM bots GROUP BY status")
        for row in cursor.fetchall():
            status = row[0] if not isinstance(row, dict) else row["status"]
            n = row[1] if not isinstance(row, dict) else list(row.values())[1]
            counts[str(status)] = int(n)
    except Exception:
        pass
    finally:
        conn.close()
    return counts


def build_digest_text(oms) -> tuple[str, str, dict[str, Any]]:
    """Return (title, plain body, payload with html_body)."""
    from app.services.analytics.portfolio import get_bot_rankings, get_daily_pnl_calendar
    from app.services.bots.risk_monitor import compute_drawdown, drawdown_to_dict
    from app.services.runtime import system_state

    tz = ZoneInfo(NOTIFICATION_DIGEST_TZ)
    now_local = datetime.now(tz)
    title = f"Daily digest — {now_local.strftime('%Y-%m-%d')}"

    history = oms.get_trade_history() if oms else []
    calendar = get_daily_pnl_calendar(history, source="combined")
    today_key = now_local.strftime("%Y-%m-%d")
    yesterday_key = (now_local.date() - timedelta(days=1)).isoformat()

    day_pnl = None
    for row in calendar.get("days") or []:
        if row.get("date") == today_key or row.get("date") == yesterday_key:
            day_pnl = row
            break

    risk = drawdown_to_dict(compute_drawdown(oms)) if oms else {}
    bot_counts = _bot_status_counts()
    rankings = get_bot_rankings(limit=3)
    safe = system_state.get_safe_mode_info()

    lines = [
        f"Trading Terminal — {now_local.strftime('%Y-%m-%d %H:%M %Z')}",
        "",
        "Account",
        f"  Equity: ${risk.get('account_equity', 0):,.2f}",
        f"  Drawdown: {risk.get('current_drawdown_pct', 0):.1f}% (limit {risk.get('max_drawdown_pct', 0):.1f}%)",
        f"  Kill switch: {'TRIPPED' if risk.get('kill_switch_tripped') else 'OK'}",
        "",
        "Bots",
        f"  Running: {bot_counts.get('RUNNING', 0)}",
        f"  Paused: {bot_counts.get('PAUSED', 0)}",
        f"  Error: {bot_counts.get('ERROR', 0)}",
    ]

    if day_pnl:
        lines.extend([
            "",
            "Recent P&L",
            f"  Date: {day_pnl.get('date')}",
            f"  P&L: ${day_pnl.get('pnl', 0):+,.2f}",
        ])

    top = rankings.get("top") or []
    if top:
        lines.append("")
        lines.append("Top bots (PnL)")
        for b in top[:3]:
            lines.append(f"  {b.get('symbol')} / {b.get('strategy')}: ${b.get('total_pnl', 0):+,.2f}")

    if safe.get("active"):
        lines.extend(["", f"⚠ Safe mode active: {safe.get('reason', 'unknown')}"])

    body = "\n".join(lines)
    html = "<br>".join(line.replace("  ", "&nbsp;&nbsp;") for line in lines)
    payload = {
        "digest_date": today_key,
        "risk": risk,
        "bot_counts": bot_counts,
        "day_pnl": day_pnl,
        "html_body": f"<html><body><pre>{html}</pre></body></html>",
    }
    return title, body, payload


async def send_daily_digest(oms) -> int:
    """Build digest and emit to channels subscribed to daily_digest."""
    title, body, payload = build_digest_text(oms)
    return await emit_notification(
        NotificationEvent(
            event_type=ntypes.DAILY_DIGEST,
            title=title,
            body=body,
            severity="info",
            payload=payload,
        )
    )
