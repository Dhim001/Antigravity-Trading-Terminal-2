# Agentic Reasoning Depth — Diagnosis & Upgrade Proposals

## Current Architecture: What's Shallow

After auditing all six agents plus the Copilot, I've identified **five structural weaknesses** that cap your system at "lightweight reactive" rather than "medium autonomy":

---

### Diagnosis 1: Single-Pass Evaluate → Act (No Reasoning Chain)

Every agent follows the same pattern:

```
evaluate() → check N thresholds → emit action
```

There is **no intermediate reasoning step** between observation and action. The agents don't:
- Weigh competing evidence against each other
- Express uncertainty or confidence in their own conclusions
- Consider alternative explanations before acting
- Chain multiple observations into a coherent hypothesis

**Example — [RiskSentinel](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/risk_sentinel.py):**
It sees drawdown velocity > threshold → pauses everything. It never asks: "Is this a flash-crash that will reverse in 30 seconds, or a structural breakdown?" Both get identical treatment.

**Example — [PreTradeIntel](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/pretrade_intel.py):**
It runs 5 independent checks and takes the worst verdict. It never synthesizes: "Sentiment is bearish BUT correlation risk is low AND the failure streak is only on a different timeframe — net assessment: proceed with caution."

---

### Diagnosis 2: No Memory / Learning Between Cycles

Every `evaluate()` call is stateless (except `RiskSentinel.drawdown_history` which is a raw deque). The agents can't:
- Remember what they decided last cycle and why
- Detect that they've been flip-flopping (e.g., regime rotation toggling every 15 minutes)
- Track how accurate their recent decisions were
- Build confidence based on accumulated evidence over time

**Example — [RegimeRotation](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/regime_rotation.py#L79-L91):**
```python
if ratio >= 1.5:
    regime = "elevated_vol"
elif float(adx_val) > 25:
    regime = "trending"
else:
    regime = "ranging"
```
This classifies every single tick independently. If ADX oscillates around 25 for an hour, the agent will thrash between "trending" and "ranging" — no hysteresis, no memory of the last classification, no "hold steady" logic.

---

### Diagnosis 3: No Inter-Agent Communication

The agents are completely isolated. They don't know what each other decided:
- **RiskSentinel** pauses a bot, but **RegimeRotation** has no idea and may try to rotate it on the next cycle
- **PreTradeIntel** vetoes an entry, but **AlphaDecay** doesn't know the strategy was already blocked (and will still monitor a position that never opened)
- The **Copilot** has no awareness of what background agents are doing — it can't tell the user "Risk Sentinel just paused your bot because drawdown spiked"

---

### Diagnosis 4: Copilot Agent Loop is 1-shot Plan → Execute

The [copilot_agent.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/agent/copilot_agent.py) does a single LLM call to plan tools, then blindly executes them. It never:
- Inspects tool results and decides if follow-up tools are needed
- Self-corrects when a tool returns an error
- Chains tool outputs (e.g., "I analyzed BTCUSDT → it's trending → let me also check which bots are already running on it → advise accordingly")
- Reflects on whether its answer actually addressed the user's question

The planner system prompt (L23–L58) limits itself to "at most 2 tool_calls" and has no loop or retry.

---

### Diagnosis 5: No Structured Reasoning Output

Agents produce flat result dicts (`{"velocity_breached": True}`) with no chain-of-thought. This means:
- No auditability — you can't understand *why* an agent made a decision
- No confidence scores on decisions
- No explanation that can be surfaced to the user
- No data for the system to self-improve

---

## Concrete Upgrade Proposals (Shallow → Medium)

### Upgrade 1: Structured Reasoning Chain (ReAct-Lite)

Add a `ReasoningStep` dataclass and an `AgentContext` that accumulates observations before deciding:

```python
@dataclass
class Observation:
    source: str          # "drawdown_velocity", "correlation", "sentiment"
    signal: str          # "danger", "neutral", "positive"
    confidence: float    # 0.0 – 1.0
    detail: str          # human-readable explanation
    data: dict           # raw numbers

@dataclass
class AgentReasoning:
    observations: list[Observation]
    synthesis: str       # "Multiple weak signals converge → medium concern"
    decision: str        # "PAUSE" / "REDUCE" / "HOLD"
    confidence: float    # overall confidence in decision
    alternatives_considered: list[str]
```

Each agent collects `Observation`s, then runs a synthesis step (either rule-based weighted scoring, or an LLM pass for complex decisions). The `AgentReasoning` is attached to every action for auditability.

**Impact:** Every decision becomes explainable and auditable. The Copilot can tell users *why* something happened.

---

### Upgrade 2: Working Memory + Hysteresis

Add a per-agent `WorkingMemory` that persists across evaluate cycles:

```python
class WorkingMemory:
    last_decision: str
    last_decision_time: float
    decision_streak: int           # how many consecutive identical decisions
    recent_accuracy: deque         # (decision, was_it_correct) tuples
    cooldown_until: float          # prevent flip-flopping
    accumulated_evidence: dict     # running tallies
```

**For RegimeRotation specifically**, this prevents thrashing:
- Don't rotate unless the new regime has been detected for `N` consecutive cycles (configurable, e.g., 3 checks = ~15 minutes)
- Track how many rotations happened in the last hour; if > 2, increase the hysteresis threshold
- Log the accuracy of past rotations (did PnL improve after the switch?)

**For RiskSentinel:**
- Distinguish between a spike that recovered (false alarm) vs. sustained drawdown
- Cool-down after pausing: don't immediately re-pause if the user manually resumed

---

### Upgrade 3: Agent Event Bus (Inter-Agent Communication)

Create a lightweight in-memory event bus that agents publish/subscribe to:

```python
class AgentEventBus:
    """In-memory pub/sub for inter-agent coordination."""
    
    async def publish(self, event: AgentEvent) -> None: ...
    def subscribe(self, event_type: str, callback: Callable) -> None: ...
    def recent_events(self, event_type: str, lookback_sec: float) -> list[AgentEvent]: ...

@dataclass
class AgentEvent:
    source_agent: str      # "RISK_SENTINEL"
    event_type: str        # "BOT_PAUSED", "REGIME_CHANGED", "ENTRY_VETOED"
    payload: dict
    timestamp: float
    reasoning: AgentReasoning | None
```

**Example flows enabled:**
- RiskSentinel pauses a bot → publishes `BOT_PAUSED` → RegimeRotation skips that bot → Copilot knows to inform the user
- RegimeRotation detects regime shift → publishes `REGIME_CHANGED` → PreTradeIntel adjusts its sentiment thresholds for the new regime
- PreTradeIntel vetoes 3 entries in a row → publishes `STRATEGY_STRUGGLING` → AlphaDecay fast-tracks a decay check on that strategy

---

### Upgrade 4: Multi-Step Copilot Agent Loop

Replace the 1-shot planner with a proper ReAct loop (capped at 3 iterations):

```
User message
  └─> LLM plans tool calls (iteration 1)
      └─> Execute tools
          └─> LLM inspects results: "Do I have enough info to answer?"
              ├─ YES → Generate final response
              └─ NO → Plan follow-up tool calls (iteration 2)
                  └─> Execute follow-up tools
                      └─> Generate final response (iteration 3 max)
```

This enables the Copilot to:
- **Self-correct:** "analyze_symbol returned an error → I'll try a different timeframe"
- **Chain reasoning:** "The user asked 'should I deploy?' → I'll analyze the market, check existing bots for conflicts, then recommend"
- **Synthesize multi-tool results:** "Portfolio shows high exposure + BTCUSDT is ranging + you already have 2 BRS bots → I'd recommend holding off"

**System prompt addition for the planning step:**
```
After receiving tool results, decide:
1. "ANSWER" — you have enough information to respond.
2. "FOLLOW_UP" — call more tools (list them). Max 3 iterations total.

When you have results from multiple tools, SYNTHESIZE them into a 
unified recommendation. Do not just list each result separately.
```

---

### Upgrade 5: Decision Confidence Scores + Explanations

Every agent output should include:

| Field | Type | Purpose |
|:---|:---|:---|
| `confidence` | `float` (0–1) | How sure the agent is in its decision |
| `reasoning_chain` | `list[str]` | Step-by-step logic trail |
| `uncertainty_sources` | `list[str]` | What data was missing or ambiguous |
| `recommendation_strength` | `"strong" / "moderate" / "weak"` | Guides downstream weight |

**Example output from an upgraded PreTradeIntel:**
```json
{
  "verdict": "REDUCE_SIZE",
  "confidence": 0.72,
  "reasoning_chain": [
    "✅ No macro event within blackout window (confidence: 0.95)",
    "⚠️ ETHUSDT correlation 0.78 with existing BTC LONG — same direction (confidence: 0.82)",
    "✅ No failure streak for this strategy/symbol (confidence: 0.90)",
    "⚠️ Sentiment slightly negative (-0.15) but below threshold (confidence: 0.60)",
    "✅ No price anomaly detected (confidence: 0.88)"
  ],
  "synthesis": "One moderate concern (correlation exposure) with otherwise clear signals. Reducing size rather than vetoing.",
  "size_multiplier": 0.5,
  "uncertainty_sources": ["sentiment data only has 3 mentions (low sample)"]
}
```

---

## Priority Implementation Order

| Priority | Upgrade | Effort | Impact on Reasoning Quality |
|:---|:---|:---|:---|
| 🥇 1 | Working Memory + Hysteresis | Medium | Eliminates flip-flopping, biggest single improvement to decision quality |
| 🥈 2 | Structured Reasoning Chain | Medium | Makes every decision auditable and explainable |
| 🥉 3 | Multi-Step Copilot Loop | Medium-High | Transforms the chatbot from "lookup tool" to "reasoning assistant" |
| 4 | Agent Event Bus | Medium | Enables coordinated decision-making across agents |
| 5 | Decision Confidence Scores | Low | Better downstream weighting + user-facing transparency |

---

## What "Medium Capability" Looks Like After These Upgrades

| Aspect | Current (Shallow) | After Upgrades (Medium) |
|:---|:---|:---|
| **Decision making** | Threshold-triggered, binary | Multi-signal synthesis with confidence weighting |
| **Memory** | Stateless per cycle | Cross-cycle memory with hysteresis and cooldowns |
| **Coordination** | None — agents isolated | Event bus with reactive chaining |
| **Copilot reasoning** | 1-shot tool → answer | Multi-step ReAct with self-correction |
| **Explainability** | Flat result dict | Structured reasoning chain with audit trail |
| **Error handling** | Try/except → skip | Self-correction and fallback strategies |
| **Flip-flop prevention** | None | Configurable hysteresis + decision streaks |

> [!TIP]
> The most impactful single change is **Upgrade 2 (Working Memory)** applied to RegimeRotation. Right now, the regime classifier at L79-91 can thrash between "trending" and "ranging" every tick if ADX hovers near 25. Adding a 3-cycle confirmation requirement would immediately eliminate the most visible problem in production.
