# Memory Optimization — Status & Verification

The browser OOM crash (`Error code: Out of Memory`) comes from **cumulative heap growth** across several vectors over 2–4+ hour sessions. Fixes are phased; all Tier 1–3 items below are **implemented** on the current branch.

---

## Implementation Status

### Tier 1 — Retained object caps (OOM prevention)

| Fix | Status | Module |
|-----|--------|--------|
| Cap `backtestResults.trades[]` + WF fold trim | ✅ | `lib/backtestSlim.js` |
| Backtest offload to `sessionStorage` when Lab closes | ✅ | `services/backtestStorage.js` |
| Overlay equity capped to 400 pts | ✅ | `lib/backtestSlim.js` |
| Order book prune (6 symbols) + gated merge | ✅ | `store/useStore.js`, `services/orderBookInterest.js` |
| `tradeHistory` cap 500 | ✅ | `store/useStore.js` |
| `agentInsightHistory` 20/symbol, 8 symbols | ✅ | `store/useResearchStore.js` |
| `visionReports` cap 10 + strip image fields | ✅ | `store/useResearchStore.js` |
| `agentDeepReasoning` cap 20 | ✅ | `store/useResearchStore.js` |
| Sonner `visibleToasts={3}` | ✅ | `components/ui/sonner.jsx` |

### Tier 2 — GC pressure reduction (tick path)

| Fix | Status | Module |
|-----|--------|--------|
| `candleRevision` outside Zustand (module `Map`) | ✅ | `services/candleRevisions.js` |
| RAF batching (unchanged) | ✅ | `services/marketUpdateBatch.js` |
| In-place ticker/direction mutation (existing symbols) | ✅ | `store/useStore.js` |
| Field-level batch merge (no spread per tick) | ✅ | `services/marketUpdateBatch.js` |
| Snapshot debounce 10s, 150 candles, scoped tickers | ✅ | `services/marketSnapshot.js` |
| Chart: trade overlay via `tradeOverlayKey` only | ✅ | `components/ChartWidget.jsx` |
| ECharts live patch `lazyUpdate: true` | ✅ | `components/ChartWidget.jsx` |

### Tier 3 — Pressure response & cold-path caps

| Fix | Status | Module |
|-----|--------|--------|
| `memoryGuard` — warn/critical trim every 30s | ✅ | `services/memoryGuard.js` |
| Pause debounced snapshot save under pressure | ✅ | `memoryGuard` + `marketSnapshot.js` |
| `scanResults` cap 200 rows | ✅ | `store/useResearchStore.js` |
| `chartDrawings` cap + evict on buffer LRU | ✅ | `store/useStore.js` |
| `backtestRuns` cap 20 | ✅ | `store/useResearchStore.js` |
| `journalEntries` cap 200 | ✅ | `store/useResearchStore.js` |
| Mini charts: 120 bar display cap + dispose on unmount | ✅ | `components/MiniChartWidget.jsx` |

### Tier 4 — Store split, typed buffers, IndexedDB (implemented)

| Fix | Status | Module |
|-----|--------|--------|
| Split Zustand: `useStore` (market) + `useResearchStore` (cold path) | ✅ | `store/useResearchStore.js` |
| IndexedDB backtest blobs (sessionStorage L1 + IDB L2) | ✅ | `services/idbBacktest.js`, `services/backtestStorage.js` |
| Typed-array 1m OHLCV buffers | ✅ | `services/compactBarSeries.js`, `services/candleBuffer.js` |
| Backend strips orderbooks from `market_update` | ✅ (pre-existing) | `backend/app/server.py` `_slim_market_payload()` |

---

## Architecture (post-optimization)

```
WebSocket tick
  → marketUpdateBatch (RAF coalesce, in-place merge)
  → updateMarketData (in-place ticker/direction; optional orderbooks)
  → candleBuffer (Map LRU, CompactBarSeries 1m buffers)
  → candleRevisions (Map counters, useSyncExternalStore)
  → ChartWidget (incremental ECharts patch)

Backtest complete
  → trimBacktestPayload → sessionStorage + IndexedDB full copy
  → useResearchStore retains trimmed results
  → Lab close → slim dock copy + _offloaded flag
  → Lab open → restore from sessionStorage, then IDB async

Heap ≥ 70%
  → memoryGuard: trim vision, insight history, pause snapshots
Heap ≥ 85%
  → memoryGuard: prune buffers, shrink orderbooks, offload backtest
```

---

## Verification Plan

1. **Baseline (2h):** 5 symbols, Massive profile, Book/Depth tab closed — heap should plateau **&lt; 400 MB** (`MemoryDevBadge` in dev).
2. **Backtest stress:** Run 3× walk-forward rigorous → close Lab → heap should drop within 5 min.
3. **Order book:** Open Book tab → books populate; close tab → no orderbook merges on ticks.
4. **Unit tests:**
   ```powershell
   cd frontend
   npm run test:unit -- src/services/candleRevisions.test.js src/services/backtestStorage.test.js src/services/memoryGuard.test.js src/services/compactBarSeries.test.js src/services/idbBacktest.test.js src/services/candleBuffer.test.js
   ```

---

## Original leak vectors (for reference)

The pre-fix diagnostic identified seven leak classes: backtest trades, orderbooks, tick allocation storm, agent insight history, session snapshot spikes, trade history, and toast DOM. Tiers 1–4 address all identified vectors.
