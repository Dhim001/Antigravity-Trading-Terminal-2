"""Tests for Phase 3.9 — LLM Bull/Bear/Judge debate + deterministic firewall."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.agent import debate


def _run(coro):
    return asyncio.run(coro)


# --- Firewall pre-check --------------------------------------------------


def test_precheck_vetoes_when_disabled():
    reason = debate.firewall_precheck("BUY", llm_available=True, config={})
    assert reason == "debate disabled"


def test_precheck_vetoes_when_llm_unavailable():
    reason = debate.firewall_precheck(
        "BUY", llm_available=False,
        config={"llm_debate_enabled": True},
    )
    assert reason == "llm unavailable"


def test_precheck_vetoes_non_actionable_signal():
    reason = debate.firewall_precheck(
        "NONE", llm_available=True,
        config={"llm_debate_enabled": True},
    )
    assert "not actionable" in reason


def test_precheck_passes_actionable_signal():
    reason = debate.firewall_precheck(
        "BUY", llm_available=True,
        config={"llm_debate_enabled": True},
    )
    assert reason is None


# --- Firewall post-check -------------------------------------------------


def test_postcheck_accepts_agreement():
    veto = debate.firewall_postcheck(
        "BUY", 0.7, base_signal="BUY",
        config={"llm_debate_min_confidence": 0.5},
    )
    assert veto is None  # no veto


def test_postcheck_vetoes_direction_mismatch():
    veto = debate.firewall_postcheck(
        "SELL", 0.8, base_signal="BUY",
        config={"llm_debate_min_confidence": 0.5},
    )
    assert veto is not None
    assert veto.signal == "NONE"
    assert veto.firewall_vetoed is True
    assert "mismatch" in veto.firewall_reason


def test_postcheck_vetoes_low_confidence():
    veto = debate.firewall_postcheck(
        "BUY", 0.3, base_signal="BUY",
        config={"llm_debate_min_confidence": 0.5},
    )
    assert veto is not None
    assert veto.signal == "NONE"
    assert "floor" in veto.firewall_reason


def test_postcheck_accepts_none_downgrade():
    veto = debate.firewall_postcheck(
        "NONE", 0.0, base_signal="BUY",
        config={"llm_debate_min_confidence": 0.5},
    )
    assert veto is None  # NONE is accepted (downgrade)


def test_postcheck_never_upgrades_none():
    veto = debate.firewall_postcheck(
        "NONE", 0.9, base_signal="BUY",
    )
    assert veto is None  # accepted as NONE, not upgraded to BUY


# --- Insight summary ----------------------------------------------------


def test_insight_summary_basic():
    s = debate._insight_summary({
        "score": 3, "confidence": 0.7,
        "sub_reports": {"trend": {"score": 2}, "momentum": {"score": 1}},
        "reasons": ["bullish RSI", "MACD crossover"],
    })
    assert "score=3" in s
    assert "confidence=70.00%" in s
    assert "trend_score=2" in s
    assert "bullish RSI" in s


def test_insight_summary_empty():
    s = debate._insight_summary({})
    assert "score=0" in s


def test_insight_summary_none():
    assert "no insight" in debate._insight_summary(None)


# --- Debate runner (mocked LLM) -----------------------------------------


def test_run_debate_returns_none_when_disabled():
    result = _run(debate.run_debate(
        "BUY", {"score": 3, "confidence": 0.7},
        config={"llm_debate_enabled": False},
    ))
    assert result is None


def test_run_debate_returns_none_when_llm_unavailable():
    with patch("app.services.agent.llm.router.is_llm_available", new=AsyncMock(return_value=False)):
        result = _run(debate.run_debate(
            "BUY", {"score": 3, "confidence": 0.7},
            config={"llm_debate_enabled": True},
        ))
    assert result is None


def _make_fake_chat(bull_text, bear_text, judge_json):
    async def fake_chat(**kwargs):
        from app.services.agent.llm.base import LLMResult
        system = kwargs.get("system", "")
        if "Bull advocate" in system:
            return LLMResult(text=bull_text, model="test", provider="test")
        if "Bear advocate" in system:
            return LLMResult(text=bear_text, model="test", provider="test")
        if "Judge" in system:
            return LLMResult(text=judge_json, model="test", provider="test")
        return LLMResult(text=None, model=None, provider="off")
    return fake_chat


def test_run_debate_full_flow_promotes():
    """Judge agrees with base BUY → verdict returned, no firewall veto."""
    bull_text = "Strong momentum and volume support a long position."
    bear_text = "RSI overbought, risk of pullback."
    judge_json = json.dumps({
        "signal": "BUY", "confidence": 0.75,
        "reasoning": "Bull argument is stronger on momentum.",
    })

    with patch("app.services.agent.llm.router.is_llm_available", new=AsyncMock(return_value=True)), \
         patch("app.services.agent.llm.router._chat", new=AsyncMock(side_effect=_make_fake_chat(bull_text, bear_text, judge_json))):
        result = _run(debate.run_debate(
            "BUY", {"score": 3, "confidence": 0.7},
            config={"llm_debate_enabled": True, "llm_debate_min_confidence": 0.5},
        ))
    assert result is not None
    assert result.signal == "BUY"
    assert result.confidence == pytest.approx(0.75)
    assert result.firewall_vetoed is False
    assert bull_text in result.bull_argument
    assert bear_text in result.bear_argument


def test_run_debate_firewall_vetoes_disagreement():
    """Judge says SELL but base is BUY → firewall vetoes to NONE."""
    judge_json = json.dumps({
        "signal": "SELL", "confidence": 0.8,
        "reasoning": "Bear argument stronger.",
    })

    with patch("app.services.agent.llm.router.is_llm_available", new=AsyncMock(return_value=True)), \
         patch("app.services.agent.llm.router._chat", new=AsyncMock(side_effect=_make_fake_chat("arg", "arg", judge_json))):
        result = _run(debate.run_debate(
            "BUY", {"score": 3, "confidence": 0.7},
            config={"llm_debate_enabled": True, "llm_debate_min_confidence": 0.5},
        ))
    assert result is not None
    assert result.signal == "NONE"
    assert result.firewall_vetoed is True
    assert "mismatch" in result.firewall_reason


def test_run_debate_firewall_vetoes_low_confidence():
    """Judge agrees but confidence below floor → vetoed."""
    judge_json = json.dumps({
        "signal": "BUY", "confidence": 0.3,
        "reasoning": "Weak bull case.",
    })

    with patch("app.services.agent.llm.router.is_llm_available", new=AsyncMock(return_value=True)), \
         patch("app.services.agent.llm.router._chat", new=AsyncMock(side_effect=_make_fake_chat("arg", "arg", judge_json))):
        result = _run(debate.run_debate(
            "BUY", {"score": 3, "confidence": 0.7},
            config={"llm_debate_enabled": True, "llm_debate_min_confidence": 0.5},
        ))
    assert result is not None
    assert result.signal == "NONE"
    assert result.firewall_vetoed is True
    assert "floor" in result.firewall_reason


def test_run_debate_advocate_failure_returns_none():
    """If an advocate generation fails, debate returns None."""
    async def fake_chat(**kwargs):
        from app.services.agent.llm.base import LLMResult
        return LLMResult(text=None, model=None, provider="off")

    with patch("app.services.agent.llm.router.is_llm_available", new=AsyncMock(return_value=True)), \
         patch("app.services.agent.llm.router._chat", new=AsyncMock(side_effect=fake_chat)):
        result = _run(debate.run_debate(
            "BUY", {"score": 3, "confidence": 0.7},
            config={"llm_debate_enabled": True},
        ))
    assert result is None


def test_run_debate_judge_none_accepted():
    """Judge says NONE → accepted as NONE (downgrade, not vetoed)."""
    judge_json = json.dumps({
        "signal": "NONE", "confidence": 0.0,
        "reasoning": "Arguments balanced, no edge.",
    })

    with patch("app.services.agent.llm.router.is_llm_available", new=AsyncMock(return_value=True)), \
         patch("app.services.agent.llm.router._chat", new=AsyncMock(side_effect=_make_fake_chat("arg", "arg", judge_json))):
        result = _run(debate.run_debate(
            "BUY", {"score": 3, "confidence": 0.7},
            config={"llm_debate_enabled": True, "llm_debate_min_confidence": 0.5},
        ))
    assert result is not None
    assert result.signal == "NONE"
    assert result.firewall_vetoed is False  # accepted downgrade, not vetoed


# --- DebateVerdict serialization ---------------------------------------


def test_verdict_to_dict():
    v = debate.DebateVerdict(
        signal="BUY", confidence=0.75, reasoning="test",
        bull_argument="bull", bear_argument="bear", judge_argument="judge",
    )
    d = v.to_dict()
    assert d["signal"] == "BUY"
    assert d["confidence"] == pytest.approx(0.75)
    assert d["firewall_vetoed"] is False
