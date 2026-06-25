"""Execution model helpers — paper (sim / Massive) vs broker-backed live modes."""

from __future__ import annotations

from app.config import TERMINAL_MODE

# Internal paper ledger; no broker reconcile or ambiguous-order workflow.
PAPER_EXECUTION_MODES = frozenset({"SIMULATED", "LIVE_MASSIVE"})


def uses_paper_oms() -> bool:
    return TERMINAL_MODE in PAPER_EXECUTION_MODES


def is_live_massive() -> bool:
    return TERMINAL_MODE == "LIVE_MASSIVE"


def execution_mode_label() -> str:
    if TERMINAL_MODE == "LIVE_MASSIVE":
        return "paper"
    if TERMINAL_MODE == "SIMULATED":
        return "simulated"
    return "broker"
