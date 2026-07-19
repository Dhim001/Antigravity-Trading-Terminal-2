# Memory budget — 16 GB workstation guide

This terminal runs **three optional profiles** (Sim, IB, Massive) that can each consume hundreds of MB to several GB. On a **16 GB RAM** machine, treat memory as a shared budget across browser tab, Python backends, and other apps.

## Recommended daily setup

| Choice | Why |
|--------|-----|
| **Run Massive only** (`.\scripts\start-massive.ps1`) | Live crypto/stocks + paper OMS; skip Sim/IB unless you need them |
| **One browser tab** on `http://127.0.0.1:5175` | Each tab holds its own candle LRU + ECharts GPU buffers |
| **Hard refresh** after long sessions | Clears stale Vite HMR state |
| **`-Restart` when ports are stuck** | Kills old Vite/Python on profile ports before relaunch |

```powershell
.\scripts\start-massive.ps1 -Restart
```

## Browser caps (client-side)

Defined in `frontend/src/services/memoryBudget.js`:

| Constant | Value | Role |
|----------|-------|------|
| `CANDLE_LRU_MAX_SYMBOLS` | 4 | Max watchlist symbols with warm 1m buffers |
| `CANDLE_BUFFER_MAX_BARS` | 3,000 | 1m bars per symbol in tab memory |
| `CANDLE_ARCHIVE_MAX_BARS` | 5,000 | Max after scroll-left archive prepend |
| `CHART_DISPLAY_BARS_DEFAULT` | 600 | Initial chart window |
| `CHART_DISPLAY_MAX_BARS` | 2,500 | Max rendered after repeated pan-left |
| `HT_BUFFER_MAX_BARS` | 600 | Native higher-TF buffer per symbol\|TF |

Active symbol is **pinned** — switching watchlist evicts the oldest non-active symbol when LRU is full.

## Observability

| Surface | What it shows |
|---------|----------------|
| **Dev badge** (bottom-right, DEV builds only) | JS heap %, buffer symbol/bar counts, WS clients, crypto lag |
| **Settings → System → Memory & buffers** | Full client stats + backend HT cache / lag (all builds) |
| **`GET /health`** | `ws_clients`, `massive.crypto_lag_sec`, `massive.ht_cache_entries` |

### Quick triage

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| Tab freezes / OOM | Browser heap + chart GPU | Close extra tabs; check dev badge heap %; reduce scroll-left history |
| Stale chart / wrong backend proxy | Old Vite on :5175 | `start-massive.ps1 -Restart` |
| `ws_clients = 0` with UI open | WS not connected | Hard refresh; confirm Vite maps 5175 → 8785 |
| Heap > 70% sustained | Too many symbols / long session | Switch away from unused symbols; refresh |
| Backend HTTP hangs / health timeouts | Full `/health` + SQLite archive COUNT under load | Prefer `/health/live`; Massive keeps `ARCHIVE_RETENTION_1M_DAYS=14` (90d backtests use broker REST / 1h archive, not local 1m) |

Launch scripts and Docker healthchecks use **`/health/live`**. UI pollers (dev badge, memory settings, IB banner) use live/massive probes — not full `/health`.

## Backend / multi-profile RAM

Each profile runs a separate Python process + SQLite DB:

| Profile | Ports | Typical use |
|---------|-------|-------------|
| Sim | 5173 / 8765–8766 | Paper sim generator |
| IB | 5174 / 8775–8776 | IB Gateway feed |
| Massive | 5175 / 8785–8786 | Live Massive feed + paper OMS |

Running **all three** can exceed 16 GB under load. Prefer **one profile** for live trading.

## Massive HT server cache

Higher-timeframe REST bars are cached server-side (`massive_feed._ht_cache`). Health exposes `massive.ht_cache_entries`, `ht_cache_max_entries`, and `ht_cache_ttl_sec`. Limits are configured via `MASSIVE_HT_LIMIT_*` env vars (see `env.profiles/massive.env` and `backend/app/services/massive_ht_limits.py`).

When running bots + multi-chart + analyst HT fetches on 16 GB:

| Env | Default | Role |
|-----|---------|------|
| `MASSIVE_HT_CACHE_TTL_SEC` | 300 | How long REST HT responses stay warm |
| `MASSIVE_HT_CACHE_MAX_ENTRIES` | 48 | LRU cap on symbol×TF pairs (~26 symbols × few TFs) |
| `MASSIVE_HT_LIMIT_ANALYSIS` | 2000 | Lower to 1200 if server RSS climbs |

Prometheus counters: `massive_ht_cache_hit_total`, `massive_ht_cache_miss_total`. Watch hit ratio in Settings → System → Metrics when switching symbols aggressively.

## List caps inventory (keep lists — do not generator-ize)

These surfaces intentionally stay **lists + hard caps**. Streaming was applied only where it cuts peak RAM without breaking lookbacks (Massive REST pages → resolve merge → chunked SQLite/footprint).

| Surface | Cap / knob | Notes |
|---------|------------|-------|
| Live candle buffers / HT cache | Client LRU + `MASSIVE_HT_*` | See tables above |
| FlexLayout heavy tabs | Unmount when deselected (`MountWhenVisible`) | Algo, ML Training, Scanner, Analyst, Equity, Ticks, Copilot |
| Multi-chart maximize | Non-focused panes unmount | Shared candle buffers refill on restore |
| Insights hub tabs | Active tab only (no `forceMount`) | Avoids duplicate Scanner/Analyst with dock |
| Chart backtest overlay | `MAX_OVERLAY_TRADES` (200) | Full trade list stays in Lab fetch, not overlay |
| Archive history (backtest / resolve) | `ARCHIVE_QUERY_LIMIT` (50 000) | Newest-N in window; `truncated` in meta |
| Archive history (chart WS pan) | `ARCHIVE_QUERY_LIMIT_UI` (10 000) | `purpose="ui"`; newest-N + truncation meta |
| Archive fetchmany batch | `ARCHIVE_QUERY_BATCH_SIZE` (2000) | Iterator page size |
| Tick archive reads | `ARCHIVE_TICK_QUERY_LIMIT` (10 000) | Newest-N; `truncated` on WS tick meta |
| Tick retention / flush | `ARCHIVE_TICK_RETENTION_HOURS`, `ARCHIVE_TICK_BATCH_MAX` | Short window |
| Footprint heatmap | `FOOTPRINT_MAX_RANGE_MS`, `FOOTPRINT_CHUNK_MS`, `FOOTPRINT_MAX_CELLS` | Clamp + chunk + cell cap |
| 1m / 1h retention | `ARCHIVE_RETENTION_1M_DAYS`, `ARCHIVE_RETENTION_1H_DAYS` | Massive profile often 14d 1m |
| Backtester / walk-forward | Lists + native TF + retention | **No** bar generators |
| Sweeps | `MAX_SWEEP_COMBOS` (24) / `MAX_SWEEP_COMBOS_EXTENDED` (100); env `BACKTEST_SWEEP_MAX_TRIALS` / `BACKTEST_SWEEP_MAX_GRID` | Trial budget |
| Backtest reasoning payload | `BACKTEST_REASONING_MAX_TRADES` (20) | LLM context size |
| Inline backtest gate | `BACKTEST_INLINE_MAX_SEC` (30) | Heavy jobs go async |
| WS candle snapshots | `MARKET_CANDLE_SNAPSHOT_LIMIT` / `MAX` | Tail of in-memory buffer |
| Small CRUD (bots, journal, workspaces) | Leave as lists | Tiny payloads |

### Archive / footprint env reference

| Env | Default | Role |
|-----|---------|------|
| `ARCHIVE_QUERY_BATCH_SIZE` | 2000 | `fetchmany` batch for bar iterators |
| `ARCHIVE_QUERY_LIMIT` | 50000 | Max bars per archive table range (backtest/default) |
| `ARCHIVE_QUERY_LIMIT_UI` | 10000 | Max bars for chart/WS history |
| `ARCHIVE_TICK_QUERY_LIMIT` | 10000 | Max ticks per query |
| `FOOTPRINT_MAX_RANGE_MS` | 86400000 (24h) | Wide requests clamp to newest window |
| `FOOTPRINT_CHUNK_MS` | 3600000 (1h) | Per-chunk GROUP BY for footprint |
| `FOOTPRINT_MAX_CELLS` | 50000 | Cap heatmap cells; later buckets omitted |

### Streaming already shipped (Phases 1–3)

| Phase | What | Still returns |
|-------|------|---------------|
| 1 | Massive REST aggs → candles page-wise | `list` helpers for callers that need lists |
| 2 | Resolve broker fill folds pages into one merge map | Final windowed `list[dict]` for backtester |
| 3 | Archive `fetchmany` + chunked footprint | Capped cell/`meta` JSON |

### Explicit non-goals

- Do **not** rewrite `backtester.py`, chart warm buffers, or walk-forward to generators.
- Do **not** `list(stream)` at the end of every hot path — that erases the Phase 1–2 win.
- Do **not** run pytest against profile DBs (`trading-massive.db`); isolate temp SQLite.

## Automated soak (optional)

```powershell
cd backend
python scripts/soak_ws_health.py --http http://127.0.0.1:8786 --ws ws://127.0.0.1:8785 --seconds 30
```

Expect `ws_clients >= 1` when the browser is open on the Massive UI.
