# Memory-Centric Comprehensive Review & Improvement Suggestions

A second full-stack review of the trading terminal — backend (38+ service modules, agent pipeline, OMS, risk engine, ML training) and frontend (74 components, hooks, stores, transport) — with **RAM management as the central design constraint** for every suggestion. Informed by how Sierra Chart, TradingView (lightweight-charts), Quantower, and Hummingbot handle memory in production.

**Re-scanned 2026-07-28** (post Phase 1–4 signal work): every "shipped" claim below was re-verified against the code. Two claims were found **not actually landed** (#4, and #11 only partially) and are corrected here. New findings from the recent signal-gate / RL / telemetry work are items **#28–#42**.

Companion docs: `MEMORY_16GB.md` (current budget inventory), `MEMORY_DIAGNOSTIC.md` (Tier 1–4 fix ledger), `REVIEW_SUGGESTIONS.md` (previous general review), `CODE_AUDIT_REPORT.md`.

---

## Lessons from the reference platforms

| Platform | Memory principle | Applies here as |
|----------|------------------|-----------------|
| **Sierra Chart** | RAM scales strictly with *days-to-load* per chart; the user chooses the storage time unit (tick / 1s / 1m). Memory is a visible, user-controlled budget. | Extend `memoryBudget.js`-style explicit budgets to backend subsystems (ML stores, screener cache) with byte-bounded caps, surfaced in Settings → Memory. |
| **TradingView** (lightweight-charts 5.1) | *Data conflation*: points that would occupy <0.5 px when zoomed out are merged, so render memory scales with **pixels, not bars**. | Display-level downsampling for ECharts when zoomed out past ~1 bar/px — shipped as `conflateBars.js` (#23). |
| **Hummingbot** | Biggest RAM win was **headless mode** (~40% reduction — UI out of the bot process); v2.7 fixed websocket-connection and orphaned-async-task leaks. | Process separation for ML training/sweeps — **partially shipped** (#9/#27); Torch/RL still default in-process (#41). |
| **Quantower / Sierra** | Stability under load beats visual richness; low steady footprint with many charts. | Multi-chart grid degrades gracefully via pressure ladder (#26). |

---

## Already in place (verified 2026-07-28)

- **Client:** candle LRU (4 symbols) + bar caps, `memoryGuard` heap-pressure trimming, backtest IndexedDB offload + payload slimming, RAF-coalesced market updates, FlexLayout heavy-tab unmount (`MountWhenVisible`), multi-chart maximize unmount, overlay trade cap, capped store lists (tradeHistory 500, vision 10, deepReasoning 20, scan 200, runs 20, journal 200, insight history 20×8), ECharts `dispose()` on unmount, vision base64 stripped, no `JSON.parse(JSON.stringify)` clones.
- **Backend:** HT cache LRU+TTL, live 1m buffers capped (1500/symbol), screener LRU (200 entries + 128 MB), backtester DF cache (TTL 300s, max 10), agent insight/event history caps, archive query LIMIT + fetchmany, WAL + checkpoint, retention pruning, sweep trial budgets, wire payload trims, meta-label WF session models freed in `finally`, archive writer buffer clear-on-WAL + 20k cap, copilot session TTL 2h + 32 sessions, retrain scheduler TTL/caps, tick-screener + data-quality idle eviction (6h / 500 symbols), event-bus handler semaphore (32), SQLite `SQLITE_CACHE_KB` (default 64 MB), ML model stores LRU+TTL (12 entries / 1h) for ML/LSTM/TCN/Transformer/GNN/VAE/meta-label/PPO.

---

## 🔴 P0 — Steady-state heap churn

### 4. Live series cache cloned every paint — **NOT landed (corrected)**
`lib/chart/chartHelpers.js:408–428` — the forming-bar path still does `cache.main.slice()` + `cache.volume.slice()` on every live paint (~4×/s). ~600 display bars ⇒ thousands of array slots cloned per second, per chart, before ECharts copies again. Tests pin the current behavior (`chartHelpers.test.js:25–27` asserts new refs). The doc previously listed this as Week-1-shipped — **it was not**.
**Fix:** mutate `cache.main[idx]`/`cache.volume[idx]` in place; only rebuild on length/chart-type change. If ECharts requires a new ref to redraw, patch via `setOption` on the two series with `lazyUpdate` and a versioned key rather than cloning the full arrays; update the pinning test accordingly. Biggest steady GC win available on the client.

### 36. MiniChart main-series still sliced per tick
`MiniChartWidget.jsx:516–529` — `patchLastDisplayBucket` correctly mutates the last OHLC bucket, but `patchMiniMainSlot` still slices the main series each tick. In the 3×2 grid that is 6 partial re-allocations ~4×/s.
**Fix:** in-place slot patch matching #4's approach; keep bucket rebuild only on new bucket / symbol / TF change.

---

## 🔴 P0/P1 — Backend: Phase 1–4 caches with no eviction (new)

### 28. Signal-gate module caches grow per bot_id, forever
Four recently added gates use module-level dicts keyed by `bot_id` with **no LRU/TTL**, and each ships an `invalidate_*` helper that has **zero production callers**:

| Cache | Where | Payload |
|-------|-------|---------|
| `_bot_cal` | `bots/conformal_gate.py:183` | conformal thresholds |
| `_bot_models` | `bots/hmm_regime.py:230` | Gaussian-mixture model |
| `_bot_cache` | `bots/calibration_fitter.py:250` | temperature/Kelly blob |
| `_bot_models` | `bots/stacking_meta_learner.py:212` | sklearn meta-learner |

Payloads are small individually, but bots (and `_bot_id` fallbacks like `"backtest"`) accumulate over a session; the PPO/LSTM/meta-label stores already moved to `model_store_lru.bind_dict_cache` — these four did not.
**Fix:** route all four through `bind_dict_cache` (`ML_MODEL_CACHE_MAX=12`, `ML_MODEL_CACHE_TTL_SEC=3600`) — reload-from-disk on miss is milliseconds for JSON/joblib. Alternatively call the existing invalidate helpers from bot-delete / model-promotion paths. Small change; closes the "caches only grow" pattern the scan flagged.

---

## 🟠 P1 — Spike control & duplication

### 7. Anchored walk-forward copies growing candle prefixes — **shipped (partial)**
`backtest_walk_forward.py:197–199` — now slices `candles[:cursor]` / `candles[cursor:test_end]` (shallow copies sharing candle dicts). Acceptable; true windowed views would still cut the list-of-references overhead per fold.

### 8. Portfolio backtests hold all symbols + parallel copies — **partial**
`backtest_portfolio.py:503–636` — chunked batches + `chunk_candles.clear()` when the runner resolves candles itself. **But** a caller passing a full `candles_by_symbol` keeps every symbol resident for the whole run.
**Fix:** document/enforce the streaming path (resolve per batch) for large universes; warn when >N symbols arrive pre-materialized.

### 9/27/41. ML training process isolation — **partial (downgraded)**
`ml_train_executor.py:99–108` + `ml_train_limits.py` — pool exists with `ML_TRAIN_RSS_LIMIT_MB=4096` (real `RLIMIT_AS` only on Unix; advisory on Windows). **However** Torch/RL trainers (LSTM, RL_PPO, TCN, VAE, Transformer, GNN) default **in-process** via `asyncio.to_thread` when `ML_TRAIN_TORCH_IN_PROCESS` — the largest RSS spikes still share the live feed/OMS process. This is the Hummingbot lesson left half-applied.
**Fix:** flip the default so deep trainers use the process pool unless an operator opts into in-process for debugging; keep `max_workers=1` budget. Add Job-Object ceilings on Windows when available.

### 11. Backtest results retained in Zustand while Lab is open — **partial (corrected)**
`api/dispatch.js:20–31` + `backtestStorage.js` — offload happens when the Lab *closes* (slim dock copy). While the Lab is *open*, the full trimmed tree lives in `useResearchStore` (duplicate of session/IDB copy). Previously listed as shipped — it only covers the closed-Lab case.
**Fix:** acceptable trade-off while the Lab is open (needed for drill-down); keep as-is but ensure the slim dock copy is written on *every* completed run so closing never blocks on slimming.

### 17. IDB prune still deserializes full blobs
`services/idbBacktest.js:114–141` — cursor-based (no `getAll`), meta array keeps key/savedAt only, **but** `cursor.value` still materializes each full payload during the prune walk — an O(all runs × payload) transient spike.
**Fix:** use a key-only cursor (`openKeyCursor`) + `get(key)` lazily only for entries being deleted, or store `savedAt` in the key.

### 29. Reject-telemetry table has no retention
`bots/reject_telemetry.py:63–107` — every silent NONE / gate reject INSERTs a row (hot path, per bot per bar); only manual `clear_reject_log()` or `reset_db` trims. Unbounded **disk** growth plus slower aggregate queries over time.
**Fix:** retention pruning (e.g. keep 7 days / 500k rows) wired into the existing archive retention job; add a summary rollup before delete so long-term stats survive.

### 30. `ml_train_runs` table grows without prune
`bots/ml_train_runs.py` — every train/validate run persists a row; no retention like the optimization/backtest job stores have.
**Fix:** same retention pass as #29 (keep N days or M newest per symbol×strategy).

### 31. Terminal ML job results kept in RAM
`bots/ml_job_store.py:18–190` — store capped at 80 jobs, but finished/failed jobs keep their full `result` dicts (walk-forward bundles, metrics trees) until pruned.
**Fix:** on terminal state, replace `result` with a slim summary (status/metrics headline) and move the full payload to disk; or prune aggressively once the client has consumed the result.

### 40. `mlTrainingSession.statusCache` unbounded (client)
`lib/mlTrainingSession.js:8,88–95` — module `Map` grows per symbol×strategy×timeframe and retains validation bodies / feature-importance payloads across component unmounts; `ModelTrainingDashboard` polls (3s/5s/15s) keep it warm while open.
**Fix:** LRU cap (~12 keys) or TTL on idle entries; drop heavy `result` bodies once a job is terminal and the dashboard has rendered them.

### 44. Torch/RL trainer default in-process — see #9/27/41 above (deduplicated).

---

## 🟠 P1 — Frontend store growth (new)

### 38. `tickerData` / `tickData` never evict
`store/useStore.js:507–558, 476–477` — every symbol ever seen in a market update stays in `tickerData` forever (orderBooks/candles got LRUs; tickers did not). `tickData` is replaced wholesale with no per-symbol cap.
**Fix:** apply the same interest-gated LRU pattern as orderBooks (evict symbols not in watchlist/visible charts); cap tickData entries per symbol.

### 39. `setAgentInsightHistory` bypasses the 20/8 caps
`store/useResearchStore.js:266–270` — wholesale replace of a symbol's insight history without the `setAgentInsight` per-symbol (20) / symbol-count (8) caps.
**Fix:** run the same trim on replace.

### 40b. `botHistory` / `orders` / `positions` mirrored uncapped
`useStore.js:474, 279–282` — full API arrays mirrored client-side. Usually modest, but order-history storms grow heap.
**Fix:** cap `botHistory` at ~200 entries; keep orders/positions as live mirrors (bounded by server state).

### 42. `analyticsReport` merge accumulates; `backtestSnapshot` opaque
`useResearchStore.js:102–116, 200` — dashboard partial merges have no TTL/size bound; `backtestSnapshot` is only cleared on ML invalidate.
**Fix:** TTL or rebuild-on-demand for analyticsReport; clear backtestSnapshot with the same lifecycle as backtest results.

---

## 🟡 P2 — Bounded-but-large / hygiene — **shipped (re-verified)**

| # | Item | Where | Status |
|---|------|-------|--------|
| 12 | SQLite page cache static 64 MB | `db/connection.py:58–68` | ✅ `SQLITE_CACHE_KB` |
| 13 | Screener LRU entries+bytes | `screener.py:26–46` | ✅ 200 entries + 128 MB |
| 14 | Retrain pending/last maps | `ml_retrain_scheduler.py:155–193` | ✅ TTL + 64/256 caps |
| 15 | Tick-screener + DQ registry | `tick_screener.py:26–53`, `data_quality/registry.py:18–48` | ✅ 6h idle / 500 symbols |
| 16 | Event-bus handler bound | `agent_event_bus.py:77–98` | ✅ semaphore 32 |
| 18 | Non-virtualized long lists | Optimizer / History / News / Journal / BotDetail | ✅ `useVirtualRows` |
| 19 | Watchlist avg-vol cache | `WatchlistWidget.jsx:70–94` | ✅ revision-keyed, 200 cap |
| 20 | `setBotLogs` cap | `useStore.js:432–440` | ✅ 100 |
| 21 | TickViewer narrow selector | `TickViewerTab.jsx:27–30` | ✅ |
| 22 | HT CompactBarSeries | `candleBuffer.js:208–216` | ✅ `storeHtBars` |
| 32 | `manager._recent_bar_volumes` key eviction | `manager.py:828–831` | ⬜ values capped 50; keys never evicted — evict with symbol interest |
| 33 | Alpaca per-symbol maps | `alpaca_feed.py:112–118` | ⬜ `_sealed_bar_ts` / `_last_quote_apply_ts` / `_crypto_last_trade_event_ts` grow per symbol ever touched; `unsubscribe` is a no-op — evict on unsubscribe |
| 34 | Feature-drift buffer keys | `ml_feature_drift.py:150–218` | ⬜ windows capped 500; symbol×strategy keys unbounded — evict idle keys |
| 35 | HA in-place vs candle-slice split | `candleTransforms.js:194–199` vs `chartHelpers.js:410–418` | ⬜ HA mutates slot in place (may skip ECharts redraw) while candle path clones — align strategies when #4 lands |
| 37 | `custom_loader` ThreadPoolExecutor | `custom_loader.py:25` | ⬜ lifetime pool, never shut down — acceptable; document |
| 43 | Fire-and-forget train/validate/sweep tasks | `api/http/app.py:717,1282,1919` | ⬜ no global semaphore; job-store + pool=1 mitigate — add concurrency guard for in-process path |

## 🟢 P3 — Strategic — **shipped (re-verified)**

### 23. Display conflation — shipped ✅
`lib/chart/conflateBars.js` + `ChartWidget.jsx:750–755,1801–1810` — power-of-2 merge beyond ~1 bar/px; live ticks throttle via configure when factor > 1.

### 24. Compute workers — shipped ✅
`workers/backtestSlim.worker.js` + `lib/backtestSlimAsync.js` — WS/job-poll trimming off the main thread.

### 25. Byte-budgeted accounting — shipped ✅
Settings → Memory (`SettingsPanel.jsx:1301`, `MemoryObservabilitySection.jsx:102`) shows KB estimates + ECharts instance count + `memory_subsystems` from `/health/live`.

### 26. Memory-pressure ladder — shipped ✅
`memoryGuard.js` + `memoryPressureSignals.js:46–65` — warn: DPR 1.0, scanner pause, ≤2 panes; critical: HT prune + research offload. Consumers verified in Scanner/MultiChart/echartsInit.

### 10. DPR cap — shipped ✅
`lib/echartsInit.js:16–35` — `Math.min(dpr, 1.5)`, multi-pane → 1, pressure → 1; all charts via `initEcharts`.

### 5. MiniChart incremental — partial
`patchLastDisplayBucket` ✅ (last OHLC mutated in place); main-slot slice still open → tracked as #36.

### 6. HA/Renko incremental — shipped ✅
`ChartWidget.jsx:1846–1870` + `candleTransforms.js:179–203` — `patchLastTransformedMain` for forming bar; full rebuild only on new bar / patch failure.

---

## Priority & effort matrix

| Priority | Item | Effort | RAM effect |
|----------|------|--------|-----------|
| **P0** | #4 In-place live series cache (**reopened**) | Small | Biggest steady GC cut, N charts |
| **P0** | #28 Phase signal caches → `bind_dict_cache` | Small | Stops cache-only-grows pattern ×4 |
| **P0** | #36 MiniChart main-slot patch | Small | 6× grid churn eliminated |
| **P1** | #29 Reject-log retention | Small | Disk growth + query speed |
| **P1** | #38 tickerData/tickData LRU | Small | Long-session heap plateau |
| **P1** | #39 insight-history replace cap | Small | Closes cap bypass |
| **P1** | #40 statusCache LRU/TTL | Small | ML-Lab session leak |
| **P1** | #9/27/41 Deep-train default → process pool | Small (config) | Largest backend spikes leave live process |
| **P1** | #31 Slim terminal job results | Small | Job-store RSS |
| **P1** | #17 IDB key-only prune | Small | Prune transient spike |
| **P1** | #30 ml_train_runs retention | Small | Disk |
| **P2** | #8 enforce streaming portfolio path | Small | Portfolio spike |
| **P2** | #32–34 idle-key evictions | Small each | Long-session creep |
| **P2** | #35 HA/candle consistency | Small | Correctness + GC |
| **P2** | #40b/42 store caps (botHistory, analytics, snapshot) | Small each | Cold-path heap |
| **P2** | #43 in-process task concurrency guard | Small | Spike overlap |

### Suggested sequence (corrected)

1. **Week 1 (P0):** #4 (reopened — land the in-place cache + update pinning test), #28 (four caches → `bind_dict_cache`), #36.
2. **Week 2 (P1):** #29, #38, #39, #40, #31, #17, #30.
3. **Week 3 (P1/P2):** flip deep-train default to process pool (#9/27/41), #8, #32–34, #35, #40b/42, #43.
4. **Already shipped (no action):** #1–3, #5(partial)/#6, #10, #12–16, #18–22, #23–27.

Every item above preserves current latency/throughput; most reduce CPU alongside RAM (fewer clones, fewer pixels, less GC). None touches trading logic, risk gates, or the OMS.
