"""LLM provider protocol — narrators must not alter trading signals."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

SYSTEM_PROMPT = (
    "You are a concise trading chart analyst. Summarize ONLY the JSON insight provided. "
    "Do not invent prices, indicators, or signals. Do not change the signal field. "
    "When sub_reports are present, mention trend/momentum/risk briefly. "
    "Keep the response under 3 sentences. "
    "Output ONLY the final summary — no commands, code, questions, headings, "
    "chain-of-thought, or references to the prompt or JSON structure."
)

CRITIQUE_SYSTEM_PROMPT = (
    "You are a trading analyst reviewing structured chart sub-reports. "
    "Respond with valid JSON only, containing exactly these keys: "
    "reasoning_summary (string, 2-3 sentences), risk_notes (string, 1-2 sentences). "
    "Do NOT change, override, or suggest changing the signal field. "
    "Base your answer only on the provided insight JSON. "
    "Put conclusions in the JSON values only — no chain-of-thought outside JSON."
)

BACKTEST_TRADE_SYSTEM_PROMPT = (
    "You explain ONE specific backtest entry fill. Use ONLY the JSON provided. "
    "Do not invent indicators, signals, or market context not in the JSON. "
    "Do not repeat generic strategy boilerplate across trades. "
    "In 1-2 sentences: why this particular entry (side, price, time, reason) "
    "occurred in the context given. Mention run scope when present (single, sweep best, walk-forward OOS). "
    "If bar_time is present, reference the timing briefly. "
    "Output ONLY the final explanation text — no commands, code, questions, headings, "
    "chain-of-thought, or references to the prompt or JSON structure."
)

_THINKING_BLOCK_RE = re.compile(
    r"<\s*think(?:ing)?\s*>.*?<\s*/\s*think(?:ing)?\s*>",
    re.IGNORECASE | re.DOTALL,
)
_REASONING_TAG_RE = re.compile(
    r"<\s*reasoning\s*>.*?<\s*/\s*reasoning\s*>",
    re.IGNORECASE | re.DOTALL,
)
_CODE_FENCE_RE = re.compile(r"```[\w-]*\s*[\s\S]*?```", re.MULTILINE)
_FINAL_LABEL_RE = re.compile(
    r"(?:^|\n)\s*(?:final(?:\s+answer)?|summary|conclusion|answer|response|explanation)\s*[:\-]\s*",
    re.IGNORECASE,
)
_PROMPT_ECHO_RE = re.compile(
    r"(?:Explain this single backtest entry fill|Summarize this chart analyst insight)"
    r"[^\n.!?]{0,80}[:\s]*",
    re.IGNORECASE,
)
_COMMAND_INLINE_RE = re.compile(
    r"\b(?:ollama\s+run|python3?\s+-m|curl\s+-[A-Za-z]|npm\s+run|pip\s+install|bash\s+-c|docker\s+run|uv\s+run)\b[^\n.!?]{0,160}",
    re.IGNORECASE,
)
_META_QUESTION_RE = re.compile(
    r"(?:would you like|do you want|shall i|can i help|should i|let me know if)[^?.!?\n]{0,120}\?",
    re.IGNORECASE,
)
_JUNK_LINE_RES = (
    re.compile(r"^\s*(?:```|~~~)", re.I),
    re.compile(r"^\s*\$\s+\S"),
    re.compile(r"^\s*(?:ollama|python3?|curl|npm|pip|bash|sh|cmd|powershell)\s+", re.I),
    re.compile(r"^\s*(?:run|execute|try)\s*(?:command|this)?\s*[:`]?\s*(?:ollama|python|curl)", re.I),
    re.compile(r"^\s*run\s*:\s*", re.I),
    re.compile(
        r"^\s*(?:explain this single backtest|summarize this chart|here(?:'s| is) the json|"
        r"the user (?:wants|asked|provided|query)|based on the (?:json|prompt|provided)|"
        r"looking at the json|from the json|step \d+)",
        re.I,
    ),
    re.compile(
        r"^\s*(?:would you like|do you want|shall i|can i help|let me know if|anything else|"
        r"need any (?:more|further))",
        re.I,
    ),
    re.compile(r"^\s*(?:question|prompt|instruction)s?\s*[:\?]", re.I),
    re.compile(r"^\s*(?:analysis|reasoning|thinking)\s*(?:process|steps?)?\s*:", re.I),
    re.compile(r"^\s*#{1,6}\s*(?:analysis|reasoning|thinking|step)", re.I),
    re.compile(r"^\s*(?:thought process|internal monologue|scratchpad|my reasoning)\s*:", re.I),
    re.compile(r"^\s*\[\d+\]\s*(?:analyze|check|review|first|step)", re.I),
    re.compile(r"^\s*>\s+"),
)
_COT_OPENERS = (
    "let me",
    "i need to",
    "first,",
    "step 1",
    "thinking:",
    "the user wants",
    "i'll analyze",
    "i will analyze",
    "okay,",
    "alright,",
    "we are given",
    "we need to",
    "looking at the json",
    "from the json",
)
_META_QUESTION_MARKERS = (
    "would you",
    "do you",
    "shall i",
    "can i help",
    "should i",
    "want me to",
    "like me to",
    "need any",
    "anything else",
    "more details",
    "clarify",
    "confirm if",
)
_TRADE_NARRATIVE_HINTS = (
    "buy",
    "sell",
    "entry",
    "fill",
    "bar",
    "momentum",
    "rsi",
    "signal",
    "breakout",
    "cross",
    "long",
    "short",
    "position",
)


def _looks_like_chain_of_thought(text: str) -> bool:
    sample = text[:320].lower()
    return any(marker in sample for marker in _COT_OPENERS)


def _is_meta_question_line(line: str) -> bool:
    if "?" not in line:
        return False
    lower = line.lower()
    return any(marker in lower for marker in _META_QUESTION_MARKERS)


def _filter_junk_lines(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(pattern.search(stripped) for pattern in _JUNK_LINE_RES):
            continue
        if _is_meta_question_line(stripped):
            continue
        lines.append(stripped)
    if len(lines) >= 2:
        non_cot = [ln for ln in lines if not _looks_like_chain_of_thought(ln)]
        if non_cot:
            lines = non_cot
    return "\n".join(lines)


def _is_mostly_json(text: str) -> bool:
    t = text.strip()
    if len(t) < 2:
        return False
    if (t[0] == "{" and t[-1] == "}") or (t[0] == "[" and t[-1] == "]"):
        try:
            json.loads(t)
            return True
        except json.JSONDecodeError:
            return False
    return False


def _score_narrative_block(block: str) -> float:
    lower = block.lower()
    score = 0.0
    if _looks_like_chain_of_thought(block):
        score -= 3.0
    if _is_meta_question_line(block):
        score -= 5.0
    if any(hint in lower for hint in _TRADE_NARRATIVE_HINTS):
        score += 2.0
    if 40 <= len(block) <= 420:
        score += 1.0
    if len(block) > 600:
        score -= 1.5
    return score


def _pick_narrative_block(text: str) -> str:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) <= 1:
        line_blocks = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(line_blocks) >= 2:
            blocks = line_blocks
        elif blocks:
            return blocks[0]
        else:
            return text.strip()
    if len(blocks) == 1:
        return blocks[0]
    best = max(blocks, key=_score_narrative_block)
    if _score_narrative_block(best) >= _score_narrative_block(blocks[-1]):
        return best
    return blocks[-1]


def _extract_outcome_sentences(text: str) -> str:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    if len(sentences) <= 1:
        return text.strip()

    kept: list[str] = []
    for sentence in sentences:
        if _looks_like_chain_of_thought(sentence) and len(kept) < len(sentences) - 1:
            continue
        if _is_meta_question_line(sentence):
            continue
        if _COMMAND_INLINE_RE.search(sentence):
            continue
        kept.append(sentence)

    if not kept:
        return sentences[-1].strip()

    if len(kept) == 1:
        return kept[0]

    best = max(kept, key=_score_narrative_block)
    if _score_narrative_block(best) >= _score_narrative_block(kept[0]) + 0.5:
        return best
    return " ".join(kept[-2:]).strip()


def _trim_to_complete_sentences(text: str, max_len: int = 480) -> str:
    if len(text) <= max_len:
        return text
    clipped = text[:max_len]
    last_stop = max(clipped.rfind("."), clipped.rfind("!"), clipped.rfind("?"))
    if last_stop >= 40:
        return clipped[: last_stop + 1].strip()
    return clipped.strip()


def strip_reasoning_process(text: str | None) -> str | None:
    """
    Remove model chain-of-thought, commands, prompt echoes, and meta questions.
    Prefer the final trader-facing explanation segment.
    """
    if not text or not isinstance(text, str):
        return None

    t = text.strip()
    if not t:
        return None

    t = _THINKING_BLOCK_RE.sub("", t).strip()
    t = _REASONING_TAG_RE.sub("", t).strip()
    t = _CODE_FENCE_RE.sub("", t).strip()

    if re.search(r"<\s*/\s*think(?:ing)?\s*>", t, re.IGNORECASE):
        parts = re.split(r"<\s*/\s*think(?:ing)?\s*>", t, flags=re.IGNORECASE)
        tail = parts[-1].strip()
        if tail:
            t = tail

    label_match = _FINAL_LABEL_RE.search(t)
    if label_match:
        t = t[label_match.end() :].strip()

    t = _filter_junk_lines(t)
    t = _pick_narrative_block(t)
    t = _PROMPT_ECHO_RE.sub("", t).strip()
    t = _COMMAND_INLINE_RE.sub("", t).strip()
    t = _META_QUESTION_RE.sub("", t).strip()

    if _is_mostly_json(t):
        return None

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]
    if len(sentences) > 1 and (
        _looks_like_chain_of_thought(t)
        or any(_looks_like_chain_of_thought(s) for s in sentences[:-1])
    ):
        t = _extract_outcome_sentences(t)

    t = re.sub(r"\s+", " ", t).strip()
    t = _trim_to_complete_sentences(t)
    return t or None


def extract_assistant_text(message: dict | None) -> str | None:
    """
    Normalize OpenAI-style assistant message text for display.
    Prefers content over reasoning fields and strips chain-of-thought scaffolding.
    """
    if not message or not isinstance(message, dict):
        return None

    content = message.get("content")
    if isinstance(content, str) and content.strip():
        cleaned = strip_reasoning_process(content.strip())
        if cleaned:
            return cleaned

    for key in ("reasoning", "thinking", "reasoning_content"):
        val = message.get(key)
        if isinstance(val, str) and val.strip():
            cleaned = strip_reasoning_process(val.strip())
            if cleaned and not _looks_like_chain_of_thought(cleaned):
                return cleaned
            if cleaned and len(cleaned) <= 320:
                return cleaned
            if cleaned:
                sentences = re.split(r"(?<=[.!?])\s+", cleaned)
                tail = " ".join(sentences[-2:]).strip() if len(sentences) >= 2 else cleaned
                return tail[:500].strip() or cleaned[:500].strip()

    return None


def parse_json_object(text: str | None) -> dict | None:
    """Parse JSON object from model text, stripping reasoning wrappers first."""
    cleaned = strip_reasoning_process(text)
    if not cleaned:
        return None
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(cleaned[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None


@dataclass
class LLMResult:
    text: str | None
    model: str | None
    provider: str | None = None
    latency_ms: float | None = None


class LLMProvider(Protocol):
    name: str

    async def is_available(self) -> bool: ...

    async def chat(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 180,
        temperature: float = 0.3,
        json_mode: bool = False,
    ) -> LLMResult: ...

    async def list_models(self) -> list[str]: ...
