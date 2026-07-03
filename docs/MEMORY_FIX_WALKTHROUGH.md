# Memory Leak Fixes — Walkthrough

Resolved browser **"Error code: Out of Memory"** crash by fixing 9 memory leaks across frontend and backend. All 555 tests pass, frontend builds clean.

---

## Frontend Fixes (4 files)

### FIX 1: Cap `visionReports` to 10 entries
**File:** [useStore.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/store/useStore.js)

Vision reports contain base64-encoded chart images (500KB-2MB each). Previously grew without limit. Now oldest entries are evicted when count exceeds 10.

### FIX 2: Cap `agentDeepReasoning` to 20 entries
**File:** [useStore.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/store/useStore.js)

Each LLM reasoning response is 5-20KB. Now capped to 20 entries with oldest evicted.

### FIX 3: Trim backtest results before storing
**File:** [dispatch.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/api/dispatch.js)

- Equity curves over 2,000 points are downsampled (keep every Nth point)
- Reasoning trades capped at 50 entries
- Overlay trades capped at 200, equity at 2,000

### FIX 4: Cap `agentInsights` to 30 keys
**File:** [useStore.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/store/useStore.js)

Insight map grows per `symbol|timeframe` combination. Now capped at 30 keys. Also caps `agentInsightHistory` to 15 symbol entries.

### FIX 5: Prune `orderBooks` on LRU eviction
**File:** [useStore.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/store/useStore.js)

When a symbol's candle buffer is LRU-evicted, its order book data is now also cleaned up.

### FIX 6: Prune `candleRevision` / `candleHistoryRevision` keys
**File:** [useStore.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/store/useStore.js)

Revision maps accumulated keys for every symbol ever seen. Now capped to 30 keys — oldest are pruned.

---

## Backend Fixes (3 files)

### FIX 7: Candle cache TTL + max entries
**File:** [backtester.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/backtester.py)

`_candle_cache` now stores `(timestamp, DataFrame)` tuples. Entries auto-expire after 5 minutes. Max 10 entries enforced on every write. Prevents zombie DataFrames from crashed sweeps.

### FIX 8: Chart analyst cache eviction
**File:** [chart_analyst.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/agent/chart_analyst.py)

`_set_cache` now evicts expired entries and caps total to 100. Previously entries were never removed from the dict.

### FIX 9: Sim feed `deque` instead of `list.pop(0)`
**File:** [sim_feed.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/sim_feed.py)

Replaced `list` + `pop(0)` (O(N) per minute per symbol) with `collections.deque(maxlen=10080)` (O(1) auto-eviction). `get_candles()` returns `list(deque)` for API compatibility.

---

## Verification

| Check | Result |
|-------|--------|
| Backend tests | **555 passed**, 3 skipped ✅ |
| Frontend build | Clean (vite build) ✅ |
| No regressions | All existing tests unchanged ✅ |
| Functionality preserved | All caps use generous limits — no user-visible data loss ✅ |
