# Memory-Centric Comprehensive Review & Improvement Suggestions

A second full-stack review of the trading terminal — backend (38+ service modules, agent pipeline, OMS, risk engine, ML training) and frontend (74 components, hooks, stores, transport) — with **RAM management as the central design constraint** for every suggestion. Informed by how Sierra Chart, TradingView (lightweight-charts), Quantower, and Hummingbot handle memory in production.

Companion docs: `MEMORY_16GB.md` (current budget inventory), `REVIEW_SUGGESTIONS.md` (previous general review), `CODE_AUDIT_REPORT.md`.

---

## Lessons from the reference platforms

| Platform | Memory principle | Applies here as |
|----------|------------------|-----------------|
| **Sierra Chart** | RAM scales strictly with *days-to-load* per chart; the user chooses the storage time unit (tick / 1s / 1m). Memory is a visible, user-controlled budget. | Extend `memoryBudget.js`-style explicit budgets to backend subsystems (ML stores, screener cache) with byte-bounded caps, surfaced in Settings → Memory. |
| **TradingView** (lightweight-charts 5.1) | *Data conflation*: points that would occupy <0.5 px when zoomed out are merged, so render memory scales with **pixels, not bars**. | Display-level downsampling for ECharts when zoomed out past ~1 bar/px (we already cap display bars; conflation is the next step). |
| **Hummingbot** | Biggest RAM win was **headless mode** (~40% reduction — UI out of the bot process); v2.7 fixed websocket-connection and orphaned-async-task leaks. | Process separation for ML training/sweeps (spiky work out of the always-on feed/OMS process); audit `create_task` fan-out. |
| **Quantower / Sierra** | Stability under load beats visual richness; low steady footprint with many charts. | Multi-chart grid should degrade gracefully (fewer live canvases, lower DPR) rather than crash. |

---

## Already in place (do not re-solve)

- **Client:** candle LRU (4 symbols) + bar caps, `memoryGuard` heap-pressure trimming, backtest IndexedDB offload + payload slimming, RAF-coalesced market updates, FlexLayout heavy-tab unmount (`MountWhenVisible`), multi-chart maximize unmount, overlay trade cap, capped store lists.
- **Backend:** HT cache LRU+TTL (`MASSIVE_HT_*`), live 1m buffers capped (1500/symbol), screener LRU, backtester DF cache (TTL, max 10, cleared post-sweep), agent insight/event history caps, archive query LIMIT + fetchmany, WAL + checkpoint, retention pruning, sweep trial budgets, wire payload trims, meta-label WF session models freed in `finally`.

---

## 🔴 P0 — Unbounded growth & leak-on-failure (backend)

### 1. Archive write buffer grows without bound on flush failure
`archive/writer.py:54–107` — `_buffer` has no max size, and on a failed SQLite flush the rows are written to the file-WAL **but `_buffer` is not cleared**. A locked/failing DB turns the buffer into a runaway list while the feed keeps appending.
**Fix:** clear `_buffer` after successful `append_wal_rows`; add a hard cap (drop-oldest + counter metric) as a second line of defense. Small change, removes the single worst "quiet OOM" path in the always-on process.

### 2. ML model stores never evict
`strategies_ml.py` (`MlSignalModelStore`), `strategies_lstm.py` (`LstmModelStore`), and the TCN/Transformer/VAE/GNN/PPO trainer stores all keep every per-symbol|version model/ONNX `InferenceSession` resident forever; `meta_label_model.py:450–523` grows per bot. Predict across many symbols × strategies over a long session and RSS ratchets up permanently.
**Fix:** one shared `ModelStoreLRU` (cap ~8–12 entries or byte-bounded, TTL for idle models). Reload from disk on miss is milliseconds for joblib/ONNX — no live-path performance cost. Also explicitly unload models loaded only for a backtest when the run ends.

### 3. Copilot session memory uncapped
`copilot.py:101,400–412` — `_SESSION_MEMORY` accumulates per `session_id` + per-symbol insights with no TTL/cap (pending actions are TTL'd; this map is not).
**Fix:** TTL (e.g. 2h idle) + max-sessions cap, mirroring the pending-actions pattern.

---

## 🔴 P0 — Steady-state heap churn (frontend)

### 4. Live series cache cloned every paint
`lib/chart/chartHelpers.js:403–417` — the forming-bar path does `cache.main.slice()` + `cache.volume.slice()` on every live paint (~4×/s at the 250 ms floor). ~600 display bars ⇒ thousands of array slots cloned per second, per chart, before ECharts copies again.
**Fix:** mutate `cache.main[idx]`/`cache.volume[idx]` in place; only rebuild on length/chart-type change. Same array ref into `setOption` (ECharts diffs by series id). Less GC *and* less CPU.

### 5. MiniChart re-aggregates the full series per tick
`MiniChartWidget.jsx:340–346, 483–485` — every live paint re-runs `bucketCandles` over the 1m slice and remaps the whole series. In the 3×2 grid that is 6 full re-aggregations ~4×/s.
**Fix:** keep a `displayBarsRef` like `ChartWidget`; patch the last OHLC tuple on forming-bar updates; re-bucket only on new bucket / symbol / TF change.

### 6. Heikin-Ashi / Renko fall back to full rebuild per tick
`ChartWidget.jsx:1765–1767` — these chart types skip the light patch and run full `configureChart` (`notMerge`) on every live tick.
**Fix:** incremental HA close computation for the forming bar; full rebuild only on new bar.

---

## 🟠 P1 — Spike control & duplication

### 7. Anchored walk-forward copies growing candle prefixes
`backtest_walk_forward.py:197–198` — `list(candles[:cursor])` materializes a copy per fold; worst case ~5 folds × 50k bars. Rolling folds already share slices.
**Fix:** pass index ranges into one shared list (the backtester already accepts windowed views elsewhere). Cuts anchored-WF peak by roughly the sum of fold prefixes.

### 8. Portfolio backtests hold all symbols + parallel copies
`backtest_portfolio.py:491–548` — `candles_by_symbol` for every symbol resident at once, plus up to `BACKTEST_PARALLEL_WORKERS` (4) concurrent runs each with indicator DataFrames. Worst case: hundreds of MB to 1+ GB spike.
**Fix:** stream per-symbol — load → run → keep only the result row + equity samples → release candles/DF before the next symbol batch. Parallelism stays; peak drops to ~`workers × 1 symbol` instead of `all symbols + workers`.

### 9. ML training spikes share the live process
`ml_lstm_trainer.py:154–257` and peers build full `(N × lookback × features)` tensors plus torch + ONNX export peaks in the same process as feed/OMS/WS. This is the Hummingbot lesson in reverse: spiky work should not share the always-on process's headroom.
**Fix (incremental):** run train/validate jobs via `ProcessPoolExecutor`/subprocess with a max-1-concurrent budget; the job API already exists, so this is a transport change, not a redesign. Spike memory is returned to the OS on process exit — CPython rarely returns arena memory otherwise.

### 10. ECharts canvases at full devicePixelRatio
Every `echarts.init` uses default DPR; on 2× displays each canvas backing store is 4× pixels; multi-chart grid can hold 6 live canvases.
**Fix:** `devicePixelRatio: Math.min(window.devicePixelRatio || 1, 1.5)` (1.0 for multi-grid cells). Large canvas-RAM win, negligible visual cost at terminal densities, less pixel fill (CPU win).

### 11. Backtest results retained in Zustand while Lab is closed
`dispatch.js:176–180` — the full trimmed result tree stays in `useResearchStore` until the Lab closes (offload currently happens on close). While the dock preview is showing, the store holds a duplicate of the session/IDB copy.
**Fix:** offload immediately after `saveFullBacktestResults` — set the store to `slimBacktestForDock`; the Lab already restores from session/IDB on open. Restore path exists; zero UX change.

### 12. SQLite page cache sized statically at 64 MB
`db/connection.py:64` — `PRAGMA cache_size = -64000` on every connection in every profile.
**Fix:** make it env-tunable (`SQLITE_CACHE_KB`), default 64 MB for Massive-only, 32 MB when multiple profiles run. Aligns with the "one profile on 16 GB" guidance.

---

## 🟡 P2 — Bounded-but-large / hygiene — **shipped**

| # | Item | Where | Fix |
|---|------|-------|-----|
| 12 | SQLite page cache static 64 MB | `db/connection.py` | `SQLITE_CACHE_KB` env (default 64000) |
| 13 | Screener LRU cap is entries (1000 DataFrames), not bytes | `screener.py` | Entry cap ~200 + `SCREENER_CACHE_MAX_MB` |
| 14 | Retrain `_pending` / `_last_retrain` grow per symbol×strategy | `ml_retrain_scheduler.py` | TTL stale pending entries; cap map size |
| 15 | Tick-screener symbol map & data-quality registry unbounded (small values) | `tick_screener.py`, `data_quality/registry.py` | Evict symbols not seen for N hours |
| 16 | Event-bus `create_task` per handler with no bound | `agent_event_bus.py` | Semaphore (`AGENT_EVENT_HANDLER_CONCURRENCY`) |
| 17 | IDB prune loads all blobs via `getAll()` | `idbBacktest.js` | Cursor over keys + `savedAt` index |
| 18 | Non-virtualized long lists | Optimizer / History / News / Journal / BotDetail | `useVirtualRows` / `VirtualScrollList` |
| 19 | Watchlist volume sort scans full candle buffers per ticker change | `WatchlistWidget.jsx` | Cache avg volume per symbol keyed by candle revision |
| 20 | `setBotLogs` uncapped (only `addBotLog` caps at 100) | `useStore.js` | Same 100 cap (Week 1) |
| 21 | TickViewer subscribes to whole `tickData` map | `TickViewerTab.jsx` | Narrow selector per active symbol |
| 22 | HT buffers stored as object[] not CompactBarSeries | `candleBuffer.js` | `storeHtBars` → CompactBarSeries |

---

## 🟢 P3 — Strategic (memory-first architecture) — **shipped (MVP)**

### 23. Display conflation (TradingView pattern) — shipped
`lib/chart/conflateBars.js` + `ChartWidget` configure path: when zoomed out beyond ~1 bar/px, merge OHLC power-of-2 before `setOption`. Cache by zoom bucket. Live ticks throttle via configure when factor > 1.

### 24. Compute workers for aggregation & slimming — shipped (backtestSlim)
`workers/backtestSlim.worker.js` + `backtestSlimAsync.js`. WS/`dispatch` and job poll trim off the main thread (sync fallback).

### 25. Byte-budgeted subsystem accounting — shipped
Client: Settings → Memory shows candle/research KB estimates + ECharts instance count + pressure ladder. Backend: `memory_subsystems` on `/health` + `/health/live` (ML store counts, screener cache MB).

### 26. Memory-pressure degradation ladder — shipped
`memoryPressureSignals` + `memoryGuard`: warn → DPR 1.0, pause scanner auto-refresh, multi-chart ≤2 panes; critical → HT prune for non-active + force research offload.

### 27. Training process isolation RSS ceilings — shipped (extends #9)
`ML_TRAIN_RSS_LIMIT_MB` (default 2048) applied via `ProcessPoolExecutor` initializer (`RLIMIT_AS` on Unix; advisory on Windows). Train/validate already process-isolated.

---

## Priority & effort matrix

| Priority | Item | Effort | RAM effect |
|----------|------|--------|-----------|
| **P0** | #1 Archive buffer clear-on-WAL + cap | Small | Removes worst backend leak-on-failure |
| **P0** | #2 ML model store LRU/TTL | Small–Med | Stops permanent RSS ratchet |
| **P0** | #4 In-place live series cache | Small | Biggest steady GC cut, N charts |
| **P0** | #5 MiniChart incremental patch | Medium | 6× grid churn eliminated |
| **P0** | #3 Copilot session TTL | Small | Closes slow leak |
| **P1** | #10 DPR cap | Small | 30–75% canvas RAM on retina |
| **P1** | #11 Immediate backtest offload | Small | Frees large tree while Lab closed |
| **P1** | #7 Anchored WF shared views | Small | Cuts WF spike ~2–5× fold prefix |
| **P1** | #8 Portfolio streaming | Medium | Spike ∝ workers, not symbols |
| **P1** | #9 Training subprocess | Medium | Spike memory returned to OS |
| **P1** | #6 HA/Renko incremental | Medium | Per-tick rebuild removed |
| **P2** | #12–22 hygiene batch — **shipped** | Small each | Cumulative; prevents regressions |
| **P3** | #23–27 — **shipped (MVP)** | Med–Large | Viewport RAM, worker trim, ladder, RSS caps |

### Suggested sequence
1. **Week 1 (P0) — shipped:** #1, #3, #4, #10, #11, #20 — archive WAL clear+cap, copilot session TTL, in-place live series cache, DPR cap, immediate backtest offload, `setBotLogs` cap.
2. **Week 2 (P0/P1) — shipped:** #2, #5, #7 — ML model store LRU/TTL, MiniChart last-bucket patch, anchored WF shared slice views.
3. **Week 3 (P1) — shipped:** #8, #9, #6 — portfolio candle streaming, ML train process pool, HA/Renko forming-bar patch.
4. **P2 hygiene (#12–#22) — shipped:** SQLite `SQLITE_CACHE_KB`; screener entry+MB LRU; retrain map TTL/cap; tick-screener + DQ idle eviction; event-bus handler semaphore; IDB prune without blob `getAll`; virtualized OptimizationHistory / News / Journal / TaOptimizer leaderboard / BotDetail trades; watchlist avg-vol by revision; `setBotLogs` cap (Week 1); TickViewer narrow selector; HT `CompactBarSeries`.
5. **P3 (#23–#27) — shipped (MVP):** display conflation; backtestSlim worker; subsystem accounting UI + `/health`; memoryGuard ladder (DPR/scanner/multi-chart/HT/offload); `ML_TRAIN_RSS_LIMIT_MB` on train pool.
6. **Follow-ups:** Optuna/sweep process isolation; Job Object hard ceilings on Windows; bucketCandles worker; display conflation for MiniChart.

Every item above preserves current latency/throughput; most reduce CPU alongside RAM (fewer clones, fewer pixels, less GC). None touches trading logic, risk gates, or the OMS.
