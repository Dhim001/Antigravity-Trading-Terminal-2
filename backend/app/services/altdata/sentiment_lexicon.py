"""Lexicon-based sentiment scoring for headlines and social snippets."""

from __future__ import annotations

import re

_BULLISH = frozenset({
    "surge", "rally", "gain", "gains", "beat", "beats", "upgrade", "upgraded",
    "bullish", "soar", "soars", "jump", "jumps", "record", "breakout", "outperform",
    "growth", "strong", "positive", "buy", "accumulate", "momentum", "recovery",
    "rebound", "approval", "partnership", "launch", "innovation", "profit", "profits",
})

_BEARISH = frozenset({
    "plunge", "drop", "drops", "fall", "falls", "miss", "misses", "downgrade",
    "downgraded", "bearish", "crash", "crashes", "sink", "sinks", "lawsuit",
    "investigation", "recall", "weak", "negative", "sell", "cut", "cuts", "loss",
    "losses", "decline", "declines", "warning", "bankruptcy", "fraud", "layoff",
    "layoffs", "halt", "halted", "subpoena",
})

_TOKEN_RE = re.compile(r"[a-z']+")


def score_text_sentiment(text: str) -> float:
    """Return sentiment in [-1, 1] from keyword counts."""
    if not text or not str(text).strip():
        return 0.0
    tokens = _TOKEN_RE.findall(str(text).lower())
    if not tokens:
        return 0.0
    bull = sum(1 for t in tokens if t in _BULLISH)
    bear = sum(1 for t in tokens if t in _BEARISH)
    total = bull + bear
    if total == 0:
        return 0.0
    raw = (bull - bear) / total
    return round(max(-1.0, min(1.0, raw)), 4)
