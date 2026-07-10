"""Daily Quant Journal & Briefing Agent."""

from __future__ import annotations

import time
import logging
from typing import Any

from app.api.state import AppState
from app.services.agent.llm.router import _chat
from app.services.journal.store import upsert_entry, list_entries

logger = logging.getLogger(__name__)

BRIEFING_SYSTEM_PROMPT = """You are a quantitative trading analyst agent. 
Your task is to generate a Daily Briefing that summarizes the trading performance, 
bot actions, and market trends over the past 24 hours.

Analyze the provided trade history, PnL, and any recent journal logs.
Provide a concise, professional markdown report covering:
1. Performance Summary (Realized PnL, Win Rate)
2. Key Trades & Bot Activity
3. Market Observations & Adjustments

Keep it strictly analytical, objective, and quantitative.
Format as markdown without top-level heading (start with ## sections).
"""

async def generate_daily_briefing(state: AppState) -> dict[str, Any]:
    """Generates an LLM summary of the last 24h of trades and logs."""
    now = int(time.time() * 1000)
    day_ago = now - 24 * 3600 * 1000

    # 1. Fetch trades
    oms = getattr(state, "oms", None)
    trades = []
    if oms and hasattr(oms, "get_trade_history"):
        history = oms.get_trade_history()
        if isinstance(history, dict):
            trades = history.get("trades", [])
        elif isinstance(history, list):
            trades = history
        else:
            trades = []
    
    def _parse_ts(val: Any) -> int:
        if isinstance(val, int): return val
        if isinstance(val, float): return int(val)
        if isinstance(val, str):
            if val.isdigit(): return int(val)
            try:
                from datetime import datetime
                val = val.replace("Z", "+00:00")
                return int(datetime.fromisoformat(val).timestamp() * 1000)
            except Exception:
                return 0
        return 0
    
    # Filter for last 24h
    recent_trades = [t for t in trades if isinstance(t, dict) and _parse_ts(t.get("timestamp")) >= day_ago]
    
    total_pnl = sum(t.get("realized_pnl", 0) or 0 for t in recent_trades)
    wins = len([t for t in recent_trades if (t.get("realized_pnl") or 0) > 0])
    losses = len([t for t in recent_trades if (t.get("realized_pnl") or 0) < 0])
    total_closed = wins + losses
    win_rate = round((wins / total_closed) * 100, 2) if total_closed > 0 else 0.0

    # 2. Fetch recent journal logs
    recent_logs = list_entries(limit=50)
    # Filter out agent briefings to avoid recursive loops
    human_logs = [log for log in recent_logs if "daily-briefing" not in (log.get("tags") or [])]
    
    # Build prompt context
    user_prompt = f"""
## 24h Trading Data
- Total Trades Closed: {total_closed}
- Realized PnL: {total_pnl:.2f}
- Win Rate: {win_rate}% ({wins} W / {losses} L)

### Recent Fills (Max 50)
"""
    for t in recent_trades[:50]:
        pnl_str = f" PnL: {t.get('realized_pnl'):.2f}" if t.get('realized_pnl') is not None else ""
        user_prompt += f"- {t.get('symbol')} {t.get('side')} {t.get('quantity')} @ {t.get('average_fill_price')}{pnl_str}\n"

    user_prompt += "\n### Recent Analyst Notes\n"
    if human_logs:
        for log in human_logs[:10]:
            user_prompt += f"- [{log.get('symbol', 'GEN')}] {log.get('note')}\n"
    else:
        user_prompt += "No recent manual journal entries.\n"

    # 3. Call LLM
    result = await _chat(
        system=BRIEFING_SYSTEM_PROMPT,
        user=user_prompt,
        task="narrator",
        max_tokens=900,
        temperature=0.4
    )

    if not result.text:
        err = "LLM failed to generate briefing"
        if result.provider == "off":
            err = "LLM unavailable — start Ollama (ollama serve) or configure OpenRouter"
        elif result.model:
            err = f"{err} (provider={result.provider or 'unknown'}, model={result.model})"
        return {"ok": False, "error": err}

    # 4. Save to Trade Journal
    entry_payload = {
        "tags": ["daily-briefing", "agent"],
        "note": result.text,
        "lesson": "Agent generated daily summary.",
        "symbol": "PORTFOLIO"
    }
    
    upsert_entry(entry_payload)
    
    return {
        "ok": True,
        "briefing": result.text,
        "stats": {
            "pnl": total_pnl,
            "trades": total_closed
        }
    }
