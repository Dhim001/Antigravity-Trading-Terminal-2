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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Observation":
        """Reconstruct an observation from its ``to_dict``/wire form (defensive)."""
        if not isinstance(data, dict):
            data = {}
        payload = data.get("data")
        return cls(
            source=str(data.get("source") or ""),
            signal=str(data.get("signal") or ""),
            confidence=float(data.get("confidence") or 0.0),
            detail=str(data.get("detail") or ""),
            data=payload if isinstance(payload, dict) else {},
        )


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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentReasoning":
        """Reconstruct a reasoning chain from its ``to_dict``/wire form.

        Defensive by design — events persisted to SQLite or relayed over Redis
        must survive partial / older payloads instead of being dropped.
        """
        if not isinstance(data, dict):
            data = {}
        raw_obs = data.get("observations")
        observations = [
            Observation.from_dict(o) for o in raw_obs if isinstance(o, dict)
        ] if isinstance(raw_obs, list) else []

        def _str_list(value: Any) -> list[str]:
            if not isinstance(value, list):
                return []
            return [str(v) for v in value]

        return cls(
            observations=observations,
            synthesis=str(data.get("synthesis") or ""),
            decision=str(data.get("decision") or ""),
            confidence=float(data.get("confidence") or 0.0),
            alternatives_considered=_str_list(data.get("alternatives_considered")),
            uncertainty_sources=_str_list(data.get("uncertainty_sources")),
            recommendation_strength=str(data.get("recommendation_strength") or "moderate"),
        )
