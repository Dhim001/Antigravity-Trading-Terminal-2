# Memory Diagnostic — Browser "Out of Memory" Crash

> **Error:** `Error code: Out of Memory` — Chrome tab crash
> **Root cause:** Multiple unbounded data structures in the Zustand store and services accumulate over time until the browser's JS heap limit (~2-4 GB) is exceeded.

---

## Critical Findings

### 🔴 LEAK 1: `visionReports` — Unbounded Map (No cap)
**File:** [useStore.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/store/useStore.js#L142)
**Severity:** HIGH — Each vision report contains base64-encoded chart images (~500KB-2MB each)

```js
visionReports: {},  // line 142 — NEVER pruned
setVisionReport: (key, report) => set((state) => ({
  visionReports: { ...state.visionReports, [key]: report },  // grows forever
})),
```

Every `symbol:timeframe` combination adds a vision report with embedded image data. After analyzing 10 symbols × 3 timeframes, this can reach **30-60 MB** and grow without limit.

**Fix:** Cap to 10 most-recent entries.

---

### 🔴 LEAK 2: `agentDeepReasoning` — Unbounded Map (No cap)
**File:** [useStore.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/store/useStore.js#L347-L350)

```js
agentDeepReasoning: {},  // line 347 — NEVER pruned
setAgentDeepReasoning: (insightId, data) => set((state) => ({
  agentDeepReasoning: { ...state.agentDeepReasoning, [insightId]: data },
})),
```

Each deep reasoning response includes full LLM text (~5-20 KB each). With continuous analysis, this grows indefinitely.

**Fix:** Cap to 20 most-recent entries.

---

### 🔴 LEAK 3: `backtestResults` + `backtestOverlay` — Large Retained Objects
**File:** [dispatch.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/api/dispatch.js#L127-L155)

A single backtest result contains:
- `trades[]` — up to 500+ trade objects
- `equity_curve[]` — up to 5,000+ data points
- `reasoning.trades[]` — LLM explanations per trade
- `sub_reports` — full indicator data

A sweep result contains **N×** these. The old results are replaced but the overlay persists separately.

**Fix:** Trim `equity_curve` to 2,000 points max, cap `trades` to 200 with summary.

---

### 🟡 LEAK 4: `agentInsights` — Keys Grow Per Symbol × Timeframe
**File:** [useStore.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/store/useStore.js#L438-L457)

`agentInsights` stores one insight per `symbol|timeframe` key AND a legacy `SYMBOL` key for 1m. The map is never pruned. With 5 symbols × 6 timeframes = 30+ entries, each containing full domain reports.

**Fix:** Cap to 30 entries with LRU eviction.

---

### 🟡 LEAK 5: `orderBooks` — One Full Book Per Symbol, Never Pruned
**File:** [useStore.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/store/useStore.js#L76)

Each order book contains 10 bids + 10 asks arrays. But the map grows per symbol and old symbols are never removed, even after candle buffers are LRU-evicted.

**Fix:** Prune on candle buffer eviction.

---

### 🟡 LEAK 6: `candleRevision` / `candleHistoryRevision` — Monotonic Growth
**File:** [useStore.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/store/useStore.js#L38-L40)

```js
export function bumpRevision(revisions, symbol) {
  return { ...revisions, [symbol]: (revisions[symbol] || 0) + 1 };
}
```

Every tick creates a **new object** via `{...revisions}` spread. At 4 ticks/sec × 5 symbols = 20 new objects/sec = **72,000 objects/hour**. While GC clears old references, the sheer churn rate can overwhelm V8's garbage collector, causing memory pressure.

Additionally, symbols are never removed from these maps, so they accumulate keys over the session.

**Fix:** Use mutable counter bump instead of spread.

---

### 🟡 LEAK 7: Backend `_candle_cache` on BacktesterService — No Auto-Clear
**File:** [backtester.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/backtester.py#L221)

```python
self._candle_cache: dict[str, "pd.DataFrame"] = {}  # keyed by (symbol, strategy, len)
```

The cache is only cleared when `clear_candle_cache()` is explicitly called. If a sweep crashes mid-way or the handler forgets to call it, the cached DataFrames persist for the server's lifetime. Each DF can be **10-50 MB**.

**Fix:** Add TTL-based auto-expiry.

---

### 🟡 LEAK 8: Backend Chart Analyst `_cache` — Never Evicts Stale Entries
**File:** [chart_analyst.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/agent/chart_analyst.py#L83)

```python
self._cache: dict[str, tuple[float, dict]] = {}
```

Entries are keyed by `symbol|timeframe`. While entries expire by TTL on read, they're never removed from the dict. Over a long session with many symbols, stale entries accumulate.

**Fix:** Evict expired entries on each write; cap dict to 100 entries.

---

### 🟢 LEAK 9: `sim_feed.py` `active_candles.pop(0)` — O(N) Trim
**File:** [sim_feed.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/sim_feed.py#L232-L233)

```python
active_candles.append(new_candle)
if len(active_candles) > 10080:
    active_candles.pop(0)  # O(N) — shifts 10,000 elements
```

Not a memory leak, but `pop(0)` on a 10K list causes CPU spikes every minute per symbol. With 5+ symbols this creates noticeable lag.

**Fix:** Use `collections.deque(maxlen=10080)`.

---

## Proposed Fix Priority

| # | Issue | Severity | Memory Impact | Fix Complexity |
|---|-------|----------|--------------|----------------|
| 1 | `visionReports` unbounded | 🔴 HIGH | 30-100+ MB | Simple cap |
| 2 | `agentDeepReasoning` unbounded | 🔴 HIGH | 10-50 MB | Simple cap |
| 3 | `backtestResults` large retained | 🔴 HIGH | 20-100+ MB | Trim arrays |
| 4 | `agentInsights` unbounded keys | 🟡 MED | 5-15 MB | LRU cap |
| 5 | `orderBooks` never pruned | 🟡 MED | 1-5 MB | Prune on evict |
| 6 | `candleRevision` object churn | 🟡 MED | GC pressure | Mutable bump |
| 7 | Backend `_candle_cache` no TTL | 🟡 MED | 50-200 MB | TTL eviction |
| 8 | Analyst `_cache` stale entries | 🟡 MED | 5-20 MB | Evict expired |
| 9 | `sim_feed.pop(0)` O(N) | 🟢 LOW | CPU only | Use deque |

> [!IMPORTANT]
> **None of these fixes reduce functionality.** All caps use generous limits. Data that exceeds the cap is simply the oldest/least-used entry — the user would never notice it being evicted.

## Verification Plan
- Run the terminal for 30+ minutes with 5 symbols and multiple backtests
- Monitor `performance.memory.usedJSHeapSize` via the existing MemoryDevBadge
- Confirm heap stays below 500 MB (vs current unbounded growth)
