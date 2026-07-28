"""LLM Bull/Bear/Judge debate with deterministic firewall.

Phase 3.9 of the Signal Enhancement Plan.

A multi-agent debate where two LLM advocates (Bull and Bear) argue opposing
sides of a trade, and a Judge LLM weighs both arguments to produce a final
verdict. A **deterministic firewall** sits between the Judge and the live
signal path: it can veto the LLM's verdict if it violates hard risk constraints,
but the LLM can never override the firewall.

Flow:

1. **Firewall pre-check** — deterministic rules veto the debate entirely when
   the base signal is already NONE, risk gates have blocked the entry, or the
   LLM is unavailable. The debate never runs on a signal that already failed
   deterministic gates.

2. **Bull advocate** — LLM argues for the LONG case given the insight snapshot.

3. **Bear advocate** — LLM argues for the SHORT case given the insight snapshot.

4. **Judge** — LLM weighs both arguments and returns a verdict: BUY / SELL / NONE
   + confidence + reasoning.

5. **Firewall post-check** — deterministic rules constrain the Judge's verdict:
   - Can downgrade BUY/SELL → NONE (never upgrade NONE → action).
   - Can reduce confidence (never increase it).
   - Can veto if the verdict disagrees with the base signal direction.
   - Can veto if confidence is below a floor.

The firewall guarantees the LLM is an *advisory* layer — it can only narrow
the signal, never widen it. Opt-in via ``llm_debate_enabled``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

MIN_LLM_CONFIDENCE_FLOOR = 0.50
MAX_DEBATE_TOKENS = 200


@dataclass
class DebateVerdict:
    signal: str          # BUY | SELL | NONE
    confidence: float
    reasoning: str
    bull_argument: str
    bear_argument: str
    judge_argument: str
    firewall_vetoed: bool = False
    firewall_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "signal": self.signal,
            "confidence": round(self.confidence, 4),
            "reasoning": self.reasoning,
            "bull_argument": self.bull_argument[:500],
            "bear_argument": self.bear_argument[:500],
            "judge_argument": self.judge_argument[:500],
            "firewall_vetoed": self.firewall_vetoed,
            "firewall_reason": self.firewall_reason,
        }


# ── Deterministic firewall ────────────────────────────────────────────────


def firewall_precheck(
    base_signal: str,
    *,
    llm_available: bool,
    config: dict | None = None,
) -> str | None:
    """Veto the debate before it starts. Returns a reason string or None.

    The debate only runs when:
    - The base signal is actionable (BUY/SELL)
    - The LLM is available
    - The debate is enabled in config
    """
    cfg = config or {}
    if not cfg.get("llm_debate_enabled"):
        return "debate disabled"
    if not llm_available:
        return "llm unavailable"
    sig = str(base_signal or "NONE").upper()
    if sig not in ("BUY", "SELL"):
        return f"base signal {sig} not actionable"
    return None


def firewall_postcheck(
    judge_signal: str,
    judge_confidence: float,
    *,
    base_signal: str,
    config: dict | None = None,
) -> DebateVerdict | None:
    """Constrain the Judge's verdict. Returns a vetoed verdict or None.

    The firewall can only NARROW the signal:
    - Disagreement with base direction → veto to NONE
    - Confidence below floor → veto to NONE
    - Judge says NONE → NONE (never upgrade)
    """
    cfg = config or {}
    base = str(base_signal or "NONE").upper()
    judge = str(judge_signal or "NONE").upper()
    conf = float(judge_confidence or 0.0)
    floor = float(cfg.get("llm_debate_min_confidence", MIN_LLM_CONFIDENCE_FLOOR))

    if judge == "NONE":
        return None  # accept the downgrade

    if judge != base:
        return DebateVerdict(
            signal="NONE", confidence=0.0,
            reasoning=f"firewall: judge {judge} disagrees with base {base}",
            bull_argument="", bear_argument="", judge_argument="",
            firewall_vetoed=True,
            firewall_reason=f"direction mismatch: judge={judge} base={base}",
        )

    if conf < floor:
        return DebateVerdict(
            signal="NONE", confidence=conf,
            reasoning=f"firewall: judge confidence {conf:.2%} < floor {floor:.2%}",
            bull_argument="", bear_argument="", judge_argument="",
            firewall_vetoed=True,
            firewall_reason=f"confidence below floor: {conf:.2%} < {floor:.2%}",
        )

    return None  # no veto — accept the judge


# ── Prompt builders ───────────────────────────────────────────────────────


def _insight_summary(insight: dict) -> str:
    """Compress an insight snapshot into a compact text summary for the LLM."""
    if not isinstance(insight, dict):
        return "no insight data"
    score = insight.get("score", 0)
    conf = insight.get("confidence", 0)
    sub = insight.get("sub_reports") or {}
    parts = [f"score={score}", f"confidence={conf:.2%}"]
    for name in ("trend", "momentum", "volume", "risk", "sentiment"):
        block = sub.get(name) or {}
        s = block.get("score")
        if s is not None:
            parts.append(f"{name}_score={s}")
    reasons = insight.get("reasons") or []
    if reasons:
        parts.append("reasons=" + "; ".join(str(r) for r in reasons[:3]))
    return " ".join(parts)


_BULL_SYSTEM = (
    "You are a Bull advocate for a trade. Given the market insight, argue the "
    "strongest case for a LONG (BUY) position. Be concise (3-4 sentences). "
    "Focus on momentum, trend, and volume evidence. Do not hedge."
)

_BEAR_SYSTEM = (
    "You are a Bear advocate for a trade. Given the market insight, argue the "
    "strongest case for a SHORT (SELL) position. Be concise (3-4 sentences). "
    "Focus on risk, overextension, and counter-signals. Do not hedge."
)

_JUDGE_SYSTEM = (
    "You are a trading Judge. Weigh the Bull and Bear arguments against the "
    "market insight. Return JSON: {\"signal\": \"BUY\"|\"SELL\"|\"NONE\", "
    "\"confidence\": 0.0-1.0, \"reasoning\": \"one sentence\"}. "
    "Only choose BUY or SELL if the winning argument is materially stronger. "
    "When in doubt, choose NONE."
)


# ── Debate runner ─────────────────────────────────────────────────────────


async def run_debate(
    base_signal: str,
    insight: dict,
    *,
    config: dict | None = None,
) -> DebateVerdict | None:
    """Run the full Bull/Bear/Judge debate with deterministic firewall.

    Returns None when the firewall pre-check vetoes (debate shouldn't run).
    Returns a DebateVerdict otherwise — possibly vetoed by the post-check.
    """
    from app.services.agent.llm.router import _chat, is_llm_available

    cfg = config or {}
    llm_ok = await is_llm_available()
    pre = firewall_precheck(base_signal, llm_available=llm_ok, config=cfg)
    if pre:
        logger.debug("Debate pre-check vetoed: %s", pre)
        return None

    summary = _insight_summary(insight)
    base = str(base_signal).upper()

    # Run bull + bear in parallel
    import asyncio
    bull_task = _chat(
        system=_BULL_SYSTEM, user=f"Insight: {summary}\nBase signal: {base}",
        task="narrator", max_tokens=MAX_DEBATE_TOKENS, temperature=0.4,
    )
    bear_task = _chat(
        system=_BEAR_SYSTEM, user=f"Insight: {summary}\nBase signal: {base}",
        task="narrator", max_tokens=MAX_DEBATE_TOKENS, temperature=0.4,
    )
    bull_res, bear_res = await asyncio.gather(bull_task, bear_task, return_exceptions=True)
    bull_arg = bull_res.text if (bull_res and not isinstance(bull_res, Exception) and bull_res.text) else ""
    bear_arg = bear_res.text if (bear_res and not isinstance(bear_res, Exception) and bear_res.text) else ""

    if not bull_arg or not bear_arg:
        logger.debug("Debate skipped — advocate generation failed")
        return None

    # Judge
    judge_user = (
        f"Insight: {summary}\nBase signal: {base}\n\n"
        f"Bull argument: {bull_arg}\n\n"
        f"Bear argument: {bear_arg}\n\n"
        f"Return JSON verdict."
    )
    judge_res = await _chat(
        system=_JUDGE_SYSTEM, user=judge_user,
        task="deep", max_tokens=150, temperature=0.2, json_mode=True,
    )
    judge_text = judge_res.text if (judge_res and judge_res.text) else ""

    # Parse judge verdict
    judge_signal = "NONE"
    judge_conf = 0.0
    judge_reason = ""
    try:
        from app.services.agent.llm.base import parse_json_object
        parsed = parse_json_object(judge_text)
        if isinstance(parsed, dict):
            judge_signal = str(parsed.get("signal") or "NONE").upper()
            judge_conf = float(parsed.get("confidence") or 0.0)
            judge_reason = str(parsed.get("reasoning") or "")
    except Exception:
        # Fallback: try raw parse
        try:
            parsed = json.loads(judge_text)
            judge_signal = str(parsed.get("signal") or "NONE").upper()
            judge_conf = float(parsed.get("confidence") or 0.0)
            judge_reason = str(parsed.get("reasoning") or "")
        except Exception:
            pass

    if judge_signal not in ("BUY", "SELL", "NONE"):
        judge_signal = "NONE"
    judge_conf = max(0.0, min(1.0, judge_conf))

    # Firewall post-check
    veto = firewall_postcheck(
        judge_signal, judge_conf, base_signal=base, config=cfg,
    )
    if veto:
        veto.bull_argument = bull_arg
        veto.bear_argument = bear_arg
        veto.judge_argument = judge_reason
        return veto

    return DebateVerdict(
        signal=judge_signal,
        confidence=judge_conf,
        reasoning=judge_reason,
        bull_argument=bull_arg,
        bear_argument=bear_arg,
        judge_argument=judge_reason,
    )
