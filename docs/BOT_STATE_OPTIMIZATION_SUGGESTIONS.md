# Bot State Management & Optimization Suggestions

Based on a review of your current trading terminal architecture and deep research into institutional algorithmic trading best practices, I've compiled an assessment and suggestions for improving how your bots handle state, execute trades, and optimize memory/CPU performance.

## 1. Current State Assessment
Currently, the terminal uses two main methods to pass state to bots:
1. **Pandas DataFrames (Backtesting):** `indicators.py` computes all necessary columns across the entire dataset in a vectorized manner, which is highly CPU efficient for historical testing.
2. **Python Lists (Live/Tick Simulation):** Inside strategies like `WyckoffStrategy` and `CvdDivergenceStrategy`, state is retained by appending `df_row` dictionaries to a standard Python list (`self.history.append(df_row)`), then truncating it (`if len > N: self.history.pop(0)`).

**The Problem:** 
- `list.pop(0)` in Python is an `O(N)` operation because it forces the entire list to shift in memory. For small lists (N=30) it's harmless, but for tick-level data (N=10,000+), this creates CPU spikes.
- Storing full dictionaries (`df_row`) per tick/bar duplicates memory rapidly and forces the garbage collector (GC) to work constantly to clean up popped dictionaries, which causes non-deterministic latency.

---

## 2. Core Architectural Suggestions (CPU & Memory Bound)

To improve execution efficiency without crashing the UI or causing memory bloat, I suggest the following architectural changes:

### A. Transition to `collections.deque` (Immediate Fix)
Instead of standard lists, strategy internal buffers should use `collections.deque(maxlen=N)`. 
- **Why:** A deque (double-ended queue) is implemented as a doubly-linked list in C. Pushing and popping from the ends is an `O(1)` operation. When `maxlen` is reached, it automatically drops the oldest item natively in C, saving Python CPU cycles.
- **Cost:** Practically zero. It's built into Python's standard library.

### B. Pre-allocated Numpy Ring Buffers (High-Frequency Fix)
For tick-level strategies (`TICK_MOMENTUM`, `TICK_MEAN_REVERT`) and orderbook analysis, Python dictionaries are too heavy.
- **Why:** Dicts have high memory overhead. Instead, initialize a fixed-size NumPy array at bot startup: `self.price_history = np.zeros(1000)`. Use a cursor index (`self.idx = (self.idx + 1) % 1000`) to overwrite the oldest data.
- **Benefit:** This is known as "Memory Pooling" or a "Ring Buffer". It completely eliminates Garbage Collection pauses because no new memory is allocated or destroyed during the hot trading loop.

### C. Context Bridges for Shared State
Right now, each bot maintains its own `self.history`. If you run 5 bots on BTC/USD, you are storing the exact same BTC/USD history 5 times in memory.
- **Suggestion:** Implement a centralized `MarketState` singleton in the backend that holds the last N bars and the Level 2 Orderbook. Bots should simply receive a *reference* (pointer) to this shared state rather than storing their own copies.

---

## 3. Improving Trade Execution & Win-Rates (State-Driven)

Winning strategies don't just react to the market—they react to their own performance. Currently, the bots have no "self-awareness" of their past trades. 

### A. Regime-Adaptive Sizing (Kelly Criterion State)
Bots should retain a lightweight state of their last 5-10 trades (e.g., `self.recent_pnl = [+1.2, -0.5, -0.6]`).
- **Logic:** If the bot is in a drawdown (e.g., 3 consecutive losses), it signals that the current market regime has shifted away from the bot's edge. The bot should automatically halve its `allocation` size. When it secures a win, it scales back to normal.
- **Impact:** Protects capital during choppy chop/whipsaw markets, drastically improving the overall profit factor.

### B. Time-in-Trade State (Time Stops)
Currently, bots rely on static Stop Loss / Take Profit percentages. 
- **Suggestion:** Add a `bars_since_entry` state counter. If an Order Block or VWAP pullback trade hasn't moved into profit within 5-8 bars, the momentum has likely failed. 
- **Logic:** The bot should actively update the OMS to scratch the trade (exit at market) to free up capital, rather than waiting for the hard stop-loss to be hit.

### C. Agentic Post-Trade Reflection (For `CHART_AGENT`)
For the LLM-driven agent, feed the result of the *last* trade back into its next prompt.
- **Example Prompt Injection:** *"Your last trade (LONG) hit the stop-loss because momentum failed to follow through. The current setup is identical. Given your recent failure, how confident are you in this signal?"*
- **Impact:** Allows the LLM to dynamically adjust its `confidence_score` based on immediate historical feedback, suppressing consecutive false signals.

---

## Summary of Actionable Steps
If you want to implement these, we can start with the lowest-effort/highest-yield changes:
1. Refactor `self.history = []` to `self.history = deque(maxlen=N)` across all microstructure strategies.
2. Add a `recent_trades` feedback loop to the `BaseStrategy` class so bots can adjust their confidence/sizing based on current winning/losing streaks.
