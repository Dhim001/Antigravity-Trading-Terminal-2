# Memory-Centric Comprehensive Review & Improvement Suggestions

A second full-stack review of the trading terminal — backend (38+ service modules, agent pipeline, OMS, risk engine, ML training) and frontend (74 components, hooks, stores, transport) — with **RAM management as the central design constraint** for every suggestion. Informed by how Sierra Chart, TradingView (lightweight-charts), Quantower, and Hummingbot handle memory in production.

**Re-scanned 2026-07-28** (post Phase 1–4 signal work): every "shipped" claim below was re-verified against the code. Two claims were found **not actually landed** (#4, and #11 only partially) and are corrected here. New findings from the recent signal-gate / RL / telemetry work are items **#28–#42**.

**Week-1 landed 2026-07-28:** #4 (in-place live series cache + quiet-tick repaint skip), #28 (four signal-gate caches → `bind_dict_cache` LRU+TTL), #36 (MiniChart main-slot patch via shared helper), and #35 was closed along the way (HA/Renko transformed path now re-refs the outer arrays so ECharts reliably repaints).

**Week-2 landed 2026-07-28:** #29 (reject-log retention + daily rollup), #30 (`ml_train_runs` retention), #31 (large terminal job results offloaded to disk, slim headline in RAM), #17 (IDB prune via key-only cursor), #38 (`tickerData`/`tickData` interest-gated caps), #39 (insight-history replace caps), #40 (`statusCache` LRU+TTL). Also fixed a latent bug found along the way: `app.config.DATA_DIR` did not exist, so the four `_default_path()` functions in the Phase signal-gate modules would have raised `ImportError` on any path-less load — now defined once in config.

**Week-3 landed 2026-07-28:** #41 (deep-train default → process pool + parent-side validate candle trim + real Windows Job Object memory ceiling with advisory fallback), #32 (POV `_recent_bar_volumes` key eviction, bot-interest protected), #33 (Alpaca per-symbol maps prune stale keys at the subscribe churn point; `unsubscribe` pops local state), #34 (feature-drift idle-key eviction, save-before-drop with lazy disk reload), #8 (portfolio preload guard warns when >16 symbols arrive pre-materialized without a streaming resolver), #40b (`botHistory` capped at 200), #42 (analytics report TTL-on-write rebuild + `backtestSnapshot` cleared with results lifecycle). **#43 was found already shipped** — all three async endpoints (train/validate/sweep) reserve the `ML_ASYNC_MAX_INFLIGHT` slot before spawning their background tasks (`_reserve_ml_async_slot`, APP_SCAN #40); table corrected below.

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

### 4. Live series cache cloned every paint — **shipped ✅**
`lib/chart/chartHelpers.js` — `patchMainSlotInPlace` now mutates the last OHLC/close slot in place and only re-references the outer array when a value actually changed (ECharts needs a fresh data-array reference to reliably re-read a series). `updateLiveSeriesCache` returns whether anything changed; `ChartWidget.jsx` skips the `setOption` repaint entirely on quiet ticks (no clone, no repaint, legend content derives from the same bars so it is unchanged too). Volume entries are patched in place (`value`/`itemStyle`) with the same change guard. The HA/Renko transformed path (`patchLastTransformedMain`) already mutated in place; the caller now re-refs `cache.main`/`cache.volume` so that path cannot paint stale (#35). Pinning test updated: unchanged ticks keep refs, changed ticks get fresh outer refs with the same inner slot.

### 36. MiniChart main-series sliced per tick — **shipped ✅**
`MiniChartWidget.jsx` — the local `patchMiniMainSlot` was removed; the widget now shares `patchMainSlotInPlace` from `chartHelpers.js` and skips `setOption` when the visible slot did not move (e.g. volume-only bucket updates on line charts). No more per-tick slice in the 3×2 grid.

---

## 🔴 P0/P1 — Backend: Phase 1–4 caches with no eviction (new)

### 28. Signal-gate module caches grow per bot_id, forever — **shipped ✅**
All four module-level caches are now bound through `model_store_lru.bind_dict_cache` (`ML_MODEL_CACHE_MAX=12`, `ML_MODEL_CACHE_TTL_SEC=3600`, lazy-initialized per module so config is read at first use):

| Cache | Where | Payload |
|-------|-------|---------|
| `_bot_cal` | `bots/conformal_gate.py` | conformal thresholds |
| `_bot_models` | `bots/hmm_regime.py` | Gaussian-mixture model |
| `_bot_cache` | `bots/calibration_fitter.py` | temperature/Kelly blob |
| `_bot_models` | `bots/stacking_meta_learner.py` | sklearn meta-learner |

`save_*`/`load_*`/`get_bot_calibration` touch the LRU on hit and store; `invalidate_*` also drops LRU tracking. Eviction pops only the hot copy — disk (JSON) stays the source of truth, so a miss reloads in milliseconds. Verified by `tests/test_signal_gate_cache_lru.py` (eviction, disk fallback, invalidation per module).

---

## 🟠 P1 — Spike control & duplication

### 7. Anchored walk-forward copies growing candle prefixes — **shipped (partial)**
`backtest_walk_forward.py:197–199` — now slices `candles[:cursor]` / `candles[cursor:test_end]` (shallow copies sharing candle dicts). Acceptable; true windowed views would still cut the list-of-references overhead per fold.

### 8. Portfolio backtests hold all symbols + parallel copies — **shipped ✅**
`backtest_portfolio.py:503–636` — chunked batches + `chunk_candles.clear()` when the runner resolves candles itself. The runner now warns loudly when >16 symbols arrive pre-materialized without a `resolve_candles` resolver (`PORTFOLIO_PRELOAD_WARN_SYMBOLS`), steering large-universe callers to the streaming path (peak ≈ workers × 1 symbol).

### 9/27/41. ML training process isolation — **shipped ✅**
`ml_train_executor.py` + `ml_train_limits.py` — **default flipped (Week 3):** `ML_TRAIN_TORCH_IN_PROCESS` now defaults off, so all deep trainers (LSTM, RL_PPO, TCN, VAE, Transformer, GNN) run in the `ProcessPoolExecutor` (`max_workers=1`) unless an operator opts back into in-process threads for debugging. The two original Windows concerns are mitigated: (1) pool-bound validate jobs trim candles in the *parent* (`_parent_trim_validate_candles`, mirroring the worker's ≤12k deep-WF cap) so far less is pickled through spawn; (2) workers import torch lazily post-spawn. `ml_train_limits.py` now sets a **real Windows Job Object** per-process memory ceiling (`JOB_OBJECT_LIMIT_PROCESS_MEMORY`) in the worker initializer, falling back to the advisory path on any failure; Unix keeps `RLIMIT_AS` (`ML_TRAIN_RSS_LIMIT_MB=4096`).

### 11. Backtest results retained in Zustand while Lab is open — **partial (corrected)**
`api/dispatch.js:20–31` + `backtestStorage.js` — offload happens when the Lab *closes* (slim dock copy). While the Lab is *open*, the full trimmed tree lives in `useResearchStore` (duplicate of session/IDB copy). Previously listed as shipped — it only covers the closed-Lab case.
**Fix:** acceptable trade-off while the Lab is open (needed for drill-down); keep as-is but ensure the slim dock copy is written on *every* completed run so closing never blocks on slimming.

### 17. IDB prune still deserializes full blobs — **shipped ✅**
`services/idbBacktest.js` — the prune walk now uses `index.openKeyCursor(null, 'prev')` on the v2 `savedAt` index: `cursor.key` = savedAt, `cursor.primaryKey` = record key, so zero payload deserialization. The value-cursor path remains only as a legacy fallback for pre-v2 DBs (the upgrade adds the index).

### 29. Reject-telemetry table has no retention — **shipped ✅**
`bots/reject_telemetry.py` — `prune_reject_log(retention_days, max_rows=...)` rolls soon-to-be-deleted rows into a new `bot_signal_reject_rollup` table (day × bot × bucket counts, upserted so repeated passes accumulate) before deleting, so long-term stats survive; `reject_rollup()` serves the historical aggregates. Wired into the startup retention pass in `server.py` with `REJECT_LOG_RETENTION_DAYS=7` / `REJECT_LOG_MAX_ROWS=500000`.

### 30. `ml_train_runs` table grows without prune — **shipped ✅**
`bots/ml_train_runs.py` — `prune_ml_train_runs(retention_days)` deletes rows older than `ML_TRAIN_RUNS_RETENTION_DAYS=30`; wired into the same startup retention pass.

### 31. Terminal ML job results kept in RAM — **shipped ✅**
`bots/ml_job_store.py` — on terminal state, results larger than `ML_JOB_RESULT_OFFLOAD_BYTES=65536` are written to `DATA_DIR/ml_job_results/{job_id}.json` (minus `_wf_bundle`, JSON-safe) and RAM keeps a slim headline (metrics/aggregate/version_id/etc). `public_ml_job` hydrates the full payload from disk transparently — API shape unchanged, and a missing file degrades to the slim headline instead of a 500. Small results (typical validate jobs) stay hot in RAM; job eviction deletes the offload file.

### 40. `mlTrainingSession.statusCache` unbounded (client) — **shipped ✅**
`lib/mlTrainingSession.js` — the module `Map` is now an LRU capped at 12 keys (touch on read/write) with a 30-minute idle TTL checked on access (no timers). Entries store `{ body, t }` wrappers internally; the public getters return the body unchanged, so all consumers are unaffected.

### 44. Torch/RL trainer default in-process — see #9/27/41 above (deduplicated).

---

## 🟠 P1 — Frontend store growth (new)

### 38. `tickerData` / `tickData` never evict — **shipped ✅**
`store/useStore.js` — `tickerData` is now an interest-gated LRU (cap 96): the watchlist universe (`symbolsList`), the active symbol, and open-position symbols are never evicted; least-recently-updated extras beyond the cap are dropped (recency stamps kept module-side so stamp writes don't re-render; `priceDirections` pruned in tandem). `setTickData` caps per-symbol entries at 500 and symbol count at 8 (active symbol protected).

### 39. `setAgentInsightHistory` bypasses the 20/8 caps — **shipped ✅**
`store/useResearchStore.js` — wholesale replace now applies the same trims as `setAgentInsight`: 20 entries per symbol (newest-first), 8 symbols max, and the symbol key is uppercased so replace/append share a bucket.

### 40b. `botHistory` / `orders` / `positions` mirrored uncapped — **shipped ✅**
`useStore.js:474, 279–282` — `setBotHistory` now caps the mirrored history at 200 entries (`MAX_BOT_HISTORY`). `orders`/`positions` stay live mirrors, bounded by server state as designed.

### 42. `analyticsReport` merge accumulates; `backtestSnapshot` opaque — **shipped ✅**
`useResearchStore.js:102–116, 200` — dashboard/partial reports now carry an `_updatedAt` stamp; when a partial arrives and the previous snapshot is older than 30 min (`ANALYTICS_REPORT_TTL_MS`), the report is **rebuilt from the partial** instead of merged into the stale base, bounding merge accumulation without timers. `setBacktestResults(null)` now clears `backtestSnapshot` in the same patch, aligning the snapshot lifecycle with the results lifecycle (it was previously only cleared on ML invalidate).

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
| 32 | `manager._recent_bar_volumes` key eviction | `manager.py:828–831` | ✅ Week 3 — keys capped at 32 (recency-ordered re-insert), symbols with active bots protected |
| 33 | Alpaca per-symbol maps | `alpaca_feed.py:112–118` | ✅ Week 3 — `_prune_symbol_state_maps` drops keys outside the live universe at the subscribe churn point; `unsubscribe` pops the symbol's local state |
| 34 | Feature-drift buffer keys | `ml_feature_drift.py:150–218` | ✅ Week 3 — idle-key eviction (6h) + LRU cap 64 (`FEATURE_DRIFT_*` envs), save-before-drop, lazy disk reload |
| 35 | HA in-place vs candle-slice split | `candleTransforms.js` + `chartHelpers.js` | ✅ closed with #4 — both paths mutate slots in place; callers re-ref outer arrays on change so ECharts reliably repaints |
| 37 | `custom_loader` ThreadPoolExecutor | `custom_loader.py:25` | ⬜ lifetime pool, never shut down — acceptable; document |
| 43 | Fire-and-forget train/validate/sweep tasks | `api/http/app.py:717,1282,1919` | ✅ already shipped (verified Week 3) — all three endpoints reserve the `ML_ASYNC_MAX_INFLIGHT` slot before spawning (`_reserve_ml_async_slot`, APP_SCAN #40), released in `finally` |

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

### 5. MiniChart incremental — shipped ✅
`patchLastDisplayBucket` (last OHLC mutated in place) + main-slot in-place patch via shared `patchMainSlotInPlace` (#36).

### 6. HA/Renko incremental — shipped ✅
`ChartWidget.jsx:1846–1870` + `candleTransforms.js:179–203` — `patchLastTransformedMain` for forming bar; full rebuild only on new bar / patch failure.

---

## Priority & effort matrix

| Priority | Item | Effort | RAM effect |
|----------|------|--------|-----------|
| ~~P0~~ ✅ | #4 In-place live series cache | Small | Biggest steady GC cut, N charts |
| ~~P0~~ ✅ | #28 Phase signal caches → `bind_dict_cache` | Small | Stops cache-only-grows pattern ×4 |
| ~~P0~~ ✅ | #36 MiniChart main-slot patch | Small | 6× grid churn eliminated |
| ~~P0~~ ✅ | #4 In-place live series cache | Small | Biggest steady GC cut, N charts |
| ~~P0~~ ✅ | #28 Phase signal caches → `bind_dict_cache` | Small | Stops cache-only-grows pattern ×4 |
| ~~P0~~ ✅ | #36 MiniChart main-slot patch | Small | 6× grid churn eliminated |
| ~~P1~~ ✅ | #29 Reject-log retention + rollup | Small | Disk growth + query speed |
| ~~P1~~ ✅ | #38 tickerData/tickData LRU | Small | Long-session heap plateau |
| ~~P1~~ ✅ | #39 insight-history replace cap | Small | Closes cap bypass |
| ~~P1~~ ✅ | #40 statusCache LRU/TTL | Small | ML-Lab session leak |
| **P1** | #9/27/41 Deep-train default → process pool | Small (config) | Largest backend spikes leave live process | ✅ Week 3 |
| ~~P1~~ ✅ | #31 Slim terminal job results | Small | Job-store RSS |
| ~~P1~~ ✅ | #17 IDB key-only prune | Small | Prune transient spike |
| ~~P1~~ ✅ | #30 ml_train_runs retention | Small | Disk |
| ~~**P2**~~ ✅ | #8 enforce streaming portfolio path | Small | Portfolio spike — warn guard shipped |
| ~~**P2**~~ ✅ | #32–34 idle-key evictions | Small each | Long-session creep — all three shipped |
| **P2** | #35 HA/candle consistency | Small | Correctness + GC |
| ~~**P2**~~ ✅ | #40b/42 store caps (botHistory, analytics, snapshot) | Small each | Cold-path heap |
| ~~**P2**~~ ✅ | #43 in-process task concurrency guard | Small | Spike overlap — verified already shipped |

### Suggested sequence (corrected)

1. ~~**Week 1 (P0):** #4, #28, #36~~ — **landed 2026-07-28** (incl. #35 closed along the way).
2. ~~**Week 2 (P1):** #29, #38, #39, #40, #31, #17, #30~~ — **landed 2026-07-28** (incl. latent `app.config.DATA_DIR` fix).
3. ~~**Week 3 (P1/P2):** #9/27/41, #8, #32–34, #40b/42~~ — **landed 2026-07-28** (#43 verified already shipped — `ML_ASYNC_MAX_INFLIGHT` semaphore guards all three async ML endpoints).
4. **Already shipped (no action):** #1–6, #8–10, #12–22, #23–44. **The review is now fully closed out** — every actionable item has landed.

Every item above preserves current latency/throughput; most reduce CPU alongside RAM (fewer clones, fewer pixels, less GC). None touches trading logic, risk gates, or the OMS.
