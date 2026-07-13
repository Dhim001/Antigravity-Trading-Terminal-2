"""Structured Reasoning Models for Agent Transparency."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Observation:
    """A single piece of evidence collected by an agent."""
    source: str
    signal: str
    confidence: float
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentReasoning:
    """The synthesized logic chain leading to an agent's decision."""
    observations: list[Observation]
    synthesis: str
    decision: str
    confidence: float
    alternatives_considered: list[str] = field(default_factory=list)
    uncertainty_sources: list[str] = field(default_factory=list)
    recommendation_strength: str = "moderate"

    def to_dict(self) -> dict[str, Any]:
        """Convert the reasoning chain to a dictionary for logging/API export."""
        return {
            "observations": [
                {
                    "source": o.source,
                    "signal": o.signal,
                    "confidence": o.confidence,
                    "detail": o.detail,
                    "data": o.data
                }
                for o in self.observations
            ],
            "synthesis": self.synthesis,
            "decision": self.decision,
            "confidence": self.confidence,
            "alternatives_considered": self.alternatives_considered,
            "uncertainty_sources": self.uncertainty_sources,
            "recommendation_strength": self.recommendation_strength
        }
