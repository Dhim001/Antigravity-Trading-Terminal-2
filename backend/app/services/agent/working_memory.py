"""Working Memory module to track agent decisions and implement hysteresis."""

import time
from typing import Any


class WorkingMemory:
    """Tracks state across evaluation cycles to prevent agent flip-flopping."""
    
    def __init__(self) -> None:
        self.last_decision: Any | None = None
        self.last_decision_time: float = 0.0
        self.decision_streak: int = 0
        self.cooldown_until: float = 0.0
        self.accumulated_evidence: dict[str, Any] = {}

    def update_decision(self, new_decision: Any) -> tuple[bool, int]:
        """Update the tracking of consecutive identical decisions.
        
        Returns:
            (is_same_decision, current_streak_count)
        """
        now = time.time()
        
        if new_decision == self.last_decision:
            self.decision_streak += 1
        else:
            self.last_decision = new_decision
            self.decision_streak = 1
            
        self.last_decision_time = now
        return (self.decision_streak > 1, self.decision_streak)

    def set_cooldown(self, seconds: float) -> None:
        """Set a cooldown period during which the agent should not act."""
        self.cooldown_until = time.time() + seconds

    def is_cooling_down(self) -> bool:
        """Check if the memory is currently in a cooldown period."""
        return time.time() < self.cooldown_until
