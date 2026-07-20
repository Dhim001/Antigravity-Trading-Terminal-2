"""Trading Chatbot (TRADE_COPILOT) — intent classify → tools → narrate."""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.config import (
    RISK_MAX_DRAWDOWN_PCT,
    TRADE_COPILOT_ENABLED,
    TRADE_COPILOT_HISTORY_LIMIT,
    TRADE_COPILOT_PENDING_TTL_SEC,
    TRADE_COPILOT_SESSION_MAX,
    TRADE_COPILOT_SESSION_TTL_SEC,
    TRADE_COPILOT_USE_LLM,
)
from app.services.agent import copilot_store
from app.services.agent.llm.router import _chat
from app.services.altdata.store import get_aggregate_sentiment
from app.services.bots import analytics as bot_analytics
from app.services.bots.portfolio_risk import build_portfolio_snapshot
from app.services.analytics.portfolio import get_bot_rankings, get_risk_utilization

logger = logging.getLogger(__name__)

INTENT_QUERY = "query"
INTENT_ACTION = "action"
INTENT_ANALYSIS = "analysis"
INTENT_EXPLAIN = "explain"
INTENT_HELP = "help"

_PENDING: dict[str, dict[str, Any]] = {}

_SYMBOL_RE = re.compile(r"\b([A-Z]{1,6}(?:USDT)?|[A-Z]{2,5}/[A-Z]{3})\b")
_ALLOC_RE = re.compile(
    r"(?:\$\s*(\d{2,7}(?:\.\d+)?)|(\d{2,7}(?:\.\d+)?)\s*(?:usd|dollars?)|"
    r"alloc(?:ation)?\s*(?:of\s*|=\s*|:\s*)?\$?\s*(\d{2,7}(?:\.\d+)?))",
    re.I,
)
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_BOT_ID_RE = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.I,
)
_DAYS_RE = re.compile(r"(\d+)\s*[-\s]?\s*days?\b|\b(\d+)\s*d\b", re.I)

_CRYPTO_BASES = frozenset({
    "BTC", "ETH", "BNB", "XRP", "SOL", "TRX", "DOGE", "ADA", "AVAX", "LINK",
    "TON", "SHIB", "SUI", "DOT", "BCH", "XLM", "LTC", "UNI", "APT", "NEAR",
    "MATIC", "POL", "ATOM", "ARB", "OP", "AAVE", "FIL", "ICP", "ETC", "PEPE",
})

# Equities / ETFs we accept as bare tickers (avoid treating random words as symbols).
_COMMON_EQUITIES = frozenset({
    "AAPL", "TSLA", "MSFT", "NVDA", "AMZN", "GOOG", "GOOGL", "META", "NFLX",
    "AMD", "INTC", "SPY", "QQQ", "IWM", "DIA", "SOXL", "TQQQ", "SQQQ",
    "COIN", "MSTR", "PLTR", "UBER", "SHOP", "BABA", "NIO", "RIVN", "SOFI",
})

NARRATE_SYSTEM = """You are TRADE_COPILOT, a conversational trading assistant for this terminal.
Answer from the TOOL RESULTS only.
When narrating a market scan (`scan_market`), make it highly conversational (e.g., "Right now, BTC and ETH are seeing strong buy signals, while SOL is ranging...").
For other responses, use short, readable markdown.
Never dump a command menu unless the user asked for help.
Never invent prices or tool data. Format money with $ and commas.
If requires_confirmation is true, tell the user to confirm or cancel.
"""

# Structured query tools get deterministic markdown — LLM often echoes raw JSON.
_TEMPLATE_ONLY_TOOLS = frozenset({
    "get_portfolio_status",
    "list_bots",
    "get_bot_performance",
    "run_backtest",
    "analyze_symbol",
    "meta_insight",
    "recommend_strategy",
    "strategy_hint",
    "help",
    "clarify",
})

_DEFAULT_ANALYZE_TF = "1m"
_ADX_TREND_THRESHOLD = 25

_MARKET_QUESTION_HINTS = (
    "doing", "happening", "price", "how is", "how's", "what is", "what's",
    "status of", "update on", "look like", "looking", "moving", "pump", "dump",
    "rally", "selloff", "trend", "range", "regime", "analyze", "analysis",
    "chart", "market", "now",
)

_EXPLICIT_HELP_HINTS = (
    "help", "what can you", "commands", "how do i use", "what do you do",
    "capabilities", "examples",
)

# Per-session last analyze_symbol result for follow-up meta questions.
# Bounded by TRADE_COPILOT_SESSION_TTL_SEC + TRADE_COPILOT_SESSION_MAX (MEMORY #3).
_SESSION_MEMORY: dict[str, dict[str, Any]] = {}

# Regime → preferred / avoid (mirrors regime_rotation + proposal table).
_REGIME_STRATEGY_MAP: dict[str, dict[str, list[str]]] = {
    "ranging": {
        "prefer": ["BRS_SCALPING", "VWAP_PULLBACK"],
        "avoid": ["SUPERTREND_ADX", "DONCHIAN_BREAKOUT", "CHART_AGENT"],
    },
    "trending": {
        "prefer": ["SUPERTREND_ADX", "CHART_AGENT", "DONCHIAN_BREAKOUT"],
        "avoid": ["BRS_SCALPING", "MARKET_MAKING"],
    },
    "elevated_vol": {
        "prefer": ["VWAP_PULLBACK"],
        "avoid": ["BRS_SCALPING", "DONCHIAN_BREAKOUT"],
    },
    "compressed": {
        "prefer": ["DONCHIAN_BREAKOUT", "CHART_AGENT"],
        "avoid": ["BRS_SCALPING"],
    },
}

_KNOWN_STRATEGIES = (
    "CHART_AGENT",
    "BRS_SCALPING",
    "SUPERTREND_ADX",
    "MACD_RSI",
    "VWAP_PULLBACK",
    "DONCHIAN_BREAKOUT",
    "MARKET_MAKING",
    "ICT_SMC",
    "EMA_CROSS",
    "MEAN_REVERSION",
    "BREAKOUT",
)


@dataclass
class CopilotResult:
    ok: bool = True
    session_id: str = ""
    intent: str = INTENT_QUERY
    reply: str = ""
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    requires_confirmation: bool = False
    pending_id: str | None = None
    pending_action: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "session_id": self.session_id,
            "intent": self.intent,
            "reply": self.reply,
            "tool_results": self.tool_results,
            "requires_confirmation": self.requires_confirmation,
            "pending_id": self.pending_id,
            "pending_action": self.pending_action,
            "error": self.error,
        }


def _purge_pending() -> None:
    now = time.time()
    expired = [k for k, v in _PENDING.items() if now - float(v.get("created_at") or 0) > TRADE_COPILOT_PENDING_TTL_SEC]
    for k in expired:
        _PENDING.pop(k, None)


def _purge_session_memory() -> None:
    """TTL eviction for in-memory analyze insight map."""
    now = time.time()
    ttl = float(TRADE_COPILOT_SESSION_TTL_SEC)
    expired = [
        k for k, v in _SESSION_MEMORY.items()
        if now - float(v.get("_touched_at") or 0) > ttl
    ]
    for k in expired:
        _SESSION_MEMORY.pop(k, None)


def _evict_session_overflow(keep_id: str | None = None) -> None:
    """Enforce TRADE_COPILOT_SESSION_MAX after inserts (prefer keeping keep_id)."""
    max_sessions = max(1, int(TRADE_COPILOT_SESSION_MAX))
    if len(_SESSION_MEMORY) <= max_sessions:
        return
    ordered = sorted(
        ((k, v) for k, v in _SESSION_MEMORY.items() if k != keep_id),
        key=lambda kv: float(kv[1].get("_touched_at") or 0),
    )
    while len(_SESSION_MEMORY) > max_sessions and ordered:
        k, _ = ordered.pop(0)
        _SESSION_MEMORY.pop(k, None)


def _touch_session(session_id: str) -> dict[str, Any]:
    _purge_session_memory()
    bucket = _SESSION_MEMORY.setdefault(session_id, {})
    bucket["_touched_at"] = time.time()
    _evict_session_overflow(keep_id=session_id)
    return _SESSION_MEMORY.setdefault(session_id, bucket)


def _store_pending(action: dict[str, Any]) -> str:
    _purge_pending()
    pid = str(uuid.uuid4())
    _PENDING[pid] = {"created_at": time.time(), "action": action}
    return pid


def get_pending(pending_id: str) -> dict[str, Any] | None:
    _purge_pending()
    item = _PENDING.get(pending_id)
    return item.get("action") if item else None


def pop_pending(pending_id: str) -> dict[str, Any] | None:
    _purge_pending()
    item = _PENDING.pop(pending_id, None)
    return item.get("action") if item else None


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def fuzzy_resolve_symbol(token: str | None) -> str | None:
    """Fix common typos (BTCUSTD→BTCUSDT) and near-matches to known crypto pairs."""
    if not token:
        return None
    raw = str(token).upper().replace("/", "").replace("-", "").replace(" ", "").strip()
    if not raw or len(raw) < 2:
        return None

    # Common quote typos
    if raw.endswith("USTD") and not raw.endswith("USDT"):
        raw = raw[:-4] + "USDT"
    elif raw.endswith("USDTT"):
        raw = raw[:-5] + "USDT"
    elif raw.endswith("USD") and not raw.endswith("USDT") and len(raw) > 3:
        base = raw[:-3]
        if base in _CRYPTO_BASES:
            raw = f"{base}USDT"

    if raw.endswith("USDT") and len(raw) > 4:
        return raw
    if raw in _CRYPTO_BASES:
        return f"{raw}USDT"

    known_pairs = [f"{b}USDT" for b in _CRYPTO_BASES]
    best: str | None = None
    best_d = 99
    max_d = 2 if len(raw) >= 5 else 1
    for cand in known_pairs:
        d = _levenshtein(raw, cand)
        if d <= max_d and d < best_d:
            best_d = d
            best = cand
    if best:
        return best
    # Near-match bare bases (e.g. BITCOIN-ish typos already handled; BTCC → BTC)
    for base in _CRYPTO_BASES:
        d = _levenshtein(raw, base)
        if d <= 1 and d < best_d:
            best_d = d
            best = f"{base}USDT"
    return best


def extract_symbol(text: str, fallback: str | None = None) -> str | None:
    upper = text.upper().replace("-", " ")
    # Prefer explicit crypto pairs first.
    for m in re.finditer(r"\b([A-Z]{2,10}USDT)\b", upper):
        return m.group(1)
    # Mistyped pairs / near-matches (BTCUSTD, ETHUSTD, …)
    for m in re.finditer(r"\b([A-Z]{3,14})\b", upper):
        tok = m.group(1)
        if tok in {
            "USDT", "USD", "WHAT", "HOW", "DOES", "LOOK", "LIKE", "RIGHT", "NOW",
            "DOING", "HAPPENING", "PRICE", "MARKET", "STATUS", "ABOUT", "WITH",
            "FROM", "THIS", "THAT", "HAVE", "BEEN", "CHART", "AGENT", "TRADE",
        }:
            continue
        if "USD" in tok or tok.endswith("USTD") or len(tok) >= 6:
            resolved = fuzzy_resolve_symbol(tok)
            if resolved:
                return resolved
    # Prefer known crypto bases (BTC → later normalized to BTCUSDT).
    for base in _CRYPTO_BASES:
        if re.search(rf"\b{base}\b", upper):
            return base
    matches = _SYMBOL_RE.findall(upper.replace("/", ""))
    skip = {
        "USD", "USDT", "BUY", "SELL", "ALL", "BOT", "PNL", "ETF", "API",
        "LLM", "ATR", "ADX", "RSI", "MACD", "SL", "TP", "OOS", "WR",
        "HOW", "WHAT", "SHOW", "WITH", "FROM", "THIS", "THAT", "BEST",
        "WORST", "PAUSE", "STOP", "DEPLOY", "RUN", "WHY", "DID", "MY",
        "CHART", "AGENT", "CREATE", "START", "SPIN", "LOOK", "LIKE",
        "RIGHT", "NOW", "NEWS", "ANY", "THE", "AND", "FOR", "ON",
        "TOTAL", "CLOSE", "LIMIT", "RISK", "DRAW", "EQUITY",
        "DOES", "LOOK", "LIKE", "ACTIVE", "PLEASE", "STATUS", "DOING",
        "ABOUT", "HAVE", "BEEN", "JUST", "OVER", "UNDER", "INTO",
        "REGIME", "TREND", "RANGE", "ANALYZE", "ANALYSIS",
        "MARKET", "CONDITION", "CONDITIONS",
        "DAY", "DAYS", "BACKTEST", "TEST", "HORIZON", "WEEK", "WEEKS",
        "MONTH", "MONTHS", "YEAR", "YEARS",
        "CHANGE", "SWITCH", "SET", "USE", "USING", "TIME", "FRAME",
        "TIMEFRAME", "MINUTE", "MINUTES", "MIN", "HOUR", "HOURS",
        "CANDLE", "BAR", "BARS", "WAS", "USED", "OUTCOME", "DIRECTION",
        "STILL", "SAME", "AGAIN", "TELL", "GIVE", "NEED", "WANT",
        "TO", "IN", "AT", "OF", "IS", "ARE", "OR", "IF", "CAN",
        "HAPPENING", "PRICE", "UPDATE",
    }
    for m in matches:
        sym = m.replace("/", "")
        if sym in skip or len(sym) < 2:
            continue
        if "_" in sym:
            continue
        if sym.isdigit():
            continue
        fuzzy = fuzzy_resolve_symbol(sym)
        if fuzzy and (fuzzy.endswith("USDT") or fuzzy in _CRYPTO_BASES):
            return fuzzy
        if sym in _COMMON_EQUITIES or sym.endswith("USDT"):
            return sym
        # Ignore unknown short tokens (ASDF, XYZ) — not real tickers.
        continue
    # Last chance: fuzzy whole tokens against known pairs
    for m in re.finditer(r"\b([A-Z]{3,14})\b", upper):
        tok = m.group(1)
        if tok in skip:
            continue
        resolved = fuzzy_resolve_symbol(tok)
        if resolved and resolved != tok:
            return resolved
        if tok in _COMMON_EQUITIES:
            return tok
    fb = (fallback or "").upper().strip() or None
    if not fb:
        return None
    return fuzzy_resolve_symbol(fb) or (fb if fb in _COMMON_EQUITIES or fb.endswith("USDT") or fb in _CRYPTO_BASES else None)


def looks_like_explicit_help(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return True
    if text in ("help", "?", "hi", "hello", "hey"):
        return True
    return any(k in text for k in _EXPLICIT_HELP_HINTS)


def looks_like_market_question(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    if looks_like_explicit_help(text):
        return False
    if extract_symbol(message):
        return True
    return any(k in text for k in _MARKET_QUESTION_HINTS)

def extract_allocation(text: str, default: float = 1000.0) -> float:
    m = _ALLOC_RE.search(text.replace(",", ""))
    if not m:
        return float(default)
    raw = next((g for g in m.groups() if g), None)
    try:
        return max(10.0, float(raw))
    except (TypeError, ValueError):
        return float(default)


def extract_strategy(text: str, default: str | None = "CHART_AGENT") -> str | None:
    upper = text.upper()
    for name in _KNOWN_STRATEGIES:
        if name in upper or name.replace("_", " ") in upper:
            return name
    if "CHART AGENT" in upper or "CHARTAGENT" in upper:
        return "CHART_AGENT"
    if "BB_STOCH" in upper or "BOLLINGER" in upper:
        return "BRS_SCALPING"
    if "SUPERTREND" in upper:
        return "SUPERTREND_ADX"
    return default


def has_explicit_strategy(text: str) -> bool:
    return extract_strategy(text, default=None) is not None


def extract_regime_from_text(text: str) -> str | None:
    t = (text or "").lower()
    if any(k in t for k in ("elevated vol", "high vol", "volatile")):
        return "elevated_vol"
    if "compressed" in t or "low vol" in t:
        return "compressed"
    if "ranging" in t or "range-bound" in t or "sideways" in t or "chop" in t:
        return "ranging"
    if "trending" in t or "trend market" in t:
        return "trending"
    return None


def recommend_strategy_for_regime(regime: str | None) -> dict[str, Any]:
    key = (regime or "").lower() or "ranging"
    mapping = _REGIME_STRATEGY_MAP.get(key) or _REGIME_STRATEGY_MAP["ranging"]
    prefer = list(mapping["prefer"])
    return {
        "regime": key,
        "primary": prefer[0],
        "alternatives": prefer[1:],
        "avoid": list(mapping["avoid"]),
    }


def remember_insight(session_id: str | None, insight: dict[str, Any] | None) -> None:
    if not session_id or not isinstance(insight, dict) or insight.get("error"):
        return
    sym = str(insight.get("symbol") or "").upper()
    if not sym:
        return
    bucket = _touch_session(session_id)
    bucket["last_insight"] = dict(insight)
    by_sym = bucket.setdefault("by_symbol", {})
    by_sym[sym] = dict(insight)
    # Cap per-session symbol map (insights can be large)
    if len(by_sym) > 24:
        for drop_key in list(by_sym.keys())[: len(by_sym) - 24]:
            by_sym.pop(drop_key, None)
    tf = insight.get("timeframe")
    if tf:
        bucket["preferred_timeframe"] = str(tf)


def remember_timeframe(session_id: str | None, timeframe: str | None) -> None:
    if not session_id or not timeframe:
        return
    try:
        from app.services.market.timeframes import normalize_timeframe

        tf = normalize_timeframe(str(timeframe))
    except ValueError:
        return
    _touch_session(session_id)["preferred_timeframe"] = tf


def get_preferred_timeframe(session_id: str | None, default: str = _DEFAULT_ANALYZE_TF) -> str:
    if session_id and session_id in _SESSION_MEMORY:
        _touch_session(session_id)
        tf = _SESSION_MEMORY[session_id].get("preferred_timeframe")
        if tf:
            return str(tf)
    return default


def get_last_insight(
    session_id: str | None,
    symbol: str | None = None,
) -> dict[str, Any] | None:
    """Return last analyze payload from memory or persisted chat history."""
    sym = (symbol or "").upper() or None
    if session_id and session_id in _SESSION_MEMORY:
        mem = _touch_session(session_id)
        if sym and isinstance(mem.get("by_symbol"), dict):
            hit = mem["by_symbol"].get(sym)
            if isinstance(hit, dict):
                return hit
        last = mem.get("last_insight")
        if isinstance(last, dict) and (
            not sym or str(last.get("symbol") or "").upper() == sym
        ):
            return last

    if not session_id:
        return None
    try:
        msgs = copilot_store.list_messages(session_id, limit=12)
    except Exception:
        return None
    for m in reversed(msgs):
        if m.get("role") != "assistant":
            continue
        payload = m.get("payload") if isinstance(m.get("payload"), dict) else {}
        for tr in reversed(payload.get("tool_results") or []):
            if not isinstance(tr, dict) or tr.get("tool") != "analyze_symbol":
                continue
            data = tr.get("result")
            if not isinstance(data, dict) or data.get("error"):
                continue
            if sym and str(data.get("symbol") or "").upper() != sym:
                continue
            remember_insight(session_id, data)
            return data
    return None


def detect_meta_field(text: str) -> str | None:
    """Follow-up field about a prior analysis (not a fresh market scan)."""
    t = (text or "").lower()
    # Re-analyze requests that mention timeframe must NOT become meta_insight.
    if re.search(
        r"\b(change|switch|set|use|run|analyze|analysis|check|look)\b.*\b("
        r"\d+\s*(?:m|min|h|hour|d|day)|1m|5m|15m|1h|4h|1d)\b",
        t,
    ) or re.search(
        r"\b(on|at|using)\s+(\d+\s*(?:m|min|h|hour)|1m|5m|15m|1h|4h|1d)\b",
        t,
    ):
        return None
    if any(
        k in t
        for k in (
            "timeframe",
            "time frame",
            "which tf",
            "what tf",
            "what interval",
            "candle size",
            "bar size",
        )
    ):
        # "change timeframe to 5m" is a re-run request, not a meta question.
        if re.search(r"\b(change|switch|set|use)\b", t) and re.search(
            r"\b(\d+\s*(?:m|min|h|hour|d|day)|1m|5m|15m|1h|4h|1d)\b", t
        ):
            return None
        return "timeframe"
    if (
        any(
            k in t
            for k in (
                "adx threshold",
                "adx rule",
                "what method",
                "which method",
                "classification rule",
                "based on what",
            )
        )
        or re.search(r"\bhow (did|was|is|do) (you|it|the)\b", t)
        or re.search(r"\b(how|what)\b.*\b(determin|classif|measur|calculat)", t)
    ):
        return "method"
    if "confidence" in t or re.search(r"\b(what|which)\s+score\b", t):
        return "confidence"
    if re.search(r"\b(what|which)\s+signal\b", t) or "directional signal" in t:
        return "signal"
    if any(
        k in t
        for k in ("which regime", "what regime", "regime again", "still trending", "still ranging")
    ):
        return "regime"
    if "used for" in t and any(
        k in t for k in ("market", "direction", "outcome", "analysis", "regime")
    ):
        if any(k in t for k in ("time", "frame", "tf", "interval")):
            return "timeframe"
        return "method"
    return None


def _provenance_bits(data: dict[str, Any] | None = None) -> dict[str, Any]:
    d = data or {}
    thr = int(d.get("adx_threshold") or _ADX_TREND_THRESHOLD)
    return {
        "timeframe": d.get("timeframe") or _DEFAULT_ANALYZE_TF,
        "adx_threshold": thr,
        "bar": d.get("bar") or "closed",
        "method": d.get("method") or f"ADX > {thr} → trending, else ranging",
    }


def _provenance_line(data: dict[str, Any] | None = None) -> str:
    p = _provenance_bits(data)
    return (
        f"_Provenance: timeframe `{p['timeframe']}` · {p['method']} · "
        f"bar: {p['bar']}_"
    )


def _tool_meta_insight(
    session_id: str,
    message: str,
    *,
    active_symbol: str | None = None,
) -> dict[str, Any]:
    field = detect_meta_field(message) or "timeframe"
    sym = normalize_symbol(extract_symbol(message, active_symbol))
    insight = get_last_insight(session_id, sym)
    if insight is None and sym is None:
        insight = get_last_insight(session_id, None)
    if insight and not sym:
        sym = normalize_symbol(str(insight.get("symbol") or "")) or None
    prov = _provenance_bits(insight)
    out: dict[str, Any] = {
        "field": field,
        "symbol": sym,
        "found_prior": bool(insight),
        **prov,
    }
    if insight:
        out["market_regime"] = insight.get("market_regime") or insight.get("trend_regime")
        out["signal"] = insight.get("signal")
        out["score"] = insight.get("score")
        out["confidence"] = insight.get("confidence")
    elif sym:
        out["note"] = (
            f"No prior analysis for {sym} in this session — defaults below apply. "
            f"Ask *what market is {sym} in?* to refresh."
        )
    else:
        out["note"] = "No prior analysis in this session — showing Copilot defaults."
    return out


def extract_days(text: str, default: int = 30) -> int:
    """Parse '90-day', '90 days', '90d' → clamped 1..365."""
    m = _DAYS_RE.search(text or "")
    if not m:
        return int(default)
    raw = m.group(1) or m.group(2)
    try:
        return max(1, min(365, int(raw)))
    except (TypeError, ValueError):
        return int(default)


def normalize_symbol(symbol: str | None) -> str | None:
    """Map bare crypto bases (BTC) → terminal pairs (BTCUSDT); fix typos."""
    if not symbol:
        return None
    fuzzy = fuzzy_resolve_symbol(symbol)
    if fuzzy:
        return fuzzy
    sym = str(symbol).upper().replace("/", "").replace("-", "").strip()
    if not sym:
        return None
    if sym.endswith("USDT"):
        return sym
    if sym.endswith("USD") and len(sym) > 3:
        base = sym[:-3]
        if base in _CRYPTO_BASES:
            return f"{base}USDT"
    if sym in _CRYPTO_BASES:
        return f"{sym}USDT"
    return sym


def classify_intent(message: str) -> tuple[str, str]:
    """Return (intent, tool_hint) using lightweight rules."""
    text = (message or "").strip().lower()
    if not text:
        return INTENT_HELP, "clarify"

    # Meta follow-ups before analyze/market keywords ("what timeframe … market …").
    meta = detect_meta_field(message)
    if meta:
        return INTENT_ANALYSIS, "meta_insight"

    # "change timeframe to 5m" / "ETHUSDT on 5m" → re-analyze at that TF
    from app.services.agent.copilot_agent import extract_timeframe_hint

    tf_hint = extract_timeframe_hint(message)
    if tf_hint and (
        any(k in text for k in ("change", "switch", "set", "use", "to "))
        or any(k in text for k in ("analyze", "market", "regime", "trending", "ranging", "look", "doing"))
        or extract_symbol(message)
    ):
        return INTENT_ANALYSIS, "analyze_symbol"

    if looks_like_explicit_help(text):
        return INTENT_HELP, "help"

    if any(k in text for k in ("why did", "explain", "what happened", "lost money", "won money")):
        return INTENT_EXPLAIN, "explain_trade"

    # Backtest before chart analysis — "CHART_AGENT" contains "chart".
    if "backtest" in text or "back-test" in text or "back test" in text:
        return INTENT_ANALYSIS, "run_backtest"

    # Advisory "what/which bot to deploy" before bare deploy (avoids CHART_AGENT default).
    advisory = any(
        k in text
        for k in (
            "right bot",
            "which bot",
            "what bot",
            "best bot",
            "best strategy",
            "which strategy",
            "what strategy",
            "recommend",
            "suggest",
            "should i deploy",
            "should i run",
            "works best",
            "right strategy",
        )
    )
    if advisory or (
        "deploy" in text
        and not has_explicit_strategy(message)
        and any(k in text for k in ("ranging", "trending", "regime", "right", "best", "which", "what"))
    ):
        return INTENT_ANALYSIS, "recommend_strategy"

    if any(k in text for k in ("deploy", "create bot", "start a bot", "spin up")):
        return INTENT_ACTION, "deploy_bot"
    if any(k in text for k in ("pause all", "pause my bots", "pause bots")):
        return INTENT_ACTION, "pause_all_bots"
    if re.search(r"\bpause\b.*\bbot\b", text) or re.search(r"\bpause\b", text) and "bot" in text:
        return INTENT_ACTION, "pause_bot"
    if any(k in text for k in ("stop all", "kill all bots", "shut down all")):
        return INTENT_ACTION, "stop_all_bots"
    if re.search(r"\bstop\b.*\bbot\b", text) or (re.search(r"\bstop\b", text) and "bot" in text):
        return INTENT_ACTION, "stop_bot"
    if any(k in text for k in ("tighten", "widen", "set stop", "change config", "update config", "min confidence")):
        return INTENT_ACTION, "update_bot_config"

    bot_status = (
        "how are my bots" in text
        or "how are the bots" in text
        or "how are bots" in text
        or "how is my bot" in text
        or "how is the bot" in text
        or "bots doing" in text
        or "bot doing" in text
        or "bot performance" in text
        or "bots performance" in text
        or ("performing" in text and "bot" in text)
        or "win rate" in text
        or ("worst" in text and "bot" in text)
        or "best bot" in text
        or "ranking" in text
    )
    if bot_status:
        return INTENT_QUERY, "get_bot_performance"
    if any(k in text for k in ("list bots", "active bots", "my bots", "show bots", "which bots")):
        return INTENT_QUERY, "list_bots"
    if any(k in text for k in ("exposure", "portfolio", "drawdown", "equity", "risk", "account status")):
        return INTENT_QUERY, "get_portfolio_status"

    if any(k in text for k in (
        "look like",
        "analyze",
        "analysis",
        "trending",
        "ranging",
        "regime",
        "what market",
        "market condition",
        "market regime",
        "which market",
        "doing now",
        "doing today",
        "happening",
        "price of",
        "price action",
        "update on",
        "status of",
    )):
        return INTENT_ANALYSIS, "analyze_symbol"
    if any(k in text for k in (
        "doing a lot",
        "market scan",
        "top movers",
        "active assets",
        "market doing",
    )):
        return INTENT_ANALYSIS, "scan_market"
    # Natural "what is X doing" — require a ticker (avoid stealing bot/portfolio asks)
    if extract_symbol(message) and any(
        k in text for k in ("what is", "what's", "how is", "how's", "doing", "now")
    ):
        return INTENT_ANALYSIS, "analyze_symbol"
    # "what market is ETHUSDT in?" / "ETHUSDT market?"
    if extract_symbol(message) and re.search(r"\bmarket\b", text):
        return INTENT_ANALYSIS, "analyze_symbol"
    # Whole-word "chart" but not CHART_AGENT
    if re.search(r"\bchart\b(?!\s*_?\s*agent)", text):
        return INTENT_ANALYSIS, "analyze_symbol"
    if any(k in text for k in ("news", "sentiment", "headline")):
        return INTENT_ANALYSIS, "get_sentiment"
    if any(k in text for k in ("compare", "which strategy", "works best")):
        return INTENT_ANALYSIS, "recommend_strategy"

    # Follow-ups / short ticker asks — never dump portfolio for "what about BTCUSDT?"
    if extract_symbol(message) and (
        re.search(r"\b(what|how)\s+about\b", text)
        or re.search(r"\bsame\s+(for|on|with)\b", text)
        or re.search(r"\band\s+(for|on|about)\b", text)
        or re.search(r"\b(check|look\s+at)\b", text)
        or any(k in text for k in _MARKET_QUESTION_HINTS)
    ):
        return INTENT_ANALYSIS, "analyze_symbol"

    # Any resolvable ticker → analyze (covers typos + longer natural phrasing)
    if extract_symbol(message):
        return INTENT_ANALYSIS, "analyze_symbol"

    if looks_like_market_question(message):
        return INTENT_ANALYSIS, "analyze_symbol"

    return INTENT_HELP, "clarify"
def _snapshot_dict(oms: Any) -> dict[str, Any]:
    snap = build_portfolio_snapshot(oms)
    return {
        "account_equity": snap.account_equity,
        "gross_exposure": snap.gross_exposure,
        "group_exposure": snap.group_exposure,
        "symbol_exposure": snap.symbol_exposure,
    }


def _tool_list_bots(bot_manager: Any) -> dict[str, Any]:
    bots = []
    for bot in (bot_manager.list_bots_public() if hasattr(bot_manager, "list_bots_public") else []):
        bots.append({
            "id": bot.get("id"),
            "symbol": bot.get("symbol"),
            "strategy": bot.get("strategy"),
            "status": bot.get("status"),
            "allocation": bot.get("allocation"),
            "timeframe": bot.get("timeframe"),
            "total_pnl": bot.get("total_pnl"),
        })
    return {"bots": bots, "count": len(bots)}


def _tool_bot_performance(bot_manager: Any, bot_id: str | None = None) -> dict[str, Any]:
    if bot_id:
        stats = bot_analytics.get_bot_stats(bot_id)
        return {"bot_id": bot_id, "stats": stats}
    rankings = get_bot_rankings(limit=10)
    active = _tool_list_bots(bot_manager)
    return {"rankings": rankings, "active_bots": active}


async def _tool_scan_market(bot_manager: Any, limit: int = 5) -> dict[str, Any]:
    if not bot_manager:
        return {"error": "Bot manager unavailable."}
    from app.config import SCANNER_DEPLOY_WATCHLIST
    from app.services.scanner.market_scanner import MarketScannerService
    scanner = MarketScannerService(bot_manager.oms.feed if hasattr(bot_manager, "oms") else None)
    scan_res = await scanner.scan(SCANNER_DEPLOY_WATCHLIST, signal_filter="any")
    
    rows = scan_res.get("rows", [])
    # Sort by confidence then score
    sorted_assets = sorted(
        rows,
        key=lambda x: (x.get("confidence", 0.0), abs(x.get("score", 0))),
        reverse=True
    )
    
    top_assets = []
    for asset in sorted_assets[:limit]:
        top_assets.append({
            "symbol": asset.get("symbol"),
            "signal": asset.get("signal"),
            "score": asset.get("score"),
            "confidence": asset.get("confidence"),
            "regime": asset.get("atr_regime") or "unknown",
            "close": None, # Price isn't in rows, but keeping structure
        })
    
    return {
        "scanned_count": len(rows),
        "watchlist_size": len(SCANNER_DEPLOY_WATCHLIST),
        "top_movers": top_assets,
    }


def _tool_portfolio(oms: Any) -> dict[str, Any]:
    body = _snapshot_dict(oms)
    try:
        body["risk_utilization"] = get_risk_utilization(oms)
    except Exception as exc:
        body["risk_utilization_error"] = str(exc)
    body["risk_max_drawdown_pct_limit"] = RISK_MAX_DRAWDOWN_PCT
    return body


def _tool_sentiment(symbol: str) -> dict[str, Any]:
    return get_aggregate_sentiment(symbol, lookback_hours=24.0)


def _extract_regime_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Pull ADX trend regime + scoring/vol context from chart insight payloads.

    ChartAgentInsight stores regime under sub_reports (not top-level):
    - trend.trend_regime → 'trending' | 'ranging' | 'unknown' (ADX > 25)
    - regime_weights.regime → scoring bucket (may be elevated_vol / compressed)
    - risk.atr_regime → volatility bucket
    """
    sub = data.get("sub_reports") if isinstance(data.get("sub_reports"), dict) else {}
    trend = sub.get("trend") if isinstance(sub.get("trend"), dict) else {}
    weights = sub.get("regime_weights") if isinstance(sub.get("regime_weights"), dict) else {}
    risk = sub.get("risk") if isinstance(sub.get("risk"), dict) else {}

    def _norm(val: Any) -> str | None:
        if val is None:
            return None
        s = str(val).strip().lower()
        return s or None

    trend_regime = _norm(
        data.get("trend_regime") or trend.get("trend_regime")
    )
    scoring = _norm(
        data.get("regime")
        or weights.get("regime")
        or sub.get("regime")
        or trend_regime
    )
    atr_regime = _norm(risk.get("atr_regime") or data.get("atr_regime"))

    # Prefer ADX trend label for "trending vs ranging" answers.
    market = trend_regime
    if market in (None, "unknown") and scoring in ("trending", "ranging"):
        market = scoring

    return {
        "regime": scoring,
        "trend_regime": trend_regime,
        "market_regime": market,
        "atr_regime": atr_regime,
    }


def _asks_regime_question(text: str) -> bool:
    t = (text or "").lower()
    return any(
        k in t
        for k in (
            "trending",
            "ranging",
            "trend or range",
            "trending or ranging",
            "ranging or trending",
            "market regime",
            "regime",
            "what market",
            "which market",
            "market condition",
            "market is",
            "in a market",
        )
    ) or bool(re.search(r"\bmarket\b.*\b(in|for|on)\b|\b(in|for)\b.*\bmarket\b", t))


def _asks_snapshot_question(text: str) -> bool:
    """Casual 'what's it doing / look like now' — prefer short chat lead over diagnostic dump."""
    t = (text or "").lower().strip()
    if not t or _asks_regime_question(t):
        return False
    return any(
        k in t
        for k in (
            "doing now",
            "doing today",
            "happening",
            "look like",
            "looking",
            "price of",
            "price action",
            "update on",
            "status of",
            "what about",
            "how about",
            "right now",
            "what is ",
            "what's ",
            "how is ",
            "how's ",
            "check ",
            "analyze",
            "analysis",
        )
    )


def _session_asks_regime(session_id: str | None, current_text: str = "") -> bool:
    """True if this turn or a recent user turn asked trending vs ranging."""
    if _asks_regime_question(current_text):
        return True
    # Current turn is a snapshot ask — don't inherit an older regime-style layout.
    if _asks_snapshot_question(current_text):
        return False
    if not session_id:
        return False
    try:
        msgs = copilot_store.list_messages(session_id, limit=8)
    except Exception:
        return False
    for m in reversed(msgs):
        if m.get("role") != "user":
            continue
        if _asks_regime_question(str(m.get("content") or "")):
            return True
    return False


def _regime_headline(symbol: str, fields: dict[str, Any]) -> str:
    market = fields.get("market_regime") or fields.get("trend_regime") or fields.get("regime")
    if market == "trending":
        return f"**{symbol} is in a TRENDING market**"
    if market == "ranging":
        return f"**{symbol} is in a RANGING market**"
    if market == "elevated_vol":
        return f"**{symbol} — elevated volatility** (trend/range unclear from ADX)"
    if market == "compressed":
        return f"**{symbol} — compressed volatility** (trend/range unclear from ADX)"
    return f"**{symbol} — regime unknown** (ADX unavailable)"


def _fmt_conf(conf: Any) -> str:
    try:
        return f"{float(conf):.2f}"
    except (TypeError, ValueError):
        return str(conf) if conf is not None else "—"


def _analyze_snapshot_lines(sym: str, data: dict[str, Any], fields: dict[str, Any]) -> list[str]:
    """Short conversational lead + compact details for 'doing now' asks."""
    market = (
        fields.get("market_regime")
        or fields.get("trend_regime")
        or fields.get("regime")
        or "unknown"
    )
    atr = fields.get("atr_regime")
    tf = data.get("timeframe") or _DEFAULT_ANALYZE_TF
    signal = str(data.get("signal") or "NONE").upper()
    score = data.get("score")
    conf_s = _fmt_conf(data.get("confidence"))

    vol_bit = f", **{atr}** vol" if atr and atr != "normal" else ""
    lead = f"**{sym}** (`{tf}`): **{market}**{vol_bit}."

    if signal in ("NONE", "HOLD", "NULL", "N/A"):
        sig = f"No clear directional signal (`{signal}`, conf {conf_s})."
    else:
        sig = f"Directional bias: **{signal}** (score {score}, conf {conf_s})."

    reasons = [str(r).strip().rstrip(".") for r in (data.get("reasons") or []) if r][:2]
    if reasons:
        sig = f"{sig} {' · '.join(reasons)}."

    detail_bits = [
        f"ADX > {_ADX_TREND_THRESHOLD} → trending, else ranging",
        f"score {score}" if score is not None else None,
        f"closed `{tf}` bars",
    ]
    details = "_Details: " + " · ".join(b for b in detail_bits if b) + "_"
    return [lead, "", sig, "", details]


async def _tool_analyze(
    state: Any,
    symbol: str,
    timeframe: str = _DEFAULT_ANALYZE_TF,
) -> dict[str, Any]:
    analyst = getattr(state, "chart_analyst", None)
    if analyst is None or not hasattr(analyst, "analyze"):
        return {"error": "Chart analyst unavailable", "symbol": symbol}
    tf = timeframe or _DEFAULT_ANALYZE_TF
    insight = await analyst.analyze(symbol, timeframe=tf, broadcast=False, force_llm=False)
    if insight is None:
        return {"error": "No insight produced (need more bars or agent disabled)", "symbol": symbol}
    if hasattr(insight, "to_dict"):
        data = insight.to_dict()
    elif isinstance(insight, dict):
        data = insight
    else:
        data = {"raw": str(insight)}
    regime = _extract_regime_fields(data if isinstance(data, dict) else {})
    thr = _ADX_TREND_THRESHOLD
    out: dict[str, Any] = {
        "symbol": symbol,
        "signal": data.get("signal"),
        "score": data.get("score"),
        "confidence": data.get("confidence"),
        "regime": regime.get("regime"),
        "trend_regime": regime.get("trend_regime"),
        "market_regime": regime.get("market_regime"),
        "atr_regime": regime.get("atr_regime"),
        "reasons": (data.get("reasons") or [])[:5],
        "timeframe": tf,
        "adx_threshold": thr,
        "bar": "closed",
        "method": f"ADX > {thr} → trending, else ranging",
    }
    return out


async def _tool_recommend_strategy(
    state: Any,
    message: str,
    *,
    active_symbol: str | None = None,
) -> dict[str, Any]:
    """Pick a strategy from stated/live regime — do not invent a deploy confirm."""
    sym = normalize_symbol(extract_symbol(message, active_symbol))
    text_regime = extract_regime_from_text(message)
    live_regime = None
    analysis: dict[str, Any] | None = None
    if sym:
        from app.services.agent.copilot_agent import extract_timeframe_hint

        tf = extract_timeframe_hint(message) or _DEFAULT_ANALYZE_TF
        analysis = await _tool_analyze(state, sym, timeframe=tf)
        if not analysis.get("error"):
            live_regime = (
                analysis.get("market_regime")
                or analysis.get("trend_regime")
                or analysis.get("regime")
            )
            atr = analysis.get("atr_regime")
            if atr == "elevated":
                live_regime = "elevated_vol"
            elif atr == "compressed" and live_regime != "trending":
                live_regime = "compressed"

    # Prefer explicit user wording ("still ranging") over a conflicting live read.
    regime = text_regime or live_regime or "ranging"
    rec = recommend_strategy_for_regime(regime)
    alloc = extract_allocation(message)
    primary = rec["primary"]
    out: dict[str, Any] = {
        "symbol": sym,
        "regime": regime,
        "regime_source": "user_text" if text_regime else ("live_analysis" if live_regime else "default"),
        "primary": primary,
        "alternatives": rec["alternatives"],
        "avoid": rec["avoid"],
        "allocation": alloc,
        "deploy_example": (
            f"Deploy {primary} on {sym} with ${alloc:.0f}"
            if sym
            else f"Deploy {primary} on <SYMBOL> with ${alloc:.0f}"
        ),
    }
    if analysis and not analysis.get("error"):
        out["live_signal"] = analysis.get("signal")
        out["live_market_regime"] = analysis.get("market_regime")
        # Stash for meta follow-ups ("what timeframe…") even when path was recommend.
        out["_insight"] = analysis
    elif analysis and analysis.get("error"):
        out["analysis_note"] = analysis.get("error")
    return out


async def _tool_run_backtest(
    state: Any,
    symbol: str,
    strategy: str,
    days: int,
    *,
    timeframe: str = "1m",
    allocation: float = 1000.0,
) -> dict[str, Any]:
    """Run a backtest for chat.

    Short horizons run inline. Longer / heavy runs are queued on the existing
    backtest job worker so the Copilot HTTP request does not time out.
    """
    import asyncio

    from app.services.bots.backtest_perf import backtest_tier_meta

    days = max(1, min(365, int(days)))
    config = {
        "allocation": float(allocation),
        "pipeline_source": "copilot",
    }
    req = {
        "symbol": symbol,
        "strategy": strategy,
        "config": config,
        "days": days,
        "timeframe": timeframe,
        "allocation": float(allocation),
    }
    tier_meta = backtest_tier_meta(req)
    # Chat sync budget is tighter than the Algo panel — queue anything likely >~2 min.
    queue_async = tier_meta.get("tier") == "deferred" or days > 10

    if queue_async:
        from app.api.context import RequestContext
        from app.api.handlers.bots import _execute_backtest
        from app.services.bots.backtest_job_store import (
            create_backtest_job,
            set_job_status,
            update_job_progress,
        )
        from app.services.bots.backtest_jobs import start_job

        job_req = {
            **req,
            "tier": "deferred",
            "estimated_sec": tier_meta.get("estimated_sec"),
            "source": "copilot",
        }
        job_id = create_backtest_job(job_req, status="pending", client_key="copilot")
        start_job(None, job_id)
        update_job_progress(job_id, {
            "pct": 0,
            "phase": "queued",
            "message": (
                f"Queued {days}d {strategy} backtest on {symbol} "
                f"(~{tier_meta.get('estimated_sec')}s est.)…"
            ),
            "job_id": job_id,
            "tier": "deferred",
            "estimated_sec": tier_meta.get("estimated_sec"),
        })

        ctx = RequestContext(
            websocket=None,
            manager=getattr(state, "manager", None),
            oms=getattr(state, "oms", None),
            bot_manager=getattr(state, "bot_manager", None),
            backtester=getattr(state, "backtester", None),
            chart_analyst=getattr(state, "chart_analyst", None),
            message=job_req,
            action="run_backtest",
        )

        async def _run_queued() -> None:
            try:
                await _execute_backtest(
                    ctx,
                    job_id=job_id,
                    symbol=symbol,
                    strategy=strategy,
                    config=config,
                    days=days,
                    interval=None,
                    timeframe=timeframe,
                )
            except Exception as exc:
                logger.exception("Copilot queued backtest %s failed", job_id)
                set_job_status(job_id, "failed", error=str(exc) or "Backtest failed")

        asyncio.create_task(_run_queued())
        return {
            "queued": True,
            "job_id": job_id,
            "symbol": symbol,
            "strategy": strategy,
            "days": days,
            "timeframe": timeframe,
            "estimated_sec": tier_meta.get("estimated_sec"),
            "message": (
                f"Queued {days}d backtest — track job `{job_id}` in the Algo / Backtest panel."
            ),
        }

    from app.services.archive.resolve import resolve_backtest_candles

    feed = getattr(getattr(state, "oms", None), "feed", None) or getattr(state, "feed", None)
    bt = getattr(state, "backtester", None)

    try:
        candles, meta = await asyncio.to_thread(
            resolve_backtest_candles,
            symbol,
            feed,
            days=days,
            timeframe=timeframe,
        )
    except Exception as exc:
        return {
            "error": f"Failed to resolve history: {exc}",
            "symbol": symbol,
            "strategy": strategy,
            "days": days,
        }

    bar_count = len(candles or [])
    replayed = float((meta or {}).get("replayed_days") or 0.0)
    if not candles or bar_count < 50:
        note = (meta or {}).get("range_note") or (meta or {}).get("resolution_note") or ""
        return {
            "error": (
                f"Not enough history for {days}d {timeframe} backtest "
                f"(got {bar_count} bars ≈{replayed:.1f}d). {note}"
            ).strip(),
            "symbol": symbol,
            "strategy": strategy,
            "days": days,
            "bar_count": bar_count,
            "meta": meta,
        }

    if bt is None or not hasattr(bt, "run_backtest"):
        from app.services.bots.backtester import BacktesterService

        bt = BacktesterService()

    try:
        result = await asyncio.to_thread(
            bt.run_backtest,
            symbol,
            strategy,
            config,
            candles,
        )
    except Exception as exc:
        return {
            "error": f"Backtest failed: {exc}",
            "symbol": symbol,
            "strategy": strategy,
            "days": days,
        }

    if not isinstance(result, dict):
        return {"error": "Invalid backtest result", "symbol": symbol, "strategy": strategy, "days": days}
    if result.get("error"):
        return {
            "error": result["error"],
            "symbol": symbol,
            "strategy": strategy,
            "days": days,
            "bar_count": bar_count,
        }

    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    return {
        "symbol": symbol,
        "strategy": strategy,
        "days": days,
        "timeframe": timeframe,
        "bar_count": bar_count,
        "replayed_days": replayed or (meta or {}).get("replayed_days"),
        "win_rate": result.get("win_rate") if result.get("win_rate") is not None else summary.get("win_rate"),
        "total_pnl": result.get("total_pnl") if result.get("total_pnl") is not None else summary.get("total_pnl"),
        "max_drawdown": result.get("max_drawdown") if result.get("max_drawdown") is not None else summary.get("max_drawdown"),
        "trade_count": result.get("trade_count") if result.get("trade_count") is not None else summary.get("total_trades"),
        "return_pct": summary.get("return_pct"),
        "sharpe_ratio": summary.get("sharpe_ratio"),
        "starting_equity": result.get("starting_equity") or summary.get("starting_equity"),
        "range_note": (meta or {}).get("range_note") or (meta or {}).get("resolution_note"),
    }


async def _tool_explain(state: Any, bot_id: str, trade_id: str | None = None) -> dict[str, Any]:
    from app.services.agent.trade_explain import explain_trade
    from app.database import get_connection

    if not bot_id:
        return {"error": "bot_id required"}
    tid = trade_id
    if not tid:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id FROM bot_trades
                WHERE bot_id = ? AND is_exit = 1
                ORDER BY timestamp DESC LIMIT 1
                """,
                (bot_id,),
            )
            row = cur.fetchone()
            if row:
                tid = str(row["id"] if isinstance(row, dict) else row[0])
        finally:
            conn.close()
    if not tid:
        return {"error": "No exit trades found for this bot"}
    try:
        result = await explain_trade(
            bot_id,
            str(tid),
            chart_analyst=getattr(state, "chart_analyst", None),
            use_llm=TRADE_COPILOT_USE_LLM,
        )
        return {
            "bot_id": bot_id,
            "trade_id": tid,
            "narrative": result.get("narrative") or result.get("summary"),
            "insight": result.get("insight"),
            "trade": {
                k: result.get("trade", {}).get(k)
                for k in ("symbol", "side", "price", "pnl", "is_exit")
                if isinstance(result.get("trade"), dict)
            },
        }
    except Exception as exc:
        return {"error": str(exc)}
async def _tool_explain_bot_events(bot_id: str, limit: int = 5) -> dict[str, Any]:
    from app.database import get_connection
    import json
    
    conn = get_connection()
    try:
        cur = conn.cursor()
        if bot_id:
            cur.execute(
                "SELECT level, message, meta, timestamp FROM bot_logs WHERE bot_id = ? AND level IN ('WARN', 'ERROR', 'INFO') ORDER BY timestamp DESC LIMIT ?",
                (bot_id, limit)
            )
        else:
            cur.execute(
                "SELECT bot_id, level, message, meta, timestamp FROM bot_logs WHERE level IN ('WARN', 'ERROR') ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            )
        rows = cur.fetchall()
        events = []
        for r in rows:
            event = dict(r)
            meta_json = event.get("meta")
            if meta_json:
                try:
                    event["meta"] = json.loads(meta_json)
                except Exception:
                    event["meta"] = None
            events.append(event)
        
        return {
            "bot_id": bot_id,
            "events": events
        }
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def _money(v: Any) -> str:
    try:
        return f"${float(v or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _pnl(v: Any) -> str:
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        n = 0.0
    sign = "+" if n > 0 else ""
    return f"{sign}${n:,.2f}"


def _pct(v: Any) -> str:
    try:
        return f"{float(v or 0):.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def _format_bot_row(b: dict[str, Any]) -> str:
    sym = b.get("symbol") or b.get("bot_id") or "?"
    strat = b.get("strategy") or "—"
    status = b.get("status") or "—"
    pnl = _pnl(b.get("total_pnl") if b.get("total_pnl") is not None else b.get("pnl"))
    wr = b.get("win_rate")
    exits = b.get("exit_count")
    alloc = b.get("allocation")
    bits = [f"**{sym}** · `{strat}` · {status}", f"PnL {pnl}"]
    if wr is not None:
        bits.append(f"WR {_pct(wr)}")
    if exits is not None:
        bits.append(f"{exits} exits")
    if alloc is not None:
        bits.append(f"alloc {_money(alloc)}")
    return " · ".join(bits[:2]) + ("\n  " + " · ".join(bits[2:]) if len(bits) > 2 else "")


def _template_reply(
    intent: str,
    tool_results: list[dict],
    pending: dict | None = None,
    *,
    user_message: str = "",
    session_id: str | None = None,
) -> str:
    if pending:
        params = pending.get("params") or {}
        lines = [f"**Confirm action:** `{pending.get('type')}`", ""]
        for k, v in list(params.items())[:8]:
            lines.append(f"- **{k}:** `{v}`")
        lines.extend(["", "Click **Confirm** to run, or **Cancel**."])
        return "\n".join(lines)

    parts: list[str] = []
    for tr in tool_results:
        name = tr.get("tool")
        data = tr.get("result") or {}
        if data.get("error"):
            parts.append(f"**Error** — {data['error']}")
            continue

        if name == "get_portfolio_status":
            ru = data.get("risk_utilization") if isinstance(data.get("risk_utilization"), dict) else {}
            equity = data.get("account_equity") or ru.get("account_equity")
            gross = data.get("gross_exposure") or ru.get("gross_exposure")
            lines = [
                "**Portfolio status**",
                f"- Equity: {_money(equity)}",
                f"- Gross exposure: {_money(gross)}",
            ]
            if ru.get("gross_utilization_pct") is not None:
                lines.append(
                    f"- Gross utilization: {_pct(ru.get('gross_utilization_pct'))} "
                    f"(cap {_pct(ru.get('max_gross_pct'))})"
                )
            if ru.get("open_bot_positions") is not None:
                lines.append(f"- Open bot positions: {ru.get('open_bot_positions')}")
            dd = ru.get("current_drawdown_pct")
            if dd is None:
                dd = ru.get("drawdown_pct")
            if dd is not None:
                limit = data.get("risk_max_drawdown_pct_limit")
                limit_s = f" / limit {_pct(limit)}" if limit is not None else ""
                lines.append(f"- Drawdown: {_pct(dd)}{limit_s}")
            sym_exp = data.get("symbol_exposure") or {}
            if isinstance(sym_exp, dict) and sym_exp:
                top = sorted(sym_exp.items(), key=lambda kv: abs(float(kv[1] or 0)), reverse=True)[:5]
                lines.append("- Top symbols: " + ", ".join(f"{s} {_money(e)}" for s, e in top))
            parts.append("\n".join(lines))

        elif name == "list_bots":
            bots = data.get("bots") or []
            if not bots:
                parts.append("**Active bots**\n\nNo bots running right now.")
            else:
                lines = [f"**Active bots** ({len(bots)})", ""]
                for b in bots[:15]:
                    lines.append(f"- {_format_bot_row(b)}")
                parts.append("\n".join(lines))

        elif name == "get_bot_performance":
            if data.get("stats") and data.get("bot_id"):
                s = data["stats"]
                parts.append(
                    "**Bot stats**\n"
                    f"- Bot: `{data.get('bot_id')}`\n"
                    f"- Exits: {s.get('exit_count') or 0}\n"
                    f"- Win rate: {_pct(s.get('win_rate'))}\n"
                    f"- Total PnL: {_pnl(s.get('total_pnl'))}\n"
                    f"- Today: {_pnl(s.get('daily_pnl'))}"
                )
            else:
                rankings = data.get("rankings") or {}
                top = rankings.get("top") or []
                active = (data.get("active_bots") or {}).get("bots") or []
                total = rankings.get("total_bots")
                header = f"**Bot performance**"
                if total is not None:
                    header += f" · {total} bots tracked"
                lines = [header, ""]
                if active:
                    running = [b for b in active if str(b.get("status") or "").upper() == "RUNNING"]
                    paused = [b for b in active if str(b.get("status") or "").upper() == "PAUSED"]
                    lines.append(
                        f"Live: **{len(running)}** running · **{len(paused)}** paused · "
                        f"**{len(active)}** total"
                    )
                    lines.append("")
                if top:
                    lines.append("**By PnL**")
                    for i, b in enumerate(top[:8], 1):
                        lines.append(f"{i}. {_format_bot_row(b)}")
                elif active:
                    lines.append("**Active**")
                    for b in active[:10]:
                        lines.append(f"- {_format_bot_row(b)}")
                else:
                    lines.append("No bot history yet — deploy a bot or wait for closed trades.")
                parts.append("\n".join(lines))

        elif name == "analyze_symbol":
            sym = data.get("symbol") or "Symbol"
            fields = {
                "regime": data.get("regime"),
                "trend_regime": data.get("trend_regime"),
                "market_regime": data.get("market_regime"),
                "atr_regime": data.get("atr_regime"),
            }
            # Back-compat if older tool payloads only have regime.
            if not fields.get("market_regime") and not fields.get("trend_regime"):
                fields = _extract_regime_fields(data)

            if _asks_snapshot_question(user_message) or (
                not _session_asks_regime(session_id, user_message)
                and not _asks_regime_question(user_message)
            ):
                # Default / "doing now" → short chat lead; details one quiet line.
                parts.append("\n".join(_analyze_snapshot_lines(str(sym), data, fields)))
            elif _session_asks_regime(session_id, user_message):
                lines = [
                    _regime_headline(str(sym), fields),
                    "",
                    f"Based on ADX (trending if ADX > {_ADX_TREND_THRESHOLD}, otherwise ranging).",
                ]
                tr = fields.get("trend_regime") or fields.get("market_regime")
                if tr:
                    lines.append(f"- Trend regime: **{tr}**")
                atr = fields.get("atr_regime")
                if atr and atr != "normal":
                    lines.append(f"- Volatility: {atr}")
                scoring = fields.get("regime")
                if scoring and scoring not in (tr, "trending", "ranging"):
                    lines.append(f"- Scoring context: {scoring}")
                lines.extend([
                    "",
                    f"Directional signal (separate from regime): `{data.get('signal')}` "
                    f"(score {data.get('score')}, conf {_fmt_conf(data.get('confidence'))})",
                ])
                reasons = data.get("reasons") or []
                if reasons:
                    lines.append("- Reasons:")
                    for r in reasons[:4]:
                        lines.append(f"  - {r}")
                lines.append("")
                lines.append(_provenance_line(data))
                parts.append("\n".join(lines))
            else:
                lines = [
                    f"**{sym} analysis**",
                    f"- Signal: `{data.get('signal')}`",
                    f"- Score: {data.get('score')} · Confidence: {_fmt_conf(data.get('confidence'))}",
                ]
                market = fields.get("market_regime") or fields.get("trend_regime") or fields.get("regime")
                if market:
                    lines.append(f"- Market regime: **{market}** (ADX)")
                atr = fields.get("atr_regime")
                if atr and atr != "normal":
                    lines.append(f"- Volatility: {atr}")
                reasons = data.get("reasons") or []
                if reasons:
                    lines.append("- Reasons:")
                    for r in reasons[:4]:
                        lines.append(f"  - {r}")
                lines.append("")
                lines.append(_provenance_line(data))
                parts.append("\n".join(lines))

        elif name == "meta_insight":
            field = data.get("field") or "timeframe"
            sym = data.get("symbol")
            prefix = f" for {sym}" if sym else ""
            if field == "timeframe":
                lines = [
                    f"**Timeframe{prefix}:** `{data.get('timeframe') or _DEFAULT_ANALYZE_TF}`",
                    "",
                    "Copilot regime/direction uses closed bars at that interval "
                    "(default `1m` unless a different TF was requested).",
                ]
            elif field == "method":
                lines = [
                    f"**Method{prefix}:** {data.get('method') or _provenance_bits()['method']}",
                    f"- ADX threshold: `{data.get('adx_threshold') or _ADX_TREND_THRESHOLD}`",
                    f"- Bars: `{data.get('bar') or 'closed'}` · TF `{data.get('timeframe') or _DEFAULT_ANALYZE_TF}`",
                ]
            elif field == "confidence":
                if data.get("found_prior"):
                    lines = [
                        f"**Confidence{prefix}:** `{data.get('confidence')}` "
                        f"(score `{data.get('score')}`)",
                    ]
                else:
                    lines = ["No prior analysis in this session — ask for a symbol first."]
            elif field == "signal":
                if data.get("found_prior"):
                    lines = [
                        f"**Signal{prefix}:** `{data.get('signal')}` "
                        f"(score `{data.get('score')}`, conf `{data.get('confidence')}`)",
                    ]
                else:
                    lines = ["No prior analysis in this session — ask for a symbol first."]
            elif field == "regime":
                if data.get("found_prior") and data.get("market_regime"):
                    lines = [f"**Regime{prefix}:** **{data.get('market_regime')}**"]
                else:
                    lines = ["No prior regime stored — ask *what market is <SYMBOL> in?*"]
            else:
                lines = [f"**{field}{prefix}:** see provenance below."]
            if data.get("note"):
                lines.extend(["", data["note"]])
            lines.extend(["", _provenance_line(data)])
            parts.append("\n".join(lines))

        elif name == "run_backtest":
            if data.get("queued"):
                est = data.get("estimated_sec")
                est_s = f" (~{est}s est.)" if est else ""
                parts.append(
                    f"**Backtest queued**{est_s}\n"
                    f"- Symbol: **{data.get('symbol')}**\n"
                    f"- Strategy: `{data.get('strategy')}`\n"
                    f"- Horizon: {data.get('days')}d · {data.get('timeframe') or '1m'}\n"
                    f"- Job: `{data.get('job_id')}`\n"
                    "- Track progress in the **Algo / Backtest** panel — "
                    "long CHART_AGENT runs are too heavy for an inline chat reply."
                )
            else:
                lines = [
                    f"**Backtest · {data.get('symbol')} · `{data.get('strategy')}`**",
                    f"- Horizon: {data.get('days')}d · {data.get('timeframe') or '1m'}",
                ]
                if data.get("bar_count") is not None:
                    lines.append(f"- Bars used: {data.get('bar_count')}")
                if data.get("replayed_days") is not None:
                    try:
                        lines.append(f"- History coverage: ≈{float(data.get('replayed_days')):.1f}d")
                    except (TypeError, ValueError):
                        pass
                lines.extend([
                    f"- Trades: {data.get('trade_count') if data.get('trade_count') is not None else '—'}",
                    f"- Win rate: {_pct(data.get('win_rate'))}",
                    f"- Total PnL: {_pnl(data.get('total_pnl'))}",
                ])
                if data.get("return_pct") is not None:
                    lines.append(f"- Return: {_pct(data.get('return_pct'))}")
                if data.get("max_drawdown") is not None:
                    lines.append(f"- Max drawdown: {_pct(data.get('max_drawdown'))}")
                if data.get("sharpe_ratio") is not None:
                    lines.append(f"- Sharpe: {data.get('sharpe_ratio')}")
                if data.get("range_note"):
                    lines.append(f"- Note: {data.get('range_note')}")
                parts.append("\n".join(lines))

        elif name == "get_sentiment":
            parts.append(
                f"**Sentiment · {data.get('symbol')}**\n"
                f"- Score: {data.get('aggregate_score')}\n"
                f"- Mentions: {data.get('mention_count')}"
            )

        elif name == "explain_trade":
            parts.append(data.get("narrative") or "Trade explained.")

        elif name == "help":
            parts.append(data.get("text") or "")

        elif name == "clarify":
            parts.append(data.get("text") or "")

        elif name == "strategy_hint":
            parts.append(data.get("message") or "See Optimization / Sweep for strategy comparison.")

        elif name == "recommend_strategy":
            sym = data.get("symbol") or "this symbol"
            regime = data.get("regime") or "unknown"
            primary = data.get("primary") or "BRS_SCALPING"
            alts = data.get("alternatives") or []
            avoid = data.get("avoid") or []
            lines = [
                f"**Recommended bot for {sym}** ({regime} market)",
                f"- Primary: `{primary}`",
            ]
            if alts:
                lines.append(f"- Alternatives: {', '.join(f'`{a}`' for a in alts)}")
            if avoid:
                lines.append(f"- Avoid in {regime}: {', '.join(f'`{a}`' for a in avoid)}")
            src = data.get("regime_source")
            if src == "user_text":
                lines.append("- Regime taken from your message.")
            elif src == "live_analysis" and data.get("live_market_regime"):
                lines.append(f"- Live ADX regime: **{data.get('live_market_regime')}**")
            if data.get("analysis_note"):
                lines.append(f"- Note: {data.get('analysis_note')}")
            lines.extend([
                "",
                f"To deploy, say: *{data.get('deploy_example') or f'Deploy {primary} on {sym}'}*",
            ])
            parts.append("\n".join(lines))

        else:
            parts.append(f"**{name}** completed.")

    return "\n\n".join(p for p in parts if p) or "Done."


def _should_use_template_only(tool_results: list[dict], pending: dict | None) -> bool:
    if pending:
        return True
    if not tool_results:
        return True
    return all((tr.get("tool") in _TEMPLATE_ONLY_TOOLS) for tr in tool_results)


async def _narrate(
    message: str,
    intent: str,
    tool_results: list[dict],
    pending: dict | None,
    *,
    session_id: str | None = None,
) -> str:
    template = _template_reply(
        intent,
        tool_results,
        pending,
        user_message=message or "",
        session_id=session_id,
    )
    if not TRADE_COPILOT_USE_LLM or _should_use_template_only(tool_results, pending):
        return template
    try:
        from app.services.agent.llm.payloads import dumps_payload

        user = dumps_payload({
            "user_message": message,
            "intent": intent,
            "tool_results": tool_results,
            "pending_action": pending,
        })
        result = await _chat(
            system=NARRATE_SYSTEM,
            user=user,
            task="narrator",
            max_tokens=500,
            temperature=0.3,
        )
        text = (result.text or "").strip()
        # Reject obvious raw-JSON regurgitation.
        if text and not re.search(r'^["\']?\w+["\']?\s*:', text) and "{" not in text[:40]:
            return text
    except Exception as exc:
        logger.debug("copilot narrate skipped: %s", exc)
    return template


def _help_text() -> dict[str, Any]:
    return {
        "text": (
            "I'm **TRADE_COPILOT** — ask in plain English. Examples:\n"
            "- *What is BTCUSDT doing now?*\n"
            "- *How are my bots doing?*\n"
            "- *What's my exposure / drawdown?*\n"
            "- *Is ADAUSDT ranging — what bot should I deploy?*\n"
            "- *Run a 90-day backtest on CHART_AGENT for BTC*\n"
            "- *Any news on BTCUSDT?*\n"
            "- *Deploy BRS_SCALPING on ETHUSDT with $2000* (asks to confirm)\n"
            "- *Pause all bots* / *Stop bot &lt;id&gt;*\n"
            "- *Why did my last trade lose?*\n"
        )
    }


def _clarify_text(active_symbol: str | None = None) -> dict[str, Any]:
    tip = (
        f" Active chart is **{active_symbol}** — ask about it, or name another ticker."
        if active_symbol
        else " Name a ticker (e.g. BTCUSDT), or ask about your portfolio / bots."
    )
    return {
        "text": (
            "I didn't catch that."
            f"{tip}\n"
            "Say **help** for more examples."
        )
    }


_AGENT_NARRATE_COOLDOWN_SEC = 900.0  # same fingerprint at most once per 15m
_agent_narrate_seen: dict[str, float] = {}


def _agent_event_fingerprint(event_type: str, payload: dict[str, Any]) -> str:
    """Stable key so the same action is not re-broadcast as a new chat spam."""
    p = payload or {}
    parts = [
        str(event_type or "").strip().lower(),
        str(p.get("action") or "").strip().lower(),
        str(p.get("bot_id") or "").strip(),
        str(p.get("symbol") or "").strip().upper(),
        str(p.get("from_strategy") or p.get("old_strategy") or "").strip().upper(),
        str(p.get("to_strategy") or p.get("new_strategy") or "").strip().upper(),
        str(p.get("reason") or "").strip().lower()[:120],
    ]
    return "|".join(parts)


def _agent_narrate_allowed(fingerprint: str) -> bool:
    now = time.time()
    # Drop expired fingerprints so the map cannot grow unbounded.
    stale = [k for k, ts in _agent_narrate_seen.items() if now - ts > _AGENT_NARRATE_COOLDOWN_SEC * 4]
    for k in stale:
        _agent_narrate_seen.pop(k, None)
    last = _agent_narrate_seen.get(fingerprint)
    if last is not None and (now - last) < _AGENT_NARRATE_COOLDOWN_SEC:
        return False
    _agent_narrate_seen[fingerprint] = now
    return True


def _template_agent_narration(event_type: str, payload: dict[str, Any]) -> str | None:
    """Deterministic chat text for real agent actions — no LLM inventing heartbeats."""
    p = payload or {}
    action = str(p.get("action") or "").strip().lower()
    symbol = str(p.get("symbol") or "").strip().upper() or None
    bot_id = str(p.get("bot_id") or "").strip() or None
    agent = str(event_type or "Agent").strip()

    if action in ("rotated_strategy", "regime_changed"):
        frm = p.get("from_strategy") or p.get("old_strategy") or "?"
        to = p.get("to_strategy") or p.get("new_strategy") or "?"
        regime = p.get("regime") or p.get("new_regime") or "current"
        sym = symbol or "a bot"
        return (
            f"Market shifted to **{regime}** regime. I rotated the **{sym}** bot "
            f"from `{frm}` to `{to}`."
        )

    if action in ("paused_all_bots", "paused_portfolio"):
        n = p.get("bots_paused")
        reason = p.get("reason") or "risk threshold breached"
        if n is not None:
            return f"Risk Sentinel paused **{n}** bot(s): {reason}."
        return f"Risk Sentinel paused active bots: {reason}."

    if action in ("paused_single_bot", "bot_paused"):
        label = symbol or bot_id or "a bot"
        reason = p.get("reason") or "risk rule triggered"
        return f"Risk Sentinel paused **{label}**: {reason}."

    if action in ("decay_detected", "alpha_decay"):
        label = symbol or bot_id or "a bot"
        reasons = p.get("reasons") or []
        first = reasons[0] if isinstance(reasons, list) and reasons else (p.get("reason") or "edge degradation")
        paused = " Auto-paused." if p.get("auto_paused") else ""
        return f"Alpha Decay flagged **{label}**: {first}.{paused}"

    if action in ("bot_deployed", "deployed"):
        label = symbol or bot_id or "a symbol"
        return f"Scanner deployed a bot on **{label}**."

    # Unknown / empty actions must not invent "I'm online" chatter.
    logger.debug("Skipping agent narrate for %s action=%s (no template)", agent, action or "(none)")
    return None


_AGENT_NARRATE_LLM_BANNED = (
    "online",
    "is active",
    "heartbeat",
    "standing by",
    "ready to help",
    "i'm here",
    "i am here",
    "how can i help",
)


def _agent_narrate_required_tokens(payload: dict[str, Any]) -> list[str]:
    """Facts the optional LLM polish must preserve."""
    p = payload or {}
    tokens: list[str] = []
    for key in (
        "symbol",
        "from_strategy",
        "to_strategy",
        "old_strategy",
        "new_strategy",
        "regime",
        "new_regime",
    ):
        raw = p.get(key)
        if raw is None:
            continue
        s = str(raw).strip()
        if s and s != "?":
            tokens.append(s)
    if p.get("bots_paused") is not None:
        tokens.append(str(p["bots_paused"]))
    return tokens


def _agent_narrate_facts_blob(event_type: str, payload: dict[str, Any], template: str) -> str:
    """Compact JSON facts for the polish prompt (kept short for narrator models)."""
    import json

    p = payload or {}
    facts: dict[str, Any] = {
        "agent": event_type,
        "action": p.get("action"),
        "symbol": p.get("symbol"),
        "bot_id": p.get("bot_id"),
        "template": template,
    }
    for key in (
        "from_strategy",
        "to_strategy",
        "old_strategy",
        "new_strategy",
        "regime",
        "new_regime",
        "reason",
        "reasons",
        "bots_paused",
        "auto_paused",
        "auto_retrained",
        "current_drawdown",
        "why",
        "confidence",
        "streak",
        "max_streak",
    ):
        if key in p and p[key] is not None:
            facts[key] = p[key]
    try:
        return json.dumps(facts, default=str)[:1800]
    except Exception:
        return template


def _llm_polish_keeps_facts(polished: str, required: list[str]) -> bool:
    text = (polished or "").strip()
    if not text or len(text) > 320:
        return False
    low = text.lower()
    if any(b in low for b in _AGENT_NARRATE_LLM_BANNED):
        return False
    upper = text.upper()
    for tok in required:
        t = str(tok).strip()
        if not t:
            continue
        if t.upper() not in upper:
            return False
    return True


async def _maybe_llm_polish_agent_notice(
    event_type: str,
    payload: dict[str, Any],
    template: str,
) -> tuple[str, str, dict[str, Any]]:
    """Optional LLM polish for real rotate/pause/decay notices.

    Gated by TRADE_COPILOT_USE_LLM and an online LLM provider. On any failure or
    fact-drop, returns the deterministic template unchanged.
    """
    meta: dict[str, Any] = {}
    if not TRADE_COPILOT_USE_LLM:
        return template, "template", meta

    try:
        from app.services.agent.llm.router import get_llm_status

        status = await get_llm_status()
        if not status.get("available"):
            return template, "template", meta

        required = _agent_narrate_required_tokens(payload)
        facts = _agent_narrate_facts_blob(event_type, payload, template)
        user = (
            "Rewrite this trading-agent action notice in ONE short sentence (max 40 words).\n"
            "Preserve every concrete fact: symbols, strategy names, regime, counts, reasons.\n"
            "Do not invent status, online, heartbeat, or help-desk claims.\n"
            "Do not add advice unrelated to the action.\n\n"
            f"FACTS_JSON:\n{facts}\n\n"
            f"TEMPLATE:\n{template}"
        )
        result = await _chat(
            user=user,
            system=(
                "You are TRADE_COPILOT. Narrate a real agent action that already happened. "
                "Return only the rewritten notice — no quotes, no preamble."
            ),
            task="narrator",
            max_tokens=120,
            temperature=0.2,
        )
        if not result or result.provider == "off":
            return template, "template", meta
        polished = (result.text or "").strip().strip('"').strip("'")
        if not _llm_polish_keeps_facts(polished, required):
            logger.debug(
                "agent narrate LLM polish rejected (facts/banlist) for %s",
                event_type,
            )
            return template, "template", meta

        meta = {
            "provider": result.provider,
            "model": result.model,
        }
        return polished, "llm", meta
    except Exception as exc:
        logger.debug("agent narrate LLM polish skipped: %s", exc)
        return template, "template", meta


async def _broadcast_copilot_agent_message(message: dict[str, Any], session_id: str) -> None:
    """Push agent chat line to connected WS clients (works without Redis)."""
    payload = {
        "type": "copilot_agent_message",
        "data": {
            "session_id": session_id,
            "message": message,
        },
    }
    try:
        from app.server import state

        manager = getattr(state, "manager", None)
        if manager is not None and hasattr(manager, "broadcast"):
            await manager.broadcast(payload)
            return

        event_bus = getattr(state, "event_bus", None)
        if event_bus is not None:
            from app.services.events import channels

            await event_bus.publish(channels.WS_BROADCAST, payload)
    except Exception as exc:
        logger.debug("copilot agent broadcast skipped: %s", exc)


async def agent_narrate_event(event_type: str, payload: dict | None = None) -> None:
    """Push a concise agent action notice into Copilot (deduped; template-first).

    Only known rotate / pause / decay / deploy actions get a template. When
    TRADE_COPILOT_USE_LLM is on and an LLM provider is online, the template may
    be lightly polished — never invent heartbeats; fall back to template on fail.
    """
    if not TRADE_COPILOT_ENABLED:
        return

    data = payload if isinstance(payload, dict) else {}
    template = _template_agent_narration(event_type, data)
    if not template:
        return

    fingerprint = _agent_event_fingerprint(event_type, data)
    if not _agent_narrate_allowed(fingerprint):
        logger.debug("Suppressing duplicate agent narrate: %s", fingerprint)
        return

    try:
        text, narration_source, llm_meta = await _maybe_llm_polish_agent_notice(
            event_type, data, template
        )

        session_id = copilot_store.ensure_session_id("default")
        msg_payload = {
            "source_agent": event_type,
            "symbol": data.get("symbol"),
            "bot_id": data.get("bot_id"),
            "action": data.get("action"),
            "fingerprint": fingerprint,
            "narration_source": narration_source,
            "template": template if narration_source == "llm" else None,
            **({k: v for k, v in llm_meta.items() if v}),
        }
        stored = copilot_store.append_message(
            session_id=session_id,
            role="assistant",
            content=text,
            intent="agent_event",
            payload=msg_payload,
        )

        await _broadcast_copilot_agent_message(
            {
                "id": stored.get("id") or str(uuid.uuid4()),
                "role": "assistant",
                "content": text,
                "source_agent": event_type,
                "symbol": data.get("symbol"),
                "bot_id": data.get("bot_id"),
                "action": data.get("action"),
                "fingerprint": fingerprint,
                "narration_source": narration_source,
                "timestamp": time.time(),
                "payload": stored.get("payload") or msg_payload,
            },
            session_id,
        )
    except Exception as exc:
        logger.error("Failed to narrate agent event: %s", exc)


async def handle_message(
    state: Any,
    message: str,
    *,
    session_id: str | None = None,
    active_symbol: str | None = None,
) -> CopilotResult:
    if not TRADE_COPILOT_ENABLED:
        return CopilotResult(ok=False, error="Trade Copilot disabled (TRADE_COPILOT_ENABLED=false)")

    sid = copilot_store.ensure_session_id(session_id)
    text = (message or "").strip()
    if not text:
        return CopilotResult(ok=False, session_id=sid, error="Empty message")

    # Confirm shortcut from chat
    if text.lower() in ("confirm", "yes", "do it", "proceed", "approve"):
        return CopilotResult(
            ok=False,
            session_id=sid,
            error="Use the Confirm button or POST /api/v1/copilot/confirm with pending_id.",
        )

    bot_manager = getattr(state, "bot_manager", None)
    oms = getattr(state, "oms", None)
    tool_results: list[dict[str, Any]] = []
    pending_action: dict[str, Any] | None = None
    pending_id: str | None = None
    intent = INTENT_HELP
    used_agent = False

    # LLM tool planner first — respects natural language (timeframe, follow-ups).
    direct_reply = None
    if TRADE_COPILOT_USE_LLM:
        try:
            from app.services.agent import copilot_agent

            mem = _SESSION_MEMORY.get(sid) or {}
            turn_history = []
            MAX_TURNS = 3
            
            for _ in range(MAX_TURNS):
                plan = await copilot_agent.plan_tool_calls(
                    message=text,
                    session_id=sid,
                    active_symbol=active_symbol,
                    session_memory=mem,
                    turn_history=turn_history,
                )
                if plan is None:
                    break

                new_results, pending_act, intent_new = await copilot_agent.execute_planned_calls(
                    state,
                    plan,
                    session_id=sid,
                    message=text,
                    active_symbol=active_symbol,
                )
                
                if new_results:
                    turn_history.extend(new_results)
                    tool_results.extend(new_results)
                    used_agent = True
                
                if plan.get("direct_reply") and not plan.get("tool_calls"):
                    direct_reply = plan.get("direct_reply")
                    used_agent = True
                    intent = intent_new
                    if pending_act:
                        pending_action = pending_act
                    break
                
                if pending_act:
                    pending_action = pending_act
                    used_agent = True
                    intent = intent_new
                    break
                
                if not plan.get("tool_calls"):
                    break

            if used_agent:
                # Never leave a market question on the help menu if the planner misfired.
                only_help = (
                    not pending_action
                    and tool_results
                    and all(tr.get("tool") in ("help", "clarify") for tr in tool_results)
                )
                if only_help and looks_like_market_question(text) and not looks_like_explicit_help(text):
                    used_agent = False
                    tool_results = []
                    pending_action = None
                    intent = INTENT_HELP
                    direct_reply = None
        except Exception:
            logger.exception("copilot agent planner failed; falling back to rules")

    if not used_agent:
        intent, tool_hint = classify_intent(text)
        try:
            if tool_hint == "help":
                tool_results.append({"tool": "help", "result": _help_text()})

            elif tool_hint == "clarify":
                tool_results.append({
                    "tool": "clarify",
                    "result": _clarify_text(normalize_symbol(active_symbol)),
                })

            elif tool_hint == "get_portfolio_status":
                tool_results.append({"tool": "get_portfolio_status", "result": _tool_portfolio(oms)})

            elif tool_hint == "list_bots":
                tool_results.append({"tool": "list_bots", "result": _tool_list_bots(bot_manager)})

            elif tool_hint == "get_bot_performance":
                bid = None
                m = _BOT_ID_RE.search(text)
                if m:
                    bid = m.group(1)
                tool_results.append({
                    "tool": "get_bot_performance",
                    "result": _tool_bot_performance(bot_manager, bid),
                })

            elif tool_hint == "analyze_symbol":
                from app.services.agent.copilot_agent import extract_timeframe_hint

                sym = normalize_symbol(extract_symbol(text, active_symbol))
                tf = (
                    extract_timeframe_hint(text)
                    or get_preferred_timeframe(sid)
                    or _DEFAULT_ANALYZE_TF
                )
                remember_timeframe(sid, tf)
                if not sym:
                    last = get_last_insight(sid)
                    sym = normalize_symbol((last or {}).get("symbol")) if last else None
                if not sym:
                    tool_results.append({"tool": "analyze_symbol", "result": {"error": "Specify a symbol"}})
                else:
                    analysis = await _tool_analyze(state, sym, timeframe=tf)
                    remember_insight(sid, analysis)
                    tool_results.append({"tool": "analyze_symbol", "result": analysis})

            elif tool_hint == "scan_market":
                tool_results.append({
                    "tool": "scan_market",
                    "result": await _tool_scan_market(bot_manager, limit=5)
                })

            elif tool_hint == "meta_insight":
                tool_results.append({
                    "tool": "meta_insight",
                    "result": _tool_meta_insight(sid, text, active_symbol=active_symbol),
                })

            elif tool_hint == "run_backtest":
                from app.services.agent.copilot_agent import extract_timeframe_hint

                sym = normalize_symbol(extract_symbol(text, active_symbol))
                if not sym:
                    tool_results.append({
                        "tool": "run_backtest",
                        "result": {"error": "Specify a symbol (e.g. BTC or BTCUSDT)"},
                    })
                else:
                    strategy = extract_strategy(text)
                    days = extract_days(text, default=30)
                    alloc = extract_allocation(text, default=1000.0)
                    tf = extract_timeframe_hint(text) or get_preferred_timeframe(sid)
                    tool_results.append({
                        "tool": "run_backtest",
                        "result": await _tool_run_backtest(
                            state,
                            sym,
                            strategy,
                            days,
                            timeframe=tf,
                            allocation=alloc,
                        ),
                    })

            elif tool_hint == "get_sentiment":
                sym = normalize_symbol(extract_symbol(text, active_symbol)) or "AAPL"
                tool_results.append({"tool": "get_sentiment", "result": _tool_sentiment(sym)})

            elif tool_hint in ("recommend_strategy", "strategy_hint"):
                rec = await _tool_recommend_strategy(
                    state, text, active_symbol=active_symbol
                )
                if isinstance(rec.get("_insight"), dict):
                    remember_insight(sid, rec.pop("_insight"))
                else:
                    rec.pop("_insight", None)
                tool_results.append({"tool": "recommend_strategy", "result": rec})

            elif tool_hint == "explain_trade":
                bid = None
                m = _BOT_ID_RE.search(text)
                if m:
                    bid = m.group(1)
                elif bot_manager and bot_manager.active_bots:
                    sym = extract_symbol(text, active_symbol)
                    for b in bot_manager.active_bots.values():
                        if sym and str(b.get("symbol") or "").upper() == sym:
                            bid = b.get("id")
                            break
                    if not bid:
                        bid = next(iter(bot_manager.active_bots))
                tool_results.append({
                    "tool": "explain_trade",
                    "result": await _tool_explain(state, bid or ""),
                })

            elif tool_hint == "deploy_bot":
                from app.services.agent.copilot_agent import extract_timeframe_hint

                sym = normalize_symbol(extract_symbol(text, active_symbol))
                if not sym:
                    tool_results.append({"tool": "deploy_bot", "result": {"error": "Specify a symbol to deploy"}})
                else:
                    tf = extract_timeframe_hint(text) or get_preferred_timeframe(sid)
                    pending_action = {
                        "type": "deploy_bot",
                        "params": {
                            "strategy": extract_strategy(text) or "CHART_AGENT",
                            "symbol": sym,
                            "timeframe": tf,
                            "allocation": extract_allocation(text),
                            "config": {"pipeline_source": "copilot"},
                        },
                    }

            elif tool_hint == "pause_all_bots":
                pending_action = {"type": "pause_all_bots", "params": {}}

            elif tool_hint == "stop_all_bots":
                pending_action = {"type": "stop_all_bots", "params": {}}

            elif tool_hint in ("pause_bot", "stop_bot"):
                m = _BOT_ID_RE.search(text)
                bid = m.group(1) if m else None
                if not bid and bot_manager:
                    sym = extract_symbol(text, active_symbol)
                    for b in bot_manager.active_bots.values():
                        if sym and str(b.get("symbol") or "").upper() == sym:
                            bid = b.get("id")
                            break
                if not bid:
                    tool_results.append({
                        "tool": tool_hint,
                        "result": {"error": "Specify a bot id or symbol"},
                    })
                else:
                    pending_action = {
                        "type": tool_hint,
                        "params": {"bot_id": bid},
                    }

            elif tool_hint == "update_bot_config":
                m = _BOT_ID_RE.search(text)
                bid = m.group(1) if m else None
                pct_m = _PCT_RE.search(text)
                patch: dict[str, Any] = {}
                if pct_m and ("stop" in text.lower() or "sl" in text.lower()):
                    patch["stop_loss_percent"] = float(pct_m.group(1))
                if "confidence" in text.lower() and pct_m:
                    val = float(pct_m.group(1))
                    patch["min_confidence"] = val / 100.0 if val > 1 else val
                if not bid and bot_manager:
                    sym = extract_symbol(text, active_symbol)
                    for b in bot_manager.active_bots.values():
                        if sym and str(b.get("symbol") or "").upper() == sym:
                            bid = b.get("id")
                            break
                if not bid or not patch:
                    tool_results.append({
                        "tool": "update_bot_config",
                        "result": {"error": "Need bot id/symbol and a config change (e.g. stop 1.5%)"},
                    })
                else:
                    pending_action = {
                        "type": "update_bot_config",
                        "params": {"bot_id": bid, "config_patch": patch},
                    }

            else:
                tool_results.append({
                    "tool": "clarify",
                    "result": _clarify_text(normalize_symbol(active_symbol)),
                })

        except Exception as exc:
            logger.exception("copilot tool error")
            return CopilotResult(ok=False, session_id=sid, intent=intent, error=str(exc))

    if pending_action:
        pending_id = _store_pending(pending_action)

    reply = ""
    if direct_reply:
        reply = direct_reply
        template = _template_reply(intent, tool_results, pending_action, user_message=text, session_id=sid)
        if template and template != "Done.":
            reply += "\n\n" + template
    else:
        reply = await _narrate(
            text, intent, tool_results, pending_action, session_id=sid
        )

    try:
        copilot_store.append_message(sid, "user", text, intent=intent)
        copilot_store.append_message(
            sid,
            "assistant",
            reply,
            intent=intent,
            payload={
                "tool_results": tool_results,
                "pending_id": pending_id,
                "pending_action": pending_action,
                "agent": used_agent,
            },
        )
    except Exception as exc:
        logger.debug("copilot persist skipped: %s", exc)

    return CopilotResult(
        ok=True,
        session_id=sid,
        intent=intent,
        reply=reply,
        tool_results=tool_results,
        requires_confirmation=bool(pending_action),
        pending_id=pending_id,
        pending_action=pending_action,
    )


async def confirm_action(state: Any, pending_id: str) -> dict[str, Any]:
    action = pop_pending(pending_id)
    if not action:
        return {"ok": False, "error": "Pending action expired or not found"}

    bot_manager = getattr(state, "bot_manager", None)
    if bot_manager is None:
        return {"ok": False, "error": "Bot manager unavailable"}

    atype = action.get("type")
    params = action.get("params") or {}

    try:
        if atype == "deploy_bot":
            bot_id = await bot_manager.create_bot(
                params.get("strategy") or "CHART_AGENT",
                params["symbol"],
                params.get("timeframe") or "1m",
                float(params.get("allocation") or 1000),
                params.get("config") or {"pipeline_source": "copilot"},
            )
            return {"ok": True, "result": {"bot_id": bot_id, "action": atype}}

        if atype == "pause_bot":
            await bot_manager.pause_bot(params["bot_id"])
            return {"ok": True, "result": {"bot_id": params["bot_id"], "action": atype}}

        if atype == "stop_bot":
            await bot_manager.stop_bot(params["bot_id"])
            return {"ok": True, "result": {"bot_id": params["bot_id"], "action": atype}}

        if atype == "pause_all_bots":
            paused = 0
            for bot_id, bot in list(bot_manager.active_bots.items()):
                if bot.get("status") == "RUNNING":
                    await bot_manager.pause_bot(bot_id)
                    paused += 1
            return {"ok": True, "result": {"paused": paused, "action": atype}}

        if atype == "stop_all_bots":
            await bot_manager.stop_all_bots()
            return {"ok": True, "result": {"action": atype}}

        if atype == "update_bot_config":
            detail = await bot_manager.update_bot_config(
                params["bot_id"],
                params.get("config_patch") or {},
            )
            return {"ok": True, "result": {"action": atype, "detail": bool(detail)}}

        return {"ok": False, "error": f"Unknown action type: {atype}"}
    except Exception as exc:
        logger.exception("copilot confirm failed")
        return {"ok": False, "error": str(exc)}


def cancel_action(pending_id: str) -> dict[str, Any]:
    action = pop_pending(pending_id)
    if not action:
        return {"ok": False, "error": "Pending action expired or not found"}
    return {"ok": True, "cancelled": action}
