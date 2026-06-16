"""Structured chart analyst insight contract (shared with frontend via JSON)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


SignalType = Literal["BUY", "SELL", "NONE"]


@dataclass
class ChartAgentInsight:
    symbol: str
    bar_time: int
    timeframe: str = "1m"
    signal: SignalType = "NONE"
    confidence: float = 0.0
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    levels: dict[str, Any] = field(default_factory=dict)
    narrative: str | None = None
    model: str | None = None
    created_at: str = ""
    insight_id: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.insight_id and self.symbol and self.bar_time:
            self.insight_id = f"{self.symbol}:{self.bar_time}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChartAgentInsight:
        return cls(
            symbol=data["symbol"],
            bar_time=int(data["bar_time"]),
            timeframe=data.get("timeframe", "1m"),
            signal=data.get("signal", "NONE"),
            confidence=float(data.get("confidence", 0)),
            score=int(data.get("score", 0)),
            reasons=list(data.get("reasons") or []),
            levels=dict(data.get("levels") or {}),
            narrative=data.get("narrative"),
            model=data.get("model"),
            created_at=data.get("created_at", ""),
            insight_id=data.get("insight_id", ""),
        )
