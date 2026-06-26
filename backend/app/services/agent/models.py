"""Structured chart analyst insight contract (shared with frontend via JSON)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from app.services.market.timeframes import normalize_timeframe


SignalType = Literal["BUY", "SELL", "NONE"]


def insight_cache_key(symbol: str, timeframe: str = "1m") -> str:
    """Memory/Redis cache key for analyst insights."""
    tf = normalize_timeframe(timeframe) if timeframe and timeframe != "tick" else "1m"
    return f"{symbol.upper()}:{tf}"


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
    version: int = 2
    sub_reports: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.insight_id and self.symbol and self.bar_time:
            tf = normalize_timeframe(self.timeframe) if self.timeframe else "1m"
            self.insight_id = f"{self.symbol.upper()}:{tf}:{self.bar_time}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("sub_reports") is None:
            d.pop("sub_reports", None)
        return d

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
            version=int(data.get("version", 1)),
            sub_reports=data.get("sub_reports"),
        )


@dataclass
class VisionReport:
    symbol: str
    timeframe: str
    bar_time: int
    structure: str = ""
    patterns: list[str] = field(default_factory=list)
    notes: str = ""
    model: str | None = None
    cached: bool = False
    report_id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.report_id and self.symbol and self.bar_time:
            tf = (self.timeframe or "4h").lower()
            self.report_id = f"{self.symbol.upper()}:{tf}:{self.bar_time}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisionReport:
        return cls(
            symbol=data["symbol"],
            timeframe=data.get("timeframe", "4h"),
            bar_time=int(data["bar_time"]),
            structure=data.get("structure") or "",
            patterns=list(data.get("patterns") or []),
            notes=data.get("notes") or "",
            model=data.get("model"),
            cached=bool(data.get("cached")),
            report_id=data.get("report_id", ""),
            created_at=data.get("created_at", ""),
        )
